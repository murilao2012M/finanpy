"""Views do app financeiro."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import hmac
import json
import logging
import unicodedata
import uuid

from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView, PasswordResetConfirmView
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Count, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import (
    AnaliseFinanceiraIAForm,
    AlterarSenhaPerfilForm,
    CartaoCreditoForm,
    CategoriaForm,
    ConfiguracaoUsuarioForm,
    ConviteOrcamentoForm,
    ExcluirContaForm,
    FotoPerfilForm,
    InvestimentoForm,
    LancamentoForm,
    LoginUsuarioForm,
    MetaFinanceiraForm,
    MoedaPerfilForm,
    OrcamentoCompartilhadoForm,
    PerfilUsuarioForm,
    PlanoContencaoForm,
    RegistroUsuarioForm,
    SimuladorDecisaoForm,
)
from .ia_financeira import OBJETIVO_ANALISE_IA, REGRAS_ANALISE_IA, OpenAIFinanceiroErro, chamar_openai_analise_financeira
from .mercado_pago import MercadoPagoClient, MercadoPagoErro
from .models import (
    AnaliseFinanceiraIA,
    CartaoCredito,
    Categoria,
    ConfiguracaoUsuario,
    EventoAssinatura,
    Investimento,
    Lancamento,
    LimiteCategoriaContencao,
    MetaFinanceira,
    MembroOrcamento,
    Notificacao,
    OrcamentoCompartilhado,
    PlanoContencao,
    PlanoUsuario,
)


logger = logging.getLogger(__name__)

ABREVIACOES_MESES_PT = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

LIMITES_RECURSOS = {
    "cartoes": {
        "titulo": "cartões",
        "nome": "cartoes",
        "rota": "lista_cartoes",
        "mensagem": "O plano Freemium permite cadastrar apenas 1 cartão. Faça upgrade para o Premium e libere mais cartões.",
    },
    "metas": {
        "titulo": "metas",
        "nome": "metas",
        "rota": "lista_metas",
        "mensagem": "O plano Freemium permite cadastrar até 2 metas. Faça upgrade para o Premium e libere metas ilimitadas.",
    },
    "investimentos": {
        "titulo": "investimentos",
        "nome": "investimentos",
        "rota": "lista_investimentos",
        "mensagem": "O plano Freemium permite cadastrar apenas 1 investimento. O Premium libera carteira completa e IA.",
    },
}

RECURSOS_PREMIUM = {
    "ia": {
        "nome": "Análise Financeira com IA",
        "mensagem": "A Análise IA faz parte do Premium. Finalize o upgrade para liberar esse recurso.",
    },
    "simulador": {
        "nome": "Simulador de Decisões",
        "mensagem": "O Simulador de Decisões é um recurso Premium. Faça upgrade para liberar as simulações.",
    },
    "contencao": {
        "nome": "Modo Anti-descontrole",
        "mensagem": "O Modo Anti-descontrole é um recurso Premium. Faça upgrade para ativar um plano de contenção.",
    },
    "familia": {
        "nome": "Modo Casal/Família",
        "mensagem": "O Modo Casal/Família é um recurso Premium. Faça upgrade para compartilhar orçamentos.",
    },
}


def healthcheck(request):
    """Endpoint simples para monitoramento do backend em producao."""
    return JsonResponse({"status": "ok", "service": "FinanPy"})


def obter_mes_referencia(request):
    """
    Converte o parâmetro ?mes=YYYY-MM em datas reais.

    Exemplo:
    - 2026-05 vira 01/05/2026 até 31/05/2026.
    """
    mes_param = request.GET.get("mes")
    hoje = date.today()

    if mes_param:
        try:
            ano, mes = map(int, mes_param.split("-"))
            inicio = date(ano, mes, 1)
        except ValueError:
            inicio = date(hoje.year, hoje.month, 1)
    else:
        inicio = date(hoje.year, hoje.month, 1)

    ultimo_dia = monthrange(inicio.year, inicio.month)[1]
    fim = date(inicio.year, inicio.month, ultimo_dia)
    return inicio, fim


def somar_valores(queryset):
    """Retorna a soma do campo valor de forma segura."""
    return queryset.aggregate(total=Sum("valor"))["total"] or Decimal("0.00")


def somar_campo(queryset, campo):
    """Retorna a soma de qualquer campo numérico de forma segura."""
    return queryset.aggregate(total=Sum(campo))["total"] or Decimal("0.00")


def adicionar_meses(data_base, quantidade_meses):
    """Soma meses manualmente sem depender de bibliotecas externas."""
    mes_final = data_base.month - 1 + quantidade_meses
    ano = data_base.year + mes_final // 12
    mes = mes_final % 12 + 1
    dia = min(data_base.day, monthrange(ano, mes)[1])
    return date(ano, mes, dia)


def obter_objeto_do_usuario(modelo, usuario, pk):
    """Garante que o usuário só edite ou exclua seus próprios dados."""
    return get_object_or_404(modelo, pk=pk, usuario=usuario)


def obter_plano_usuario(usuario):
    """Retorna o plano do usuario, criando o freemium se ainda nao existir."""
    plano_usuario, _ = PlanoUsuario.objects.get_or_create(usuario=usuario)
    return plano_usuario


def criar_notificacao(usuario, titulo, mensagem, tipo=Notificacao.TIPO_INFO, link=""):
    """Registra uma notificação simples para aparecer no sininho do usuário."""
    if not usuario or not usuario.is_authenticated:
        return None

    return Notificacao.objects.create(
        usuario=usuario,
        titulo=titulo,
        mensagem=mensagem,
        tipo=tipo,
        link=link,
    )


def limpar_payload_auditavel(payload):
    """Garante que o payload salvo em auditoria seja JSON simples."""
    if not payload:
        return {}

    try:
        return json.loads(json.dumps(payload, default=str))
    except (TypeError, ValueError):
        return {"payload_texto": str(payload)}


def registrar_evento_assinatura(
    *,
    tipo,
    origem=EventoAssinatura.ORIGEM_SISTEMA,
    usuario=None,
    plano=None,
    mercado_pago_preapproval_id="",
    mercado_pago_evento_id="",
    mercado_pago_tipo="",
    mercado_pago_acao="",
    status_gateway="",
    referencia_externa="",
    valor=None,
    moeda="BRL",
    mensagem="",
    payload=None,
):
    """Cria uma trilha de auditoria para assinatura, checkout, webhook e bloqueios."""
    if plano and not usuario:
        usuario = plano.usuario

    try:
        valor_decimal = Decimal(str(valor if valor is not None else "0.00"))
    except Exception:
        valor_decimal = Decimal("0.00")

    return EventoAssinatura.objects.create(
        usuario=usuario,
        plano=plano,
        tipo=tipo,
        origem=origem,
        mercado_pago_preapproval_id=mercado_pago_preapproval_id or "",
        mercado_pago_evento_id=mercado_pago_evento_id or "",
        mercado_pago_tipo=mercado_pago_tipo or "",
        mercado_pago_acao=mercado_pago_acao or "",
        status_gateway=status_gateway or "",
        referencia_externa=referencia_externa or "",
        valor=valor_decimal,
        moeda=moeda or "BRL",
        mensagem=mensagem or "",
        payload=limpar_payload_auditavel(payload),
    )


def usuario_tem_recurso_premium(usuario, recurso_nome):
    """Centraliza a decisao sobre quais recursos premium o usuario pode usar."""
    plano_usuario = obter_plano_usuario(usuario)

    if recurso_nome == "ia":
        return plano_usuario.eh_premium and plano_usuario.ia_habilitada

    return plano_usuario.eh_premium


def bloquear_recurso_premium(request, recurso_nome, rota_retorno="configuracoes"):
    """Bloqueia uso real de recurso premium no backend e registra a tentativa."""
    if usuario_tem_recurso_premium(request.user, recurso_nome):
        return None

    recurso = RECURSOS_PREMIUM.get(recurso_nome, {})
    mensagem = recurso.get("mensagem", "Este recurso faz parte do plano Premium.")
    plano_usuario = obter_plano_usuario(request.user)

    registrar_evento_assinatura(
        tipo=EventoAssinatura.TIPO_ACESSO_BLOQUEADO,
        origem=EventoAssinatura.ORIGEM_USUARIO,
        usuario=request.user,
        plano=plano_usuario,
        mensagem=f"Tentativa bloqueada de uso do recurso premium: {recurso_nome}.",
        payload={
            "recurso": recurso_nome,
            "metodo": request.method,
            "caminho": request.path,
        },
    )
    messages.warning(request, mensagem)
    return redirect(rota_retorno)


def atingiu_limite_temporario(request, chave, limite, janela_segundos):
    """Rate limit simples para acoes caras ou sensiveis sem dependencia externa."""
    if request.user.is_authenticated:
        identificador = f"user:{request.user.id}"
    else:
        identificador = f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"

    chave_cache = f"finanpy:rate:{chave}:{identificador}"

    criado = cache.add(chave_cache, 1, janela_segundos)
    if criado:
        return False

    tentativas = cache.incr(chave_cache)
    return tentativas > limite


def sincronizar_plano_com_gateway(plano_usuario):
    """Atualiza o plano local de acordo com o status da assinatura no Mercado Pago."""
    if not plano_usuario.mercado_pago_preapproval_id:
        return plano_usuario

    plano_anterior = plano_usuario.nome_plano
    status_anterior = plano_usuario.status
    cliente = MercadoPagoClient()
    resposta = cliente.buscar_assinatura(plano_usuario.mercado_pago_preapproval_id)
    status_gateway = str(resposta.get("status") or "")
    referencia_gateway = str(resposta.get("external_reference") or "")
    referencia_esperada = f"finanpy-premium-user-{plano_usuario.usuario_id}"

    if referencia_gateway and referencia_gateway != referencia_esperada:
        logger.error(
            "Assinatura Mercado Pago %s recusada por referencia externa divergente.",
            plano_usuario.mercado_pago_preapproval_id,
        )
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
            plano=plano_usuario,
            mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
            status_gateway=status_gateway,
            referencia_externa=referencia_gateway,
            mensagem="Assinatura recusada por referencia externa divergente.",
            payload={"resposta_gateway": resposta, "referencia_esperada": referencia_esperada},
        )
        raise MercadoPagoErro("A assinatura retornada pelo gateway nao pertence a esta conta.")

    plano_usuario.mercado_pago_status = status_gateway
    plano_usuario.mercado_pago_checkout_url = resposta.get("init_point") or plano_usuario.mercado_pago_checkout_url
    if referencia_gateway:
        plano_usuario.mercado_pago_referencia_externa = referencia_gateway
    plano_usuario.ultima_sincronizacao_gateway = timezone.now()

    if status_gateway in {"authorized", "active"}:
        plano_usuario.ativar_premium()
    elif status_gateway in {"cancelled", "paused"}:
        plano_usuario.ativar_freemium(status=PlanoUsuario.STATUS_CANCELADO)

    plano_usuario.save()
    registrar_evento_assinatura(
        tipo=EventoAssinatura.TIPO_SINCRONIZACAO,
        origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
        plano=plano_usuario,
        mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
        status_gateway=status_gateway,
        referencia_externa=plano_usuario.mercado_pago_referencia_externa,
        valor=plano_usuario.valor_mensal,
        mensagem="Status da assinatura sincronizado com o Mercado Pago.",
        payload={
            "resposta_gateway": resposta,
            "plano_anterior": plano_anterior,
            "status_anterior": status_anterior,
            "plano_atual": plano_usuario.nome_plano,
            "status_atual": plano_usuario.status,
        },
    )

    if plano_anterior != plano_usuario.nome_plano or status_anterior != plano_usuario.status:
        tipo_evento = (
            EventoAssinatura.TIPO_PREMIUM_ATIVADO
            if plano_usuario.eh_premium
            else EventoAssinatura.TIPO_PREMIUM_CANCELADO
        )
        registrar_evento_assinatura(
            tipo=tipo_evento,
            origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
            plano=plano_usuario,
            mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
            status_gateway=status_gateway,
            referencia_externa=plano_usuario.mercado_pago_referencia_externa,
            valor=plano_usuario.valor_mensal,
            mensagem="Plano local atualizado apos sincronizacao com o gateway.",
            payload={
                "plano_anterior": plano_anterior,
                "status_anterior": status_anterior,
                "plano_atual": plano_usuario.nome_plano,
                "status_atual": plano_usuario.status,
            },
        )
        if plano_usuario.eh_premium:
            criar_notificacao(
                plano_usuario.usuario,
                "Pagamento Recebido",
                "Seu Plano Premium está ativo. A Análise Financeira com IA foi liberada.",
                Notificacao.TIPO_SUCESSO,
                reverse("configuracoes"),
            )
    return plano_usuario


def extrair_cabecalho_assinatura(valor_cabecalho):
    """Converte o header x-signature em um dicionario simples."""
    partes = {}

    if not valor_cabecalho:
        return partes

    for item in valor_cabecalho.split(","):
        if "=" not in item:
            continue
        chave, valor = item.split("=", 1)
        partes[chave.strip()] = valor.strip()

    return partes


def validar_assinatura_webhook_mercado_pago(request, data_id=None):
    """Valida a origem do webhook quando a secret key estiver configurada."""
    secret = getattr(settings, "MERCADO_PAGO_WEBHOOK_SECRET", "")

    if not secret:
        if settings.DEBUG:
            logger.warning("Webhook Mercado Pago aceito sem secret porque DEBUG=True.")
            return True
        logger.error("Webhook Mercado Pago recusado: MERCADO_PAGO_WEBHOOK_SECRET ausente em producao.")
        return False

    partes_assinatura = extrair_cabecalho_assinatura(request.headers.get("x-signature", ""))
    timestamp = partes_assinatura.get("ts")
    hash_recebido = partes_assinatura.get("v1")
    request_id = request.headers.get("x-request-id", "")
    data_id_url = request.GET.get("data.id") or request.GET.get("id")

    if not timestamp or not hash_recebido or not request_id:
        logger.warning("Webhook Mercado Pago sem dados suficientes para validar assinatura.")
        return False

    partes_manifesto = []
    if data_id_url:
        data_id_assinatura = str(data_id_url).lower() if str(data_id_url).isalnum() else str(data_id_url)
        partes_manifesto.append(f"id:{data_id_assinatura};")

    partes_manifesto.append(f"request-id:{request_id};")
    partes_manifesto.append(f"ts:{timestamp};")
    manifesto = "".join(partes_manifesto)
    hash_esperado = hmac.new(
        secret.encode("utf-8"),
        manifesto.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assinatura_valida = hmac.compare_digest(hash_esperado, hash_recebido)
    if not assinatura_valida:
        logger.warning("Webhook Mercado Pago recusado por assinatura invalida.")
    return assinatura_valida


def validar_limite_plano(request, recurso_nome):
    """Confere se o usuario ainda pode cadastrar um recurso no plano atual."""
    plano_usuario = obter_plano_usuario(request.user)
    configuracao = LIMITES_RECURSOS[recurso_nome]

    if plano_usuario.eh_premium:
        return None

    total_atual = getattr(request.user, configuracao["nome"]).count()
    limite_atual = getattr(plano_usuario, f"limite_{recurso_nome}")

    if total_atual >= limite_atual:
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ACESSO_BLOQUEADO,
            origem=EventoAssinatura.ORIGEM_USUARIO,
            usuario=request.user,
            plano=plano_usuario,
            mensagem=f"Limite freemium atingido para o recurso: {recurso_nome}.",
            payload={
                "recurso": recurso_nome,
                "total_atual": total_atual,
                "limite_atual": limite_atual,
                "caminho": request.path,
            },
        )
        messages.warning(request, configuracao["mensagem"])
        return redirect("configuracoes")

    return None


def usuario_tem_ia_liberada(usuario):
    """Centraliza a regra de acesso ao modulo premium de IA."""
    return usuario_tem_recurso_premium(usuario, "ia")


def usuario_tem_simulador_liberado(usuario):
    """O simulador de decisoes e um diferencial reservado ao Premium."""
    return usuario_tem_recurso_premium(usuario, "simulador")


def usuario_tem_contencao_liberada(usuario):
    """O modo anti-descontrole tambem faz parte do pacote Premium."""
    return usuario_tem_recurso_premium(usuario, "contencao")


def usuario_tem_modo_familia_liberado(usuario):
    """O modo casal/familia e um diferencial reservado ao plano Premium."""
    return usuario_tem_recurso_premium(usuario, "familia")


def obter_orcamentos_compartilhados(usuario):
    """Retorna os orçamentos ativos em que o usuário participa."""
    return OrcamentoCompartilhado.objects.filter(
        ativo=True,
        membros__usuario=usuario,
    ).distinct()


def serializar_decimal(valor):
    """Padroniza valores monetarios para texto no contexto enviado a IA."""
    if valor is None:
        return "0.00"
    return f"{valor:.2f}"


def formatar_percentual(valor):
    """Formata percentuais calculados com Decimal."""
    return round(float(valor), 2)


def normalizar_texto(texto):
    """Remove acentos e deixa o texto em minúsculo para comparar palavras-chave."""
    texto_seguro = texto or ""
    texto_normalizado = unicodedata.normalize("NFKD", texto_seguro)
    texto_sem_acentos = "".join(
        caractere for caractere in texto_normalizado if not unicodedata.combining(caractere)
    )
    return texto_sem_acentos.lower()


def obter_visual_vilao(nome):
    """Escolhe ícone, apelido e cor do vilão financeiro a partir do nome da categoria."""
    nome_normalizado = normalizar_texto(nome)
    visuais = [
        {
            "termos": ["delivery", "ifood", "restaurante", "lanche", "pizza", "hamburguer", "alimentacao"],
            "icone": "fa-burger",
            "apelido": "O Devora-Orçamento",
            "classe": "villain-red",
            "mensagem": "Comida fora costuma parecer pequena no dia, mas vira um monstro quando soma no mês.",
        },
        {
            "termos": ["cartao", "credito", "fatura", "parcelada", "parcelamento"],
            "icone": "fa-credit-card",
            "apelido": "O Dragão do Cartão",
            "classe": "villain-purple",
            "mensagem": "O cartão concentrou gastos importantes. Vale conferir compras parceladas e recorrentes.",
        },
        {
            "termos": ["assinatura", "netflix", "spotify", "streaming", "plano", "mensalidade"],
            "icone": "fa-repeat",
            "apelido": "O Invisível Recorrente",
            "classe": "villain-blue",
            "mensagem": "Gastos recorrentes passam quietos. Revise o que ainda faz sentido manter ativo.",
        },
        {
            "termos": ["mercado", "supermercado", "compras", "feira"],
            "icone": "fa-basket-shopping",
            "apelido": "O Carrinho Faminto",
            "classe": "villain-orange",
            "mensagem": "Mercado pesa menos quando existe lista, teto por compra e comparação de preços.",
        },
        {
            "termos": ["transporte", "uber", "99", "combustivel", "gasolina", "onibus", "metro", "estacionamento"],
            "icone": "fa-car-side",
            "apelido": "O Motor do Gasto",
            "classe": "villain-yellow",
            "mensagem": "Transporte pode escapar fácil. Agrupar trajetos e planejar deslocamentos ajuda bastante.",
        },
        {
            "termos": ["lazer", "cinema", "bar", "show", "viagem", "passeio"],
            "icone": "fa-martini-glass-citrus",
            "apelido": "O Rolê Premium",
            "classe": "villain-pink",
            "mensagem": "Lazer é importante, mas precisa caber no plano para não sabotar suas metas.",
        },
        {
            "termos": ["saude", "farmacia", "medico", "remedio", "consulta"],
            "icone": "fa-heart-pulse",
            "apelido": "O Gasto Necessário",
            "classe": "villain-green",
            "mensagem": "Saúde é prioridade. O ideal é prever uma reserva para esse tipo de despesa.",
        },
    ]

    for visual in visuais:
        if any(termo in nome_normalizado for termo in visual["termos"]):
            return visual

    return {
        "icone": "fa-mask",
        "apelido": "O Infiltrado do Mês",
        "classe": "villain-slate",
        "mensagem": "Essa categoria merece investigação. Veja os lançamentos e procure cortes simples.",
    }


def classificar_vilao(percentual):
    """Transforma a participação no mês em um nível visual de alerta."""
    if percentual >= Decimal("35"):
        return "critico", "Vilão principal"
    if percentual >= Decimal("22"):
        return "alto", "Muito perigoso"
    if percentual >= Decimal("12"):
        return "medio", "Ponto de atenção"
    return "leve", "Sob observação"


def montar_busca_por_palavras_chave(termos):
    """Cria uma busca OR em descricao e categoria para encontrar padroes de gasto."""
    consulta = Q()

    for termo in termos:
        consulta |= Q(descricao__icontains=termo)
        consulta |= Q(categoria__nome__icontains=termo)

    return consulta


def montar_sinal_vilao(nome, queryset, total_despesas, icone, mensagem, classe):
    """Monta um cartão resumido de padrão perigoso quando houver gasto no mês."""
    total = somar_valores(queryset)

    if total <= 0:
        return None

    percentual = Decimal("0.00")
    if total_despesas > 0:
        percentual = min((total / total_despesas) * Decimal("100"), Decimal("100.00"))

    return {
        "nome": nome,
        "total": total,
        "quantidade": queryset.count(),
        "percentual": formatar_percentual(percentual),
        "percentual_css": f"{percentual:.2f}",
        "icone": icone,
        "mensagem": mensagem,
        "classe": classe,
    }


def calcular_simulacao_decisao(usuario, dados):
    """Calcula o impacto da decisao no saldo previsto dos proximos meses."""
    descricao = dados["descricao"]
    tipo = dados["tipo"]
    prioridade = dados["prioridade"]
    valor_total = dados["valor_total"]
    quantidade_parcelas = dados["quantidade_parcelas"]
    mes_inicio = dados["mes_inicio"].replace(day=1)
    valor_parcela = valor_total / Decimal(quantidade_parcelas)

    meses = []
    maior_comprometimento = Decimal("0.00")
    meses_negativos = 0
    menor_saldo_apos = None

    for indice in range(quantidade_parcelas):
        mes_atual = adicionar_meses(mes_inicio, indice)
        inicio_mes = date(mes_atual.year, mes_atual.month, 1)
        fim_mes = date(mes_atual.year, mes_atual.month, monthrange(mes_atual.year, mes_atual.month)[1])
        lancamentos_mes = Lancamento.objects.filter(
            usuario=usuario,
            data_competencia__range=(inicio_mes, fim_mes),
        )

        receitas = somar_valores(lancamentos_mes.filter(tipo=Lancamento.TIPO_RECEITA))
        despesas = somar_valores(lancamentos_mes.filter(tipo=Lancamento.TIPO_DESPESA))
        saldo_previsto = receitas - despesas
        saldo_apos_decisao = saldo_previsto - valor_parcela
        comprometimento = Decimal("0.00")

        if receitas > 0:
            comprometimento = (valor_parcela / receitas) * Decimal("100")

        maior_comprometimento = max(maior_comprometimento, comprometimento)
        menor_saldo_apos = saldo_apos_decisao if menor_saldo_apos is None else min(menor_saldo_apos, saldo_apos_decisao)

        if saldo_apos_decisao < 0:
            meses_negativos += 1
            status_mes = "CRITICO"
            status_label = "Fica negativo"
        elif comprometimento >= Decimal("25.00"):
            status_mes = "ATENCAO"
            status_label = "Compromete muito"
        elif comprometimento >= Decimal("12.00"):
            status_mes = "CUIDADO"
            status_label = "Exige cuidado"
        else:
            status_mes = "OK"
            status_label = "Impacto controlado"

        meses.append(
            {
                "mes": inicio_mes.strftime("%m/%Y"),
                "receitas": receitas,
                "despesas": despesas,
                "saldo_previsto": saldo_previsto,
                "impacto": valor_parcela,
                "saldo_apos_decisao": saldo_apos_decisao,
                "comprometimento": formatar_percentual(comprometimento),
                "status": status_mes,
                "status_label": status_label,
            }
        )

    if meses_negativos:
        recomendacao = "Nao recomendado agora"
        nivel_risco = "CRITICO"
        explicacao = "A decisao deixa pelo menos um mes com saldo previsto negativo."
    elif maior_comprometimento >= Decimal("25.00"):
        recomendacao = "Adiar ou reduzir o valor"
        nivel_risco = "ALTO"
        explicacao = "A parcela compromete uma parte alta das receitas do periodo."
    elif maior_comprometimento >= Decimal("12.00") or prioridade == SimuladorDecisaoForm.PRIORIDADE_DESEJO:
        recomendacao = "Pode fazer com cautela"
        nivel_risco = "MEDIO"
        explicacao = "A compra parece possivel, mas merece limite claro para nao afetar metas e reservas."
    else:
        recomendacao = "Decisao viavel"
        nivel_risco = "BAIXO"
        explicacao = "O impacto estimado fica controlado dentro dos meses analisados."

    return {
        "descricao": descricao,
        "tipo": tipo,
        "prioridade": prioridade,
        "valor_total": valor_total,
        "quantidade_parcelas": quantidade_parcelas,
        "valor_parcela": valor_parcela,
        "mes_inicio": mes_inicio,
        "maior_comprometimento": formatar_percentual(maior_comprometimento),
        "menor_saldo_apos": menor_saldo_apos or Decimal("0.00"),
        "meses_negativos": meses_negativos,
        "nivel_risco": nivel_risco,
        "recomendacao": recomendacao,
        "explicacao": explicacao,
        "meses": meses,
    }


def obter_plano_contencao_ativo(usuario):
    """Busca o plano ativo atual e finaliza automaticamente planos vencidos."""
    planos_ativos = PlanoContencao.objects.filter(
        usuario=usuario,
        status=PlanoContencao.STATUS_ATIVO,
    ).order_by("-data_inicio", "-id")

    plano_atual = None
    for plano in planos_ativos:
        status_original = plano.status
        plano.atualizar_status_automaticamente()
        if plano.status != status_original:
            plano.save(update_fields=["status", "atualizado_em"])
            continue
        if plano.esta_ativo:
            plano_atual = plano
            break

    return plano_atual


def calcular_resumo_contencao(plano):
    """Calcula limites, gasto real e saldo diario do plano anti-descontrole."""
    if not plano:
        return None

    limites = plano.limites.select_related("categoria")
    gasto_total = Decimal("0.00")
    categorias = []
    alertas = []

    for limite in limites:
        gasto_categoria = somar_valores(
            Lancamento.objects.filter(
                usuario=plano.usuario,
                tipo=Lancamento.TIPO_DESPESA,
                categoria=limite.categoria,
                data_competencia__range=(plano.data_inicio, plano.data_fim),
            )
        )
        restante = limite.limite - gasto_categoria
        percentual = Decimal("0.00")
        if limite.limite > 0:
            percentual = (gasto_categoria / limite.limite) * Decimal("100")

        if percentual >= Decimal("100.00"):
            nivel = "CRITICO"
            status_label = "Limite estourado"
            alertas.append(f"{limite.categoria.nome} ultrapassou o limite definido.")
        elif percentual >= Decimal("80.00"):
            nivel = "ATENCAO"
            status_label = "Perto do limite"
            alertas.append(f"{limite.categoria.nome} ja consumiu mais de 80% do limite.")
        else:
            nivel = "OK"
            status_label = "Dentro do plano"

        gasto_total += gasto_categoria
        categorias.append(
            {
                "nome": limite.categoria.nome,
                "limite": limite.limite,
                "gasto": gasto_categoria,
                "restante": restante if restante > 0 else Decimal("0.00"),
                "percentual": formatar_percentual(min(percentual, Decimal("100.00"))),
                "nivel": nivel,
                "status_label": status_label,
            }
        )

    restante_total = plano.orcamento_total - gasto_total
    restante_total = restante_total if restante_total > 0 else Decimal("0.00")
    dias_restantes = plano.dias_restantes or 1
    gasto_por_dia = restante_total / Decimal(dias_restantes)

    if gasto_total > plano.orcamento_total:
        alertas.append("O orçamento total do modo anti-descontrole ja foi ultrapassado.")
    elif gasto_por_dia <= Decimal("20.00") and plano.dias_restantes > 0:
        alertas.append("O valor disponivel por dia esta baixo. Priorize apenas gastos essenciais.")

    return {
        "gasto_total": gasto_total,
        "restante_total": restante_total,
        "gasto_por_dia": gasto_por_dia,
        "dias_restantes": plano.dias_restantes,
        "dias_totais": plano.dias_totais,
        "categorias": categorias,
        "alertas": alertas,
    }


def calcular_resumo_orcamento_compartilhado(usuario, orcamento, inicio_mes, fim_mes):
    """Separa contas conjuntas do orçamento e contas individuais do usuário."""
    lancamentos_conjuntos = Lancamento.objects.filter(
        orcamento_compartilhado=orcamento,
        escopo=Lancamento.ESCOPO_COMPARTILHADO,
        data_competencia__range=(inicio_mes, fim_mes),
    ).select_related("usuario", "categoria", "cartao")
    lancamentos_individuais = Lancamento.objects.filter(
        usuario=usuario,
        data_competencia__range=(inicio_mes, fim_mes),
    ).filter(Q(escopo=Lancamento.ESCOPO_INDIVIDUAL) | Q(orcamento_compartilhado__isnull=True))

    receitas_conjuntas = lancamentos_conjuntos.filter(tipo=Lancamento.TIPO_RECEITA)
    despesas_conjuntas = lancamentos_conjuntos.filter(tipo=Lancamento.TIPO_DESPESA)
    receitas_individuais = lancamentos_individuais.filter(tipo=Lancamento.TIPO_RECEITA)
    despesas_individuais = lancamentos_individuais.filter(tipo=Lancamento.TIPO_DESPESA)

    participantes = orcamento.membros.select_related("usuario").order_by("papel", "usuario__first_name", "usuario__username")
    resumo_participantes = []

    for membro in participantes:
        lancamentos_membro = lancamentos_conjuntos.filter(usuario=membro.usuario)
        total_receitas = somar_valores(lancamentos_membro.filter(tipo=Lancamento.TIPO_RECEITA))
        total_despesas = somar_valores(lancamentos_membro.filter(tipo=Lancamento.TIPO_DESPESA))
        resumo_participantes.append(
            {
                "membro": membro,
                "receitas": total_receitas,
                "despesas": total_despesas,
                "saldo": total_receitas - total_despesas,
                "quantidade": lancamentos_membro.count(),
            }
        )

    categorias_conjuntas = (
        despesas_conjuntas.values("categoria__nome")
        .annotate(total=Sum("valor"), quantidade=Count("id"))
        .order_by("-total")[:5]
    )

    return {
        "lancamentos_conjuntos": lancamentos_conjuntos.order_by("-data_competencia", "-id")[:8],
        "lancamentos_individuais": lancamentos_individuais.order_by("-data_competencia", "-id")[:8],
        "total_receitas_conjuntas": somar_valores(receitas_conjuntas),
        "total_despesas_conjuntas": somar_valores(despesas_conjuntas),
        "saldo_conjunto": somar_valores(receitas_conjuntas) - somar_valores(despesas_conjuntas),
        "total_receitas_individuais": somar_valores(receitas_individuais),
        "total_despesas_individuais": somar_valores(despesas_individuais),
        "saldo_individual": somar_valores(receitas_individuais) - somar_valores(despesas_individuais),
        "quantidade_conjuntas": lancamentos_conjuntos.count(),
        "quantidade_individuais": lancamentos_individuais.count(),
        "participantes": participantes,
        "resumo_participantes": resumo_participantes,
        "categorias_conjuntas": categorias_conjuntas,
    }


def calcular_metas_inteligentes(usuario, metas):
    """Gera recomendacoes praticas para cada meta sem depender de recurso premium."""
    hoje = date.today()
    inicio_mes = date(hoje.year, hoje.month, 1)
    fim_mes = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])
    despesas_por_categoria = list(
        Lancamento.objects.filter(
            usuario=usuario,
            tipo=Lancamento.TIPO_DESPESA,
            data_competencia__range=(inicio_mes, fim_mes),
        )
        .values("categoria__nome")
        .annotate(total=Sum("valor"))
        .order_by("-total")[:3]
    )

    sugestoes = {}

    for meta in metas:
        restante = meta.valor_restante
        dias_restantes = max((meta.data_limite - hoje).days, 0)
        semanas_restantes = max(Decimal(dias_restantes) / Decimal("7"), Decimal("1"))
        valor_semanal_necessario = restante / semanas_restantes if restante > 0 else Decimal("0.00")

        if meta.valor_semanal_planejado > 0:
            valor_semanal = meta.valor_semanal_planejado
        elif meta.estrategia == MetaFinanceira.ESTRATEGIA_CONSERVADORA:
            valor_semanal = valor_semanal_necessario * Decimal("0.45")
        elif meta.estrategia == MetaFinanceira.ESTRATEGIA_SUAVE:
            valor_semanal = valor_semanal_necessario * Decimal("0.70")
        else:
            valor_semanal = valor_semanal_necessario

        valor_mensal = valor_semanal * Decimal("4.33")
        ritmo_sugerido = valor_semanal if valor_semanal > 0 else Decimal("0.00")
        semanas_estimadas = Decimal("0.00")

        if ritmo_sugerido > 0 and restante > 0:
            semanas_estimadas = restante / ritmo_sugerido

        cortes = []
        for item in despesas_por_categoria:
            total_categoria = item["total"] or Decimal("0.00")
            corte_sugerido = total_categoria * Decimal("0.12")
            if corte_sugerido <= 0:
                continue
            cortes.append(
                {
                    "categoria": item["categoria__nome"] or "Sem categoria",
                    "valor": corte_sugerido,
                    "mensagem": f"Reduzir cerca de R$ {corte_sugerido:.2f} em {item['categoria__nome'] or 'Sem categoria'} neste mês.",
                }
            )

        if restante <= 0:
            resumo = "Meta concluída. Mantenha o hábito e direcione novos aportes para o próximo objetivo."
        elif dias_restantes == 0:
            resumo = "O prazo termina hoje. Considere ajustar a data limite ou fazer um aporte final."
        elif meta.estrategia == MetaFinanceira.ESTRATEGIA_CONSERVADORA:
            resumo = "Meta conservadora. O prazo fica mais confortável e exige menos pressão semanal."
        elif meta.estrategia == MetaFinanceira.ESTRATEGIA_SUAVE:
            resumo = "Meta suave. O plano equilibra consistência e tranquilidade no orçamento."
        else:
            resumo = "Meta agressiva. Exige aportes maiores e pede atenção para não pressionar o orçamento."

        sugestoes[meta.id] = {
            "dias_restantes": dias_restantes,
            "semanas_restantes": round(float(semanas_restantes), 1),
            "valor_semanal": valor_semanal,
            "valor_semanal_necessario": valor_semanal_necessario,
            "valor_mensal": valor_mensal,
            "semanas_estimadas": round(float(semanas_estimadas), 1),
            "cortes": cortes,
            "resumo": resumo,
            "estrategia": meta.get_estrategia_display(),
        }

    return sugestoes


def construir_contexto_analise_financeira(usuario, inicio_mes, fim_mes):
    """Monta um contexto rico, porem enxuto, para a IA analisar."""
    lancamentos_mes = Lancamento.objects.filter(
        usuario=usuario,
        data_competencia__range=(inicio_mes, fim_mes),
    ).select_related("categoria", "cartao", "orcamento_compartilhado")

    receitas_mes = lancamentos_mes.filter(tipo=Lancamento.TIPO_RECEITA)
    despesas_mes = lancamentos_mes.filter(tipo=Lancamento.TIPO_DESPESA)
    receitas_pagas = receitas_mes.filter(status=Lancamento.STATUS_PAGO)
    despesas_pagas = despesas_mes.filter(status=Lancamento.STATUS_PAGO)
    pendentes = lancamentos_mes.filter(status=Lancamento.STATUS_PENDENTE)
    atrasados = lancamentos_mes.filter(status=Lancamento.STATUS_ATRASADO)

    total_receitas = somar_valores(receitas_mes)
    total_despesas = somar_valores(despesas_mes)
    saldo_previsto = total_receitas - total_despesas
    saldo_mes = somar_valores(receitas_pagas) - somar_valores(despesas_pagas)

    percentual_despesas = Decimal("0.00")
    if total_receitas > 0:
        percentual_despesas = (total_despesas / total_receitas) * Decimal("100")

    despesas_por_categoria = list(
        despesas_mes.values("categoria__nome")
        .annotate(total=Sum("valor"))
        .order_by("-total")
    )
    top_categoria = despesas_por_categoria[0]["categoria__nome"] if despesas_por_categoria else "Nenhuma"

    metas = MetaFinanceira.objects.filter(usuario=usuario).order_by("status", "data_limite")
    investimentos = Investimento.objects.filter(usuario=usuario)
    investimentos_ativos = investimentos.filter(status=Investimento.STATUS_ATIVO)
    total_investido = somar_campo(investimentos_ativos, "valor_aplicado")
    total_atual_investimentos = somar_campo(investimentos_ativos, "valor_atual")

    comparativo_meses = []
    mes_cursor = date(inicio_mes.year, inicio_mes.month, 1)
    for indice in range(2, -1, -1):
        mes_analisado = adicionar_meses(mes_cursor, -indice)
        inicio_base = date(mes_analisado.year, mes_analisado.month, 1)
        fim_base = date(
            mes_analisado.year,
            mes_analisado.month,
            monthrange(mes_analisado.year, mes_analisado.month)[1],
        )
        lancamentos_base = Lancamento.objects.filter(
            usuario=usuario,
            data_competencia__range=(inicio_base, fim_base),
        )
        receitas_base = somar_valores(lancamentos_base.filter(tipo=Lancamento.TIPO_RECEITA))
        despesas_base = somar_valores(lancamentos_base.filter(tipo=Lancamento.TIPO_DESPESA))
        comparativo_meses.append(
            {
                "mes": inicio_base.strftime("%m/%Y"),
                "receitas": serializar_decimal(receitas_base),
                "despesas": serializar_decimal(despesas_base),
                "saldo": serializar_decimal(receitas_base - despesas_base),
            }
        )

    return {
        "periodo": {
            "inicio": inicio_mes.isoformat(),
            "fim": fim_mes.isoformat(),
        },
        "indicadores": {
            "total_receitas": serializar_decimal(total_receitas),
            "total_despesas": serializar_decimal(total_despesas),
            "saldo_previsto": serializar_decimal(saldo_previsto),
            "saldo_mes": serializar_decimal(saldo_mes),
            "total_pendente": serializar_decimal(somar_valores(pendentes)),
            "total_atrasado": serializar_decimal(somar_valores(atrasados)),
            "percentual_despesas_sobre_receitas": serializar_decimal(percentual_despesas),
        },
        "contagens": {
            "lancamentos": lancamentos_mes.count(),
            "contas_pendentes": pendentes.count(),
            "contas_atrasadas": atrasados.count(),
            "metas_total": metas.count(),
            "metas_concluidas": metas.filter(status=MetaFinanceira.STATUS_CONCLUIDA).count(),
            "investimentos_total": investimentos.count(),
            "investimentos_ativos": investimentos_ativos.count(),
        },
        "categorias_despesa": [
            {
                "nome": item["categoria__nome"] or "Sem categoria",
                "total": serializar_decimal(item["total"] or Decimal("0.00")),
            }
            for item in despesas_por_categoria[:6]
        ],
        "principal_categoria_despesa": top_categoria,
        "metas": [
            {
                "titulo": meta.titulo,
                "status": meta.get_status_display(),
                "progresso_percentual": meta.progresso_percentual,
                "valor_atual": serializar_decimal(meta.valor_atual),
                "valor_alvo": serializar_decimal(meta.valor_alvo),
                "valor_restante": serializar_decimal(meta.valor_restante),
                "prazo": meta.data_limite.isoformat(),
            }
            for meta in metas[:5]
        ],
        "investimentos": {
            "total_aplicado": serializar_decimal(total_investido),
            "total_atual": serializar_decimal(total_atual_investimentos),
            "rentabilidade_total": serializar_decimal(total_atual_investimentos - total_investido),
        },
        "comparativo_recente": comparativo_meses,
        "lancamentos_recentes": [
            {
                "descricao": lancamento.descricao_completa,
                "tipo": lancamento.get_tipo_display(),
                "status": lancamento.get_status_display(),
                "categoria": lancamento.categoria.nome,
                "valor": serializar_decimal(lancamento.valor),
                "data_competencia": lancamento.data_competencia.isoformat(),
            }
            for lancamento in lancamentos_mes.order_by("-data_competencia", "-id")[:8]
        ],
    }


def pagina_inicial(request):
    """
    Decide o primeiro destino do usuário.

    A página inicial pública do produto sempre apresenta o fluxo de entrada.
    Assim, quem abrir o site cai primeiro no cadastro.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")
    return redirect("registrar_usuario")


def chave_login_tentativas(request):
    """Monta uma chave estável para controlar tentativas de login por IP e usuário."""
    username = request.POST.get("username", "").strip().lower() or "sem-usuario"
    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "unknown")).split(",")[0]
    return f"finanpy:login:{ip}:{username}"


def enviar_email_confirmacao_cadastro(request, usuario):
    """Envia o link de ativação da conta para o e-mail informado no cadastro."""
    uid = urlsafe_base64_encode(force_bytes(usuario.pk))
    token = default_token_generator.make_token(usuario)
    link_ativacao = request.build_absolute_uri(
        reverse("ativar_conta", kwargs={"uidb64": uid, "token": token})
    )

    assunto = "Confirme seu cadastro no FinanPy"
    mensagem = (
        f"Olá, {usuario.first_name or usuario.username}!\n\n"
        "Seu cadastro no FinanPy foi criado com sucesso.\n"
        "Para ativar sua conta e acessar o painel, clique no link abaixo:\n\n"
        f"{link_ativacao}\n\n"
        "Se você não criou esta conta, ignore este e-mail."
    )
    send_mail(assunto, mensagem, settings.DEFAULT_FROM_EMAIL, [usuario.email], fail_silently=False)


class EntrarUsuarioView(LoginView):
    """Tela de login com formulário customizado, bloqueio temporário e layout do projeto."""

    template_name = "registration/login.html"
    authentication_form = LoginUsuarioForm
    redirect_authenticated_user = True
    limite_tentativas = 5
    tempo_bloqueio_segundos = 30 * 60

    def dispatch(self, request, *args, **kwargs):
        """Bloqueia novas tentativas quando o usuário erra cinco vezes."""
        if request.method == "POST":
            chave = chave_login_tentativas(request)
            if cache.get(f"{chave}:bloqueado"):
                messages.error(
                    request,
                    "Muitas tentativas incorretas. Redefina sua senha ou tente novamente em alguns minutos.",
                )
                return redirect("password_reset")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        """Limpa a contagem de erros quando o login é concluído com sucesso."""
        chave = chave_login_tentativas(self.request)
        cache.delete(chave)
        cache.delete(f"{chave}:bloqueado")
        return super().form_valid(form)

    def form_invalid(self, form):
        """Conta erros de login e direciona para recuperação de senha ao atingir o limite."""
        chave = chave_login_tentativas(self.request)
        tentativas = cache.get(chave, 0) + 1
        cache.set(chave, tentativas, self.tempo_bloqueio_segundos)
        restantes = max(self.limite_tentativas - tentativas, 0)

        if tentativas >= self.limite_tentativas:
            cache.set(f"{chave}:bloqueado", True, self.tempo_bloqueio_segundos)
            messages.error(
                self.request,
                "Você atingiu 5 tentativas incorretas. Use a recuperação de senha para liberar o acesso.",
            )
            return redirect("password_reset")

        messages.warning(
            self.request,
            f"Login não realizado. Você ainda tem {restantes} tentativa(s) antes da recuperação obrigatória.",
        )
        return super().form_invalid(form)


class ConfirmarRedefinicaoSenhaView(PasswordResetConfirmView):
    """Redefine a senha e envia confirmação por e-mail ao usuário."""

    template_name = "registration/password_reset_confirm.html"

    def form_valid(self, form):
        usuario = form.user
        resposta = super().form_valid(form)
        if usuario.email:
            send_mail(
                "Senha alterada no FinanPy",
                (
                    f"Olá, {usuario.first_name or usuario.username}!\n\n"
                    "Sua senha do FinanPy foi alterada com sucesso.\n"
                    "Se você não fez essa alteração, redefina sua senha imediatamente."
                ),
                settings.DEFAULT_FROM_EMAIL,
                [usuario.email],
                fail_silently=True,
            )
        return resposta


@require_POST
def sair_usuario(request):
    """
    Encerra a sessão do usuário com redirecionamento previsível.

    Usamos apenas POST para evitar logout acidental por link, crawler ou imagem
    externa. O token CSRF confirma que a ação saiu da interface do FinanPy.
    """
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, "Sua sessão foi encerrada com sucesso.")

    return redirect("login")


def registrar_usuario(request):
    """Cria uma conta inativa e envia confirmação por e-mail antes do primeiro acesso."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = RegistroUsuarioForm(request.POST)
        if form.is_valid():
            usuario = form.save(commit=False)
            usuario.is_active = False
            usuario.email = form.cleaned_data["email"].strip().lower()
            usuario.save()
            enviar_email_confirmacao_cadastro(request, usuario)
            messages.success(
                request,
                "Cadastro criado com sucesso. Enviamos um e-mail de confirmação para ativar sua conta.",
            )
            return redirect("login")
        messages.error(request, "Revise os dados informados para concluir seu cadastro.")
    else:
        form = RegistroUsuarioForm()

    return render(request, "registration/register.html", {"form": form})


def ativar_conta(request, uidb64, token):
    """Ativa a conta depois que o usuário clica no link enviado por e-mail."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        usuario = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        usuario = None

    if usuario and default_token_generator.check_token(usuario, token):
        usuario.is_active = True
        usuario.save(update_fields=["is_active"])
        PlanoUsuario.objects.get_or_create(usuario=usuario)
        ConfiguracaoUsuario.objects.get_or_create(usuario=usuario)
        messages.success(request, "E-mail confirmado com sucesso. Agora você já pode entrar no FinanPy.")
        return redirect("login")

    messages.error(request, "Link de confirmação inválido ou expirado. Solicite um novo cadastro ou redefinição.")
    return redirect("registrar_usuario")


@login_required
def perfil_usuario(request):
    """Pagina de perfil com dados pessoais, moeda, senha e foto."""
    configuracao, _ = ConfiguracaoUsuario.objects.get_or_create(usuario=request.user)

    perfil_form = PerfilUsuarioForm(instance=request.user, prefix="perfil")
    moeda_form = MoedaPerfilForm(instance=configuracao, prefix="moeda")
    foto_form = FotoPerfilForm(instance=configuracao, prefix="foto")
    senha_form = AlterarSenhaPerfilForm(request.user, prefix="senha")

    if request.method == "POST":
        if "salvar_perfil" in request.POST:
            perfil_form = PerfilUsuarioForm(request.POST, instance=request.user, prefix="perfil")
            if perfil_form.is_valid():
                perfil_form.save()
                messages.success(request, "Perfil atualizado com sucesso.")
                return redirect("perfil_usuario")
            messages.error(request, "Revise os dados do perfil para continuar.")

        elif "salvar_moeda" in request.POST:
            moeda_form = MoedaPerfilForm(request.POST, instance=configuracao, prefix="moeda")
            if moeda_form.is_valid():
                moeda_form.save()
                messages.success(request, "Moeda principal atualizada com sucesso.")
                return redirect("perfil_usuario")
            messages.error(request, "Revise a moeda selecionada para continuar.")

        elif "salvar_foto" in request.POST:
            foto_form = FotoPerfilForm(request.POST, request.FILES, instance=configuracao, prefix="foto")
            if foto_form.is_valid():
                foto_form.save()
                messages.success(request, "Foto de perfil atualizada com sucesso.")
                return redirect("perfil_usuario")
            messages.error(request, "Nao foi possivel atualizar a foto. Revise o arquivo enviado.")

        elif "alterar_senha" in request.POST:
            senha_form = AlterarSenhaPerfilForm(request.user, request.POST, prefix="senha")
            if senha_form.is_valid():
                usuario = senha_form.save()
                update_session_auth_hash(request, usuario)
                messages.success(request, "Senha alterada com sucesso.")
                return redirect("perfil_usuario")
            messages.error(request, "Nao foi possivel alterar a senha. Revise os campos informados.")

    contexto = {
        "perfil_form": perfil_form,
        "moeda_form": moeda_form,
        "foto_form": foto_form,
        "senha_form": senha_form,
        "configuracao_usuario": configuracao,
    }
    return render(request, "perfil/painel.html", contexto)


@login_required
@require_POST
def excluir_conta_usuario(request):
    """Exclui definitivamente a conta e todos os dados ligados ao usuário."""
    form = ExcluirContaForm(request.user, request.POST, prefix="excluir")

    if not form.is_valid():
        for erros in form.errors.values():
            for erro in erros:
                messages.error(request, erro)
        return redirect("configuracoes")

    usuario = request.user
    logout(request)
    usuario.delete()
    messages.success(request, "Sua conta e todos os dados foram excluídos definitivamente.")
    return redirect("login")


@login_required
@require_POST
def marcar_notificacoes_lidas(request):
    """Marca os alertas do sininho como lidos."""
    Notificacao.objects.filter(usuario=request.user, lida=False).update(lida=True)
    return redirect(request.POST.get("proximo") or "dashboard")


@login_required
def simulador_decisoes(request):
    """Simula o impacto de uma compra antes dela acontecer."""
    plano_usuario = obter_plano_usuario(request.user)
    simulador_liberado = usuario_tem_simulador_liberado(request.user)
    resultado = None
    mes_atual = date.today().replace(day=1).strftime("%Y-%m")
    form = SimuladorDecisaoForm(initial={"mes_inicio": mes_atual, "quantidade_parcelas": 1})

    if request.method == "POST":
        form = SimuladorDecisaoForm(request.POST)

        bloqueio = bloquear_recurso_premium(request, "simulador", "simulador_decisoes")
        if bloqueio:
            return bloqueio

        if form.is_valid():
            resultado = calcular_simulacao_decisao(request.user, form.cleaned_data)
            messages.success(request, "Simulacao concluida com sucesso.")

    contexto = {
        "form": form,
        "plano_usuario": plano_usuario,
        "simulador_liberado": simulador_liberado,
        "resultado": resultado,
    }
    return render(request, "simulador/decisoes.html", contexto)


@login_required
def modo_contencao(request):
    """Ativa e acompanha o modo anti-descontrole premium."""
    plano_usuario = obter_plano_usuario(request.user)
    contencao_liberada = usuario_tem_contencao_liberada(request.user)
    plano_ativo = obter_plano_contencao_ativo(request.user)
    resumo = calcular_resumo_contencao(plano_ativo)
    form = PlanoContencaoForm(usuario=request.user)

    if request.method == "POST":
        form = PlanoContencaoForm(request.POST, usuario=request.user)

        bloqueio = bloquear_recurso_premium(request, "contencao", "modo_contencao")
        if bloqueio:
            return bloqueio

        if plano_ativo:
            messages.warning(request, "Ja existe um plano anti-descontrole ativo. Cancele ou finalize antes de criar outro.")
            return redirect("modo_contencao")

        if form.is_valid():
            categorias = form.cleaned_data["categorias"]
            duracao_dias = form.cleaned_data["duracao_dias"]
            data_inicio = date.today()
            data_fim = date.fromordinal(data_inicio.toordinal() + duracao_dias - 1)
            orcamento_total = form.cleaned_data["orcamento_total"]
            limite_por_categoria = orcamento_total / Decimal(categorias.count())

            plano_ativo = PlanoContencao.objects.create(
                usuario=request.user,
                titulo=form.cleaned_data["titulo"],
                duracao_dias=duracao_dias,
                data_inicio=data_inicio,
                data_fim=data_fim,
                orcamento_total=orcamento_total,
            )

            limites = [
                LimiteCategoriaContencao(
                    plano=plano_ativo,
                    categoria=categoria,
                    limite=limite_por_categoria,
                )
                for categoria in categorias
            ]
            LimiteCategoriaContencao.objects.bulk_create(limites)
            messages.success(request, "Modo Anti-descontrole ativado com sucesso.")
            return redirect("modo_contencao")

    contexto = {
        "form": form,
        "plano_usuario": plano_usuario,
        "contencao_liberada": contencao_liberada,
        "plano_ativo": plano_ativo,
        "resumo": resumo,
    }
    return render(request, "contencao/modo.html", contexto)


@login_required
@require_POST
def cancelar_modo_contencao(request, pk):
    """Cancela o plano anti-descontrole ativo do usuario."""
    plano = get_object_or_404(PlanoContencao, pk=pk, usuario=request.user)

    plano.status = PlanoContencao.STATUS_CANCELADO
    plano.save(update_fields=["status", "atualizado_em"])
    messages.success(request, "Modo Anti-descontrole cancelado com sucesso.")
    return redirect("modo_contencao")


@login_required
def modo_familia(request):
    """Permite criar e acompanhar orçamento compartilhado premium."""
    inicio_mes, fim_mes = obter_mes_referencia(request)
    plano_usuario = obter_plano_usuario(request.user)
    familia_liberada = usuario_tem_modo_familia_liberado(request.user)
    form_criar = OrcamentoCompartilhadoForm()
    form_convite = ConviteOrcamentoForm()

    if request.method == "POST":
        acao = request.POST.get("acao")

        bloqueio = bloquear_recurso_premium(request, "familia", "modo_familia")
        if bloqueio:
            return bloqueio

        if acao == "criar_orcamento":
            form_criar = OrcamentoCompartilhadoForm(request.POST)
            if form_criar.is_valid():
                orcamento = form_criar.save(commit=False)
                orcamento.dono = request.user
                orcamento.save()
                MembroOrcamento.objects.get_or_create(
                    orcamento=orcamento,
                    usuario=request.user,
                    defaults={"papel": MembroOrcamento.PAPEL_ADMIN},
                )
                messages.success(request, "Orçamento compartilhado criado com sucesso.")
                return redirect(f"{request.path}?grupo={orcamento.id}&mes={inicio_mes:%Y-%m}")

        elif acao == "entrar_orcamento":
            form_convite = ConviteOrcamentoForm(request.POST)
            if form_convite.is_valid():
                codigo = form_convite.cleaned_data["codigo_convite"]
                orcamento = OrcamentoCompartilhado.objects.filter(codigo_convite=codigo, ativo=True).first()

                if not orcamento:
                    form_convite.add_error("codigo_convite", "Código de convite não encontrado.")
                else:
                    membro, criado = MembroOrcamento.objects.get_or_create(
                        orcamento=orcamento,
                        usuario=request.user,
                        defaults={"papel": MembroOrcamento.PAPEL_MEMBRO},
                    )
                    if criado:
                        messages.success(request, f"Você entrou no orçamento {orcamento.nome}.")
                    else:
                        messages.info(request, f"Você já participa do orçamento {orcamento.nome}.")
                    return redirect(f"{request.path}?grupo={orcamento.id}&mes={inicio_mes:%Y-%m}")

    orcamentos = obter_orcamentos_compartilhados(request.user) if familia_liberada else OrcamentoCompartilhado.objects.none()
    orcamento_selecionado = None
    resumo = None
    grupo_id = request.GET.get("grupo")

    if orcamentos.exists():
        if grupo_id:
            orcamento_selecionado = orcamentos.filter(id=grupo_id).first()
        if not orcamento_selecionado:
            orcamento_selecionado = orcamentos.first()
        resumo = calcular_resumo_orcamento_compartilhado(request.user, orcamento_selecionado, inicio_mes, fim_mes)

    contexto = {
        "inicio_mes": inicio_mes,
        "fim_mes": fim_mes,
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "plano_usuario": plano_usuario,
        "familia_liberada": familia_liberada,
        "form_criar": form_criar,
        "form_convite": form_convite,
        "orcamentos": orcamentos,
        "orcamento_selecionado": orcamento_selecionado,
        "resumo": resumo,
    }
    return render(request, "familia/modo.html", contexto)


@login_required
def dashboard(request):
    """Mostra resumo mensal, saldos, metas e investimentos."""
    inicio_mes, fim_mes = obter_mes_referencia(request)
    plano_usuario = obter_plano_usuario(request.user)
    lancamentos_mes = Lancamento.objects.filter(
        usuario=request.user,
        data_competencia__range=(inicio_mes, fim_mes),
    ).select_related("categoria", "cartao", "orcamento_compartilhado")

    receitas_mes = lancamentos_mes.filter(tipo=Lancamento.TIPO_RECEITA)
    despesas_mes = lancamentos_mes.filter(tipo=Lancamento.TIPO_DESPESA)
    receitas_pagas = receitas_mes.filter(status=Lancamento.STATUS_PAGO)
    despesas_pagas = despesas_mes.filter(status=Lancamento.STATUS_PAGO)
    pendentes = lancamentos_mes.filter(status=Lancamento.STATUS_PENDENTE)
    atrasados = lancamentos_mes.filter(status=Lancamento.STATUS_ATRASADO)

    total_receitas = somar_valores(receitas_mes)
    total_despesas = somar_valores(despesas_mes)
    saldo_previsto = total_receitas - total_despesas
    saldo_mes = somar_valores(receitas_pagas) - somar_valores(despesas_pagas)

    despesas_por_categoria = (
        despesas_mes.values("categoria__nome")
        .annotate(total=Sum("valor"))
        .order_by("-total")
    )

    meses_labels = []
    receitas_series = []
    despesas_series = []
    saldo_series = []
    mes_cursor = date(inicio_mes.year, inicio_mes.month, 1)

    for indice in range(5, -1, -1):
        mes_analisado = adicionar_meses(mes_cursor, -indice)
        inicio_grafico = date(mes_analisado.year, mes_analisado.month, 1)
        fim_grafico = date(
            mes_analisado.year,
            mes_analisado.month,
            monthrange(mes_analisado.year, mes_analisado.month)[1],
        )

        lancamentos_grafico = Lancamento.objects.filter(
            usuario=request.user,
            data_competencia__range=(inicio_grafico, fim_grafico),
        )
        receitas_grafico = somar_valores(lancamentos_grafico.filter(tipo=Lancamento.TIPO_RECEITA))
        despesas_grafico = somar_valores(lancamentos_grafico.filter(tipo=Lancamento.TIPO_DESPESA))

        meses_labels.append(ABREVIACOES_MESES_PT[inicio_grafico.month])
        receitas_series.append(float(receitas_grafico))
        despesas_series.append(float(despesas_grafico))
        saldo_series.append(float(receitas_grafico - despesas_grafico))

    categorias_chart = [item["categoria__nome"] for item in despesas_por_categoria]
    totais_categorias_chart = [float(item["total"]) for item in despesas_por_categoria]

    cartoes_resumo = (
        CartaoCredito.objects.filter(usuario=request.user, ativo=True)
        .annotate(
            total_fatura=Sum(
                "lancamentos__valor",
                filter=Q(
                    lancamentos__tipo=Lancamento.TIPO_DESPESA,
                    lancamentos__forma_pagamento=Lancamento.FORMA_CREDITO,
                    lancamentos__data_competencia__range=(inicio_mes, fim_mes),
                ),
            )
        )
        .order_by("nome")
    )

    metas = MetaFinanceira.objects.filter(usuario=request.user).order_by("status", "data_limite")[:4]
    metas_concluidas = MetaFinanceira.objects.filter(
        usuario=request.user,
        status=MetaFinanceira.STATUS_CONCLUIDA,
    ).count()

    investimentos = Investimento.objects.filter(usuario=request.user)
    investimentos_ativos = investimentos.filter(status=Investimento.STATUS_ATIVO)
    total_investido = somar_campo(investimentos_ativos, "valor_aplicado")
    total_investimentos_atual = somar_campo(investimentos_ativos, "valor_atual")
    rentabilidade_investimentos = total_investimentos_atual - total_investido
    investimentos_dashboard = investimentos.order_by("status", "-data_aplicacao", "-id")[:4]
    analise_ia_mes = AnaliseFinanceiraIA.objects.filter(
        usuario=request.user,
        periodo_inicio=inicio_mes,
        status=AnaliseFinanceiraIA.STATUS_SUCESSO,
    ).first()

    contexto = {
        "inicio_mes": inicio_mes,
        "fim_mes": fim_mes,
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "lancamentos_recentes": lancamentos_mes[:8],
        "total_receitas": total_receitas,
        "total_despesas": total_despesas,
        "saldo_previsto": saldo_previsto,
        "saldo_mes": saldo_mes,
        "total_pendentes": somar_valores(pendentes),
        "total_atrasados": somar_valores(atrasados),
        "quantidade_pendentes": pendentes.count(),
        "quantidade_atrasados": atrasados.count(),
        "despesas_por_categoria": despesas_por_categoria,
        "cartoes_resumo": cartoes_resumo,
        "chart_labels": meses_labels,
        "chart_receitas": receitas_series,
        "chart_despesas": despesas_series,
        "chart_saldo": saldo_series,
        "categorias_chart": categorias_chart,
        "totais_categorias_chart": totais_categorias_chart,
        "metas_dashboard": metas,
        "quantidade_metas": MetaFinanceira.objects.filter(usuario=request.user).count(),
        "metas_concluidas": metas_concluidas,
        "investimentos_dashboard": investimentos_dashboard,
        "quantidade_investimentos": investimentos.count(),
        "quantidade_investimentos_ativos": investimentos_ativos.count(),
        "total_investido": total_investido,
        "total_investimentos_atual": total_investimentos_atual,
        "rentabilidade_investimentos": rentabilidade_investimentos,
        "plano_usuario": plano_usuario,
        "ia_liberada": usuario_tem_ia_liberada(request.user),
        "analise_ia_mes": analise_ia_mes,
    }
    return render(request, "dashboard.html", contexto)


@login_required
def lista_categorias(request):
    """Lista categorias do usuário."""
    categorias = Categoria.objects.filter(usuario=request.user)
    return render(request, "categorias/lista.html", {"categorias": categorias})


@login_required
def criar_categoria(request):
    """Cria categoria nova."""
    if request.method == "POST":
        form = CategoriaForm(request.POST)
        if form.is_valid():
            categoria = form.save(commit=False)
            categoria.usuario = request.user
            categoria.save()
            criar_notificacao(
                request.user,
                "Categoria Criada",
                f"A categoria {categoria.nome} foi adicionada.",
                Notificacao.TIPO_SUCESSO,
                reverse("lista_categorias"),
            )
            messages.success(request, "Categoria criada com sucesso.")
            return redirect("lista_categorias")
    else:
        form = CategoriaForm()

    return render(request, "categorias/form.html", {"form": form, "titulo": "Nova categoria"})


@login_required
def editar_categoria(request, pk):
    """Edita categoria existente."""
    categoria = obter_objeto_do_usuario(Categoria, request.user, pk)

    if request.method == "POST":
        form = CategoriaForm(request.POST, instance=categoria)
        if form.is_valid():
            categoria = form.save()
            criar_notificacao(
                request.user,
                "Categoria Atualizada",
                f"A categoria {categoria.nome} foi atualizada.",
                Notificacao.TIPO_INFO,
                reverse("lista_categorias"),
            )
            messages.success(request, "Categoria atualizada com sucesso.")
            return redirect("lista_categorias")
    else:
        form = CategoriaForm(instance=categoria)

    return render(request, "categorias/form.html", {"form": form, "titulo": "Editar categoria"})


@login_required
def excluir_categoria(request, pk):
    """Exclui categoria após confirmação."""
    categoria = obter_objeto_do_usuario(Categoria, request.user, pk)

    if request.method == "POST":
        nome_categoria = categoria.nome
        try:
            categoria.delete()
            criar_notificacao(
                request.user,
                "Categoria Excluída",
                f"A categoria {nome_categoria} foi removida.",
                Notificacao.TIPO_ALERTA,
                reverse("lista_categorias"),
            )
            messages.success(request, "Categoria excluída com sucesso.")
        except ProtectedError:
            messages.error(
                request,
                "Esta categoria não pode ser excluída porque já está vinculada a lançamentos.",
            )
        return redirect("lista_categorias")

    return render(request, "confirm_delete.html", {"objeto": categoria, "titulo": "Excluir categoria"})


@login_required
def lista_cartoes(request):
    """Lista cartões cadastrados pelo usuário."""
    cartoes = CartaoCredito.objects.filter(usuario=request.user)
    return render(request, "cartoes/lista.html", {"cartoes": cartoes})


@login_required
def criar_cartao(request):
    """Cria cartão novo."""
    bloqueio = validar_limite_plano(request, "cartoes")
    if bloqueio:
        return bloqueio

    if request.method == "POST":
        form = CartaoCreditoForm(request.POST)
        if form.is_valid():
            cartao = form.save(commit=False)
            cartao.usuario = request.user
            cartao.save()
            criar_notificacao(
                request.user,
                "Cartão Criado",
                f"O cartão {cartao.nome} foi cadastrado.",
                Notificacao.TIPO_SUCESSO,
                reverse("lista_cartoes"),
            )
            messages.success(request, "Cartão cadastrado com sucesso.")
            return redirect("lista_cartoes")
    else:
        form = CartaoCreditoForm()

    return render(request, "cartoes/form.html", {"form": form, "titulo": "Novo cartão"})


@login_required
def editar_cartao(request, pk):
    """Edita cartão existente."""
    cartao = obter_objeto_do_usuario(CartaoCredito, request.user, pk)

    if request.method == "POST":
        form = CartaoCreditoForm(request.POST, instance=cartao)
        if form.is_valid():
            cartao = form.save()
            criar_notificacao(
                request.user,
                "Cartão Atualizado",
                f"O cartão {cartao.nome} foi atualizado.",
                Notificacao.TIPO_INFO,
                reverse("lista_cartoes"),
            )
            messages.success(request, "Cartão atualizado com sucesso.")
            return redirect("lista_cartoes")
    else:
        form = CartaoCreditoForm(instance=cartao)

    return render(request, "cartoes/form.html", {"form": form, "titulo": "Editar cartão"})


@login_required
def excluir_cartao(request, pk):
    """Exclui cartão após confirmação."""
    cartao = obter_objeto_do_usuario(CartaoCredito, request.user, pk)

    if request.method == "POST":
        nome_cartao = cartao.nome
        cartao.delete()
        criar_notificacao(
            request.user,
            "Cartão Excluído",
            f"O cartão {nome_cartao} foi removido.",
            Notificacao.TIPO_ALERTA,
            reverse("lista_cartoes"),
        )
        messages.success(request, "Cartão excluído com sucesso.")
        return redirect("lista_cartoes")

    return render(request, "confirm_delete.html", {"objeto": cartao, "titulo": "Excluir cartão"})


@login_required
def lista_investimentos(request):
    """Lista os investimentos cadastrados pelo usuário, com filtros e dados para gráfico."""
    investimentos = Investimento.objects.filter(usuario=request.user)

    data_inicio = request.GET.get("data_inicio")
    data_fim = request.GET.get("data_fim")
    tipo_filtro = request.GET.get("tipo")

    if data_inicio:
        investimentos = investimentos.filter(data_aplicacao__gte=data_inicio)

    if data_fim:
        investimentos = investimentos.filter(data_aplicacao__lte=data_fim)

    if tipo_filtro:
        investimentos = investimentos.filter(tipo=tipo_filtro)

    investimentos = investimentos.order_by("status", "-data_aplicacao", "-id")
    investimentos_ativos = investimentos.filter(status=Investimento.STATUS_ATIVO)

    total_aplicado = somar_campo(investimentos_ativos, "valor_aplicado")
    total_atual = somar_campo(investimentos_ativos, "valor_atual")
    rentabilidade_total = total_atual - total_aplicado
    mapa_tipos = dict(Investimento.TIPOS)
    resumo_por_tipo = list(
        investimentos_ativos.values("tipo")
        .annotate(total=Sum("valor_atual"), quantidade=Count("id"))
        .order_by("-total")
    )
    for item in resumo_por_tipo:
        item["tipo_label"] = mapa_tipos.get(item["tipo"], item["tipo"])
    grafico_labels = [mapa_tipos.get(item["tipo"], item["tipo"]) for item in resumo_por_tipo]
    grafico_valores = [float(item["total"] or Decimal("0.00")) for item in resumo_por_tipo]

    contexto = {
        "investimentos": investimentos,
        "total_investimentos": investimentos.count(),
        "investimentos_ativos": investimentos_ativos.count(),
        "total_aplicado": total_aplicado,
        "total_atual": total_atual,
        "rentabilidade_total": rentabilidade_total,
        "tipos_investimento": Investimento.TIPOS,
        "tipo_filtro": tipo_filtro,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "resumo_por_tipo": resumo_por_tipo,
        "grafico_labels": json.dumps(grafico_labels),
        "grafico_valores": json.dumps(grafico_valores),
    }
    return render(request, "investimentos/lista.html", contexto)


@login_required
def criar_investimento(request):
    """Cria um novo investimento."""
    bloqueio = validar_limite_plano(request, "investimentos")
    if bloqueio:
        return bloqueio

    if request.method == "POST":
        form = InvestimentoForm(request.POST)
        if form.is_valid():
            investimento = form.save(commit=False)
            investimento.usuario = request.user
            investimento.save()
            criar_notificacao(
                request.user,
                "Investimento Registrado",
                f"Você adicionou {investimento.nome} à sua carteira.",
                Notificacao.TIPO_SUCESSO,
                reverse("lista_investimentos"),
            )
            messages.success(request, "Investimento cadastrado com sucesso.")
            return redirect("lista_investimentos")
    else:
        form = InvestimentoForm()

    return render(request, "investimentos/form.html", {"form": form, "titulo": "Novo investimento"})


@login_required
def editar_investimento(request, pk):
    """Edita um investimento existente."""
    investimento = obter_objeto_do_usuario(Investimento, request.user, pk)

    if request.method == "POST":
        form = InvestimentoForm(request.POST, instance=investimento)
        if form.is_valid():
            investimento = form.save()
            criar_notificacao(
                request.user,
                "Investimento Atualizado",
                f"As informações de {investimento.nome} foram atualizadas.",
                Notificacao.TIPO_INFO,
                reverse("lista_investimentos"),
            )
            messages.success(request, "Investimento atualizado com sucesso.")
            return redirect("lista_investimentos")
    else:
        form = InvestimentoForm(instance=investimento)

    return render(
        request,
        "investimentos/form.html",
        {"form": form, "titulo": "Editar investimento", "investimento": investimento},
    )


@login_required
def excluir_investimento(request, pk):
    """Exclui um investimento após confirmação."""
    investimento = obter_objeto_do_usuario(Investimento, request.user, pk)

    if request.method == "POST":
        nome_investimento = investimento.nome
        investimento.delete()
        criar_notificacao(
            request.user,
            "Investimento Excluído",
            f"O investimento {nome_investimento} foi removido.",
            Notificacao.TIPO_ALERTA,
            reverse("lista_investimentos"),
        )
        messages.success(request, "Investimento excluído com sucesso.")
        return redirect("lista_investimentos")

    return render(
        request,
        "confirm_delete.html",
        {"objeto": investimento, "titulo": "Excluir investimento"},
    )


@login_required
def configuracoes(request):
    """Centraliza perfil, preferências e segurança da conta."""
    configuracao, _ = ConfiguracaoUsuario.objects.get_or_create(usuario=request.user)
    plano_usuario = obter_plano_usuario(request.user)
    ultima_analise_ia = AnaliseFinanceiraIA.objects.filter(usuario=request.user).first()
    eventos_assinatura = EventoAssinatura.objects.filter(usuario=request.user)[:8]

    perfil_form = PerfilUsuarioForm(instance=request.user, prefix="perfil")
    preferencias_form = ConfiguracaoUsuarioForm(instance=configuracao, prefix="preferencias")
    senha_form = AlterarSenhaPerfilForm(request.user, prefix="senha")
    excluir_conta_form = ExcluirContaForm(request.user, prefix="excluir")

    if request.method == "POST":
        if "salvar_perfil" in request.POST:
            perfil_form = PerfilUsuarioForm(request.POST, instance=request.user, prefix="perfil")
            if perfil_form.is_valid():
                perfil_form.save()
                messages.success(request, "Seus dados de perfil foram atualizados com sucesso.")
                return redirect("configuracoes")
            messages.error(request, "Revise os dados do perfil para continuar.")

        elif "salvar_preferencias" in request.POST:
            preferencias_form = ConfiguracaoUsuarioForm(
                request.POST,
                instance=configuracao,
                prefix="preferencias",
            )
            if preferencias_form.is_valid():
                preferencias_form.save()
                messages.success(request, "Suas preferências foram salvas com sucesso.")
                return redirect("configuracoes")
            messages.error(request, "Revise as preferências informadas para continuar.")

        elif "alterar_senha" in request.POST:
            senha_form = AlterarSenhaPerfilForm(request.user, request.POST, prefix="senha")
            if senha_form.is_valid():
                usuario = senha_form.save()
                update_session_auth_hash(request, usuario)
                messages.success(request, "Sua senha foi alterada com sucesso.")
                return redirect("configuracoes")
            messages.error(request, "Não foi possível alterar a senha. Revise os campos informados.")

    contexto = {
        "perfil_form": perfil_form,
        "preferencias_form": preferencias_form,
        "senha_form": senha_form,
        "excluir_conta_form": excluir_conta_form,
        "configuracao_usuario": configuracao,
        "plano_usuario": plano_usuario,
        "ultima_analise_ia": ultima_analise_ia,
        "eventos_assinatura": eventos_assinatura,
    }
    return render(request, "configuracoes/painel.html", contexto)


@login_required
def analise_ia(request):
    """Tela premium de analise financeira com IA e historico salvo."""
    plano_usuario = obter_plano_usuario(request.user)
    analises_recentes = AnaliseFinanceiraIA.objects.filter(usuario=request.user)[:6]
    analise_atual = analises_recentes[0] if analises_recentes else None

    valor_inicial_mes = date.today().replace(day=1).strftime("%Y-%m")
    form = AnaliseFinanceiraIAForm(initial={"mes_referencia": valor_inicial_mes})

    if request.method == "POST":
        form = AnaliseFinanceiraIAForm(request.POST)

        bloqueio = bloquear_recurso_premium(request, "ia", "configuracoes")
        if bloqueio:
            return bloqueio

        if atingiu_limite_temporario(request, "analise_ia", limite=10, janela_segundos=3600):
            messages.warning(request, "Limite temporario de analises atingido. Tente novamente em alguns minutos.")
            return redirect("analise_ia")

        if form.is_valid():
            mes_referencia = form.cleaned_data["mes_referencia"]
            inicio_mes = mes_referencia.replace(day=1)
            fim_mes = date(
                inicio_mes.year,
                inicio_mes.month,
                monthrange(inicio_mes.year, inicio_mes.month)[1],
            )
            forcar_regeneracao = form.cleaned_data["forcar_regeneracao"]

            analise_salva = AnaliseFinanceiraIA.objects.filter(
                usuario=request.user,
                periodo_inicio=inicio_mes,
                status=AnaliseFinanceiraIA.STATUS_SUCESSO,
            ).first()

            if analise_salva and not forcar_regeneracao:
                messages.info(
                    request,
                    "Ja existe uma analise salva para esse mes. Marque a opcao de nova geracao se quiser atualizar.",
                )
                analise_atual = analise_salva
            else:
                contexto_ia = construir_contexto_analise_financeira(request.user, inicio_mes, fim_mes)

                try:
                    analise_estruturada, resposta_bruta = chamar_openai_analise_financeira(contexto_ia)
                except OpenAIFinanceiroErro as erro:
                    AnaliseFinanceiraIA.objects.create(
                        usuario=request.user,
                        periodo_inicio=inicio_mes,
                        periodo_fim=fim_mes,
                        status=AnaliseFinanceiraIA.STATUS_ERRO,
                        modelo=settings.OPENAI_MODEL,
                        contexto_enviado=contexto_ia,
                        mensagem_erro=str(erro),
                    )
                    messages.error(request, str(erro))
                    return redirect("analise_ia")

                analise_atual = AnaliseFinanceiraIA.objects.create(
                    usuario=request.user,
                    periodo_inicio=inicio_mes,
                    periodo_fim=fim_mes,
                    status=AnaliseFinanceiraIA.STATUS_SUCESSO,
                    modelo=settings.OPENAI_MODEL,
                    saude_financeira=analise_estruturada["saude_financeira"],
                    resumo_executivo=analise_estruturada["resumo_executivo"],
                    contexto_enviado=contexto_ia,
                    metricas_resumo=analise_estruturada["metricas_resumo"],
                    sinais_positivos=analise_estruturada["sinais_positivos"],
                    alertas_prioritarios=analise_estruturada["alertas_prioritarios"],
                    oportunidades=analise_estruturada["oportunidades"],
                    plano_acao=analise_estruturada["plano_acao"],
                    resposta_bruta=resposta_bruta,
                )
                messages.success(request, "Analise IA gerada com sucesso.")

            analises_recentes = AnaliseFinanceiraIA.objects.filter(usuario=request.user)[:6]

    contexto = {
        "form": form,
        "plano_usuario": plano_usuario,
        "ia_liberada": usuario_tem_ia_liberada(request.user),
        "analise_atual": analise_atual,
        "analises_recentes": analises_recentes,
        "objetivos_ia": OBJETIVO_ANALISE_IA,
        "regras_ia": REGRAS_ANALISE_IA,
    }
    return render(request, "ia/analise.html", contexto)


@login_required
@require_POST
def iniciar_checkout_premium(request):
    """Cria uma assinatura recorrente no Mercado Pago e redireciona para o checkout."""
    plano_usuario = obter_plano_usuario(request.user)

    if plano_usuario.eh_premium and plano_usuario.mercado_pago_preapproval_id:
        messages.info(request, "Sua conta ja possui um plano premium em andamento.")
        return redirect("configuracoes")

    if (
        plano_usuario.mercado_pago_preapproval_id
        and plano_usuario.mercado_pago_checkout_url
        and plano_usuario.mercado_pago_status in {"pending", "authorized", "active"}
    ):
        return redirect(plano_usuario.mercado_pago_checkout_url)

    if not request.user.email:
        messages.error(request, "Cadastre um e-mail valido no perfil antes de iniciar a assinatura premium.")
        return redirect("configuracoes")

    if atingiu_limite_temporario(request, "checkout_premium", limite=5, janela_segundos=600):
        messages.warning(request, "Muitas tentativas de checkout em pouco tempo. Aguarde alguns minutos e tente novamente.")
        return redirect("configuracoes")

    referencia_externa = f"finanpy-premium-user-{request.user.id}"

    try:
        cliente = MercadoPagoClient()
        resposta = cliente.criar_assinatura_premium(request.user, referencia_externa)
    except MercadoPagoErro as erro:
        logger.warning("Falha ao iniciar checkout premium para usuario %s: %s", request.user.id, erro)
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_USUARIO,
            usuario=request.user,
            plano=plano_usuario,
            referencia_externa=referencia_externa,
            valor=Decimal("10.50"),
            mensagem=f"Falha ao iniciar checkout premium: {erro}",
        )
        messages.error(request, str(erro))
        return redirect("configuracoes")

    plano_usuario.mercado_pago_preapproval_id = resposta.identificador
    plano_usuario.mercado_pago_checkout_url = resposta.init_point
    plano_usuario.mercado_pago_status = resposta.status
    plano_usuario.mercado_pago_referencia_externa = resposta.referencia_externa
    plano_usuario.ultima_sincronizacao_gateway = timezone.now()
    plano_usuario.save()
    registrar_evento_assinatura(
        tipo=EventoAssinatura.TIPO_CHECKOUT_CRIADO,
        origem=EventoAssinatura.ORIGEM_USUARIO,
        usuario=request.user,
        plano=plano_usuario,
        mercado_pago_preapproval_id=resposta.identificador,
        status_gateway=resposta.status,
        referencia_externa=resposta.referencia_externa,
        valor=Decimal("10.50"),
        mensagem="Checkout premium criado e usuario redirecionado ao Mercado Pago.",
        payload={"init_point": resposta.init_point},
    )

    return redirect(resposta.init_point)


@login_required
@require_POST
def checkout_premium(request):
    """Rota semantica para abrir o checkout premium a partir de Configuracoes."""
    return iniciar_checkout_premium(request)


@login_required
def retorno_assinatura(request):
    """Recebe o usuario de volta do Mercado Pago e sincroniza o status da assinatura."""
    plano_usuario = obter_plano_usuario(request.user)

    if not plano_usuario.mercado_pago_preapproval_id:
        messages.warning(request, "Nenhuma assinatura premium foi encontrada para sincronizar.")
        return redirect("configuracoes")

    try:
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
            plano=plano_usuario,
            mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
            status_gateway=plano_usuario.mercado_pago_status,
            referencia_externa=plano_usuario.mercado_pago_referencia_externa,
            mensagem=f"Falha ao sincronizar retorno da assinatura: {erro}",
        )
        messages.error(request, str(erro))
        return redirect("configuracoes")

    if plano_usuario.eh_premium:
        messages.success(request, "Pagamento confirmado. Seu plano premium esta ativo.")
    else:
        messages.warning(
            request,
            "A assinatura ainda nao foi concluida no gateway. Assim que o pagamento for confirmado, o premium sera liberado.",
        )

    return redirect("configuracoes")


@login_required
@require_POST
def sincronizar_assinatura_premium(request):
    """Sincroniza manualmente o status da assinatura premium com o gateway."""
    plano_usuario = obter_plano_usuario(request.user)

    if not plano_usuario.mercado_pago_preapproval_id:
        messages.warning(request, "Nenhuma assinatura premium foi encontrada para sincronizar.")
        return redirect("configuracoes")

    try:
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_USUARIO,
            plano=plano_usuario,
            mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
            status_gateway=plano_usuario.mercado_pago_status,
            referencia_externa=plano_usuario.mercado_pago_referencia_externa,
            mensagem=f"Falha ao sincronizar assinatura manualmente: {erro}",
        )
        messages.error(request, str(erro))
        return redirect("configuracoes")

    messages.success(request, "Status da assinatura sincronizado com sucesso.")
    return redirect("configuracoes")


@login_required
@require_POST
def cancelar_assinatura_premium(request):
    """Cancela a assinatura premium no Mercado Pago e atualiza o plano local."""
    plano_usuario = obter_plano_usuario(request.user)

    if not plano_usuario.mercado_pago_preapproval_id:
        messages.warning(request, "Nao existe assinatura premium ativa para cancelar.")
        return redirect("configuracoes")

    try:
        cliente = MercadoPagoClient()
        cliente.cancelar_assinatura(plano_usuario.mercado_pago_preapproval_id)
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_USUARIO,
            plano=plano_usuario,
            mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
            status_gateway=plano_usuario.mercado_pago_status,
            referencia_externa=plano_usuario.mercado_pago_referencia_externa,
            mensagem=f"Falha ao cancelar assinatura premium: {erro}",
        )
        messages.error(request, str(erro))
        return redirect("configuracoes")

    messages.success(request, "Assinatura premium cancelada com sucesso.")
    return redirect("configuracoes")


@csrf_exempt
def webhook_mercado_pago(request):
    """Recebe notificacoes do Mercado Pago e sincroniza o plano automaticamente."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    data_id = (
        request.GET.get("data.id")
        or request.GET.get("id")
        or str(payload.get("data", {}).get("id", ""))
    )
    tipo = request.GET.get("type") or payload.get("type") or ""
    acao = payload.get("action", "")
    request_id = request.headers.get("x-request-id", "")
    evento_id = str(payload.get("id") or request.GET.get("id") or request_id or "")
    tipos_assinatura = {"subscription_preapproval", "preapproval"}
    tipos_pagamento = {"payment", "payments"}

    if not validar_assinatura_webhook_mercado_pago(request, data_id):
        return JsonResponse({"ok": False, "erro": "assinatura_invalida"}, status=400)

    plano_usuario = None
    pagamento_payload = {}

    if data_id and tipo in tipos_assinatura:
        plano_usuario = PlanoUsuario.objects.filter(mercado_pago_preapproval_id=data_id).first()
    elif data_id and tipo in tipos_pagamento:
        try:
            pagamento_payload = MercadoPagoClient().buscar_pagamento(data_id)
        except MercadoPagoErro as erro:
            registrar_evento_assinatura(
                tipo=EventoAssinatura.TIPO_ERRO,
                origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
                mercado_pago_evento_id=evento_id,
                mercado_pago_tipo=tipo,
                mercado_pago_acao=acao,
                mensagem=f"Falha ao consultar pagamento Mercado Pago: {erro}",
                payload=payload,
            )
            return JsonResponse({"ok": False, "erro": str(erro)}, status=500)

        metadata = pagamento_payload.get("metadata") or {}
        referencia_pagamento = str(pagamento_payload.get("external_reference") or metadata.get("external_reference") or "")
        preapproval_pagamento = str(
            pagamento_payload.get("preapproval_id")
            or metadata.get("preapproval_id")
            or metadata.get("subscription_id")
            or ""
        )

        plano_usuario = (
            PlanoUsuario.objects.filter(mercado_pago_preapproval_id=preapproval_pagamento).first()
            if preapproval_pagamento
            else None
        )
        if not plano_usuario and referencia_pagamento:
            plano_usuario = PlanoUsuario.objects.filter(
                mercado_pago_referencia_externa=referencia_pagamento
            ).first()
    registrar_evento_assinatura(
        tipo=EventoAssinatura.TIPO_WEBHOOK_RECEBIDO,
        origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
        plano=plano_usuario,
        mercado_pago_preapproval_id=data_id if tipo in tipos_assinatura else getattr(plano_usuario, "mercado_pago_preapproval_id", ""),
        mercado_pago_evento_id=evento_id,
        mercado_pago_tipo=tipo,
        mercado_pago_acao=acao,
        status_gateway=plano_usuario.mercado_pago_status if plano_usuario else "",
        referencia_externa=plano_usuario.mercado_pago_referencia_externa if plano_usuario else "",
        mensagem="Webhook recebido e validado pelo FinanPy.",
        payload={
            "query_params": request.GET.dict(),
            "body": payload,
            "pagamento": pagamento_payload,
            "x_request_id": request_id,
        },
    )

    if tipo in tipos_pagamento and plano_usuario:
        status_pagamento = str(pagamento_payload.get("status") or "")
        if status_pagamento in {"approved", "authorized"}:
            plano_usuario.mercado_pago_status = "authorized"
            plano_usuario.ultima_sincronizacao_gateway = timezone.now()
            plano_usuario.ativar_premium()
            plano_usuario.save()
            registrar_evento_assinatura(
                tipo=EventoAssinatura.TIPO_PREMIUM_ATIVADO,
                origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
                plano=plano_usuario,
                mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
                mercado_pago_evento_id=evento_id,
                mercado_pago_tipo=tipo,
                mercado_pago_acao=acao,
                status_gateway=status_pagamento,
                referencia_externa=plano_usuario.mercado_pago_referencia_externa,
                valor=plano_usuario.valor_mensal,
                mensagem="Pagamento recebido e Premium ativado automaticamente.",
                payload=pagamento_payload,
            )
            criar_notificacao(
                plano_usuario.usuario,
                "Pagamento Recebido",
                "Seu Plano Premium está ativo. Todos os recursos foram liberados.",
                Notificacao.TIPO_SUCESSO,
                reverse("configuracoes"),
            )
        return JsonResponse(
            {
                "ok": True,
                "tipo": tipo,
                "acao": acao,
                "pagamento": data_id,
                "status_pagamento": status_pagamento,
                "plano": plano_usuario.nome_plano,
            },
            status=200,
        )

    if tipo not in tipos_assinatura:
        return JsonResponse(
            {"ok": True, "ignorado": True, "tipo": tipo, "acao": acao},
            status=200,
        )

    if not plano_usuario:
        return JsonResponse(
            {"ok": True, "ignorado": True, "motivo": "assinatura_nao_encontrada", "data_id": data_id},
            status=200,
        )

    try:
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
            plano=plano_usuario,
            mercado_pago_preapproval_id=data_id,
            mercado_pago_evento_id=evento_id,
            mercado_pago_tipo=tipo,
            mercado_pago_acao=acao,
            status_gateway=plano_usuario.mercado_pago_status,
            referencia_externa=plano_usuario.mercado_pago_referencia_externa,
            mensagem=f"Falha ao processar webhook Mercado Pago: {erro}",
            payload=payload,
        )
        return JsonResponse({"ok": False, "erro": str(erro)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "tipo": tipo,
            "acao": acao,
            "assinatura": data_id,
            "plano": plano_usuario.nome_plano,
            "status_gateway": plano_usuario.mercado_pago_status,
        },
        status=200,
    )


@login_required
def lista_metas(request):
    """Lista todas as metas financeiras do usuário."""
    metas = list(MetaFinanceira.objects.filter(usuario=request.user).order_by("status", "data_limite"))
    metas_inteligentes = calcular_metas_inteligentes(request.user, metas)

    for meta in metas:
        meta.inteligencia = metas_inteligentes.get(meta.id)

    contexto = {
        "metas": metas,
        "total_metas": len(metas),
        "metas_concluidas": sum(1 for meta in metas if meta.status == MetaFinanceira.STATUS_CONCLUIDA),
        "metas_em_andamento": sum(1 for meta in metas if meta.status == MetaFinanceira.STATUS_EM_ANDAMENTO),
    }
    return render(request, "metas/lista.html", contexto)


@login_required
def criar_meta(request):
    """Cria uma nova meta financeira."""
    bloqueio = validar_limite_plano(request, "metas")
    if bloqueio:
        return bloqueio

    if request.method == "POST":
        form = MetaFinanceiraForm(request.POST)
        if form.is_valid():
            meta = form.save(commit=False)
            meta.usuario = request.user
            meta.save()
            criar_notificacao(
                request.user,
                "Meta Criada",
                f"A meta {meta.titulo} foi criada com estratégia {meta.get_estrategia_display()}.",
                Notificacao.TIPO_SUCESSO,
                reverse("lista_metas"),
            )
            messages.success(request, "Meta criada com sucesso.")
            return redirect("lista_metas")
    else:
        form = MetaFinanceiraForm()

    return render(request, "metas/form.html", {"form": form, "titulo": "Nova meta"})


@login_required
def editar_meta(request, pk):
    """Edita uma meta existente."""
    meta = obter_objeto_do_usuario(MetaFinanceira, request.user, pk)

    if request.method == "POST":
        form = MetaFinanceiraForm(request.POST, instance=meta)
        if form.is_valid():
            meta = form.save()
            criar_notificacao(
                request.user,
                "Meta Atualizada",
                f"A meta {meta.titulo} foi atualizada.",
                Notificacao.TIPO_INFO,
                reverse("lista_metas"),
            )
            messages.success(request, "Meta atualizada com sucesso.")
            return redirect("lista_metas")
    else:
        form = MetaFinanceiraForm(instance=meta)

    return render(request, "metas/form.html", {"form": form, "titulo": "Editar meta", "meta": meta})


@login_required
def excluir_meta(request, pk):
    """Exclui uma meta após confirmação."""
    meta = obter_objeto_do_usuario(MetaFinanceira, request.user, pk)

    if request.method == "POST":
        titulo_meta = meta.titulo
        meta.delete()
        criar_notificacao(
            request.user,
            "Meta Excluída",
            f"A meta {titulo_meta} foi removida.",
            Notificacao.TIPO_ALERTA,
            reverse("lista_metas"),
        )
        messages.success(request, "Meta excluída com sucesso.")
        return redirect("lista_metas")

    return render(request, "confirm_delete.html", {"objeto": meta, "titulo": "Excluir meta"})


@login_required
@require_POST
def concluir_meta(request, pk):
    """Marca uma meta como alcançada pelo usuário."""
    meta = obter_objeto_do_usuario(MetaFinanceira, request.user, pk)
    meta.valor_atual = meta.valor_alvo
    meta.status = MetaFinanceira.STATUS_CONCLUIDA
    meta.save()
    criar_notificacao(
        request.user,
        "Meta Alcançada",
        f"Parabéns! A meta {meta.titulo} foi marcada como alcançada.",
        Notificacao.TIPO_SUCESSO,
        reverse("lista_metas"),
    )
    messages.success(request, "Meta marcada como alcançada. Parabéns pela conquista!")
    return redirect("lista_metas")


def aplicar_filtros_lancamentos(queryset, request):
    """Filtra por mês, tipo, categoria e status."""
    inicio_mes, fim_mes = obter_mes_referencia(request)
    queryset = queryset.filter(data_competencia__range=(inicio_mes, fim_mes))

    tipo = request.GET.get("tipo")
    categoria_id = request.GET.get("categoria")
    status = request.GET.get("status")

    if tipo:
        queryset = queryset.filter(tipo=tipo)

    if categoria_id:
        queryset = queryset.filter(categoria_id=categoria_id)

    if status:
        queryset = queryset.filter(status=status)

    return queryset, inicio_mes


@login_required
def lista_lancamentos(request):
    """Lista geral de lançamentos com filtros."""
    queryset = Lancamento.objects.filter(usuario=request.user).select_related("categoria", "cartao", "orcamento_compartilhado")
    lancamentos, inicio_mes = aplicar_filtros_lancamentos(queryset, request)

    contexto = {
        "titulo": "Todos os lançamentos",
        "lancamentos": lancamentos,
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "categorias": Categoria.objects.filter(usuario=request.user),
        "tipos": Lancamento.TIPOS,
        "status_opcoes": Lancamento.STATUS_CHOICES,
    }
    return render(request, "lancamentos/lista.html", contexto)


@login_required
def lista_receitas(request):
    """Lista apenas receitas."""
    queryset = Lancamento.objects.filter(
        usuario=request.user,
        tipo=Lancamento.TIPO_RECEITA,
    ).select_related("categoria", "cartao", "orcamento_compartilhado")
    lancamentos, inicio_mes = aplicar_filtros_lancamentos(queryset, request)

    contexto = {
        "titulo": "Receitas",
        "lancamentos": lancamentos,
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "categorias": Categoria.objects.filter(usuario=request.user, tipo=Categoria.TIPO_RECEITA),
        "tipos": Lancamento.TIPOS,
        "status_opcoes": Lancamento.STATUS_CHOICES,
    }
    return render(request, "lancamentos/lista.html", contexto)


@login_required
def lista_despesas(request):
    """Lista apenas despesas."""
    queryset = Lancamento.objects.filter(
        usuario=request.user,
        tipo=Lancamento.TIPO_DESPESA,
    ).select_related("categoria", "cartao", "orcamento_compartilhado")
    lancamentos, inicio_mes = aplicar_filtros_lancamentos(queryset, request)

    contexto = {
        "titulo": "Despesas",
        "lancamentos": lancamentos,
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "categorias": Categoria.objects.filter(usuario=request.user, tipo=Categoria.TIPO_DESPESA),
        "tipos": Lancamento.TIPOS,
        "status_opcoes": Lancamento.STATUS_CHOICES,
    }
    return render(request, "lancamentos/lista.html", contexto)


@login_required
def contas_pagas(request):
    """Lista lançamentos pagos do mês filtrado."""
    queryset = Lancamento.objects.filter(
        usuario=request.user,
        status=Lancamento.STATUS_PAGO,
    ).select_related("categoria", "cartao", "orcamento_compartilhado")
    lancamentos, inicio_mes = aplicar_filtros_lancamentos(queryset, request)

    contexto = {
        "titulo": "Contas pagas",
        "lancamentos": lancamentos,
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "categorias": Categoria.objects.filter(usuario=request.user),
        "tipos": Lancamento.TIPOS,
        "status_opcoes": Lancamento.STATUS_CHOICES,
    }
    return render(request, "lancamentos/lista.html", contexto)


@login_required
def contas_pendentes(request):
    """Lista lançamentos pendentes e atrasados do mês filtrado."""
    queryset = Lancamento.objects.filter(usuario=request.user).filter(
        Q(status=Lancamento.STATUS_PENDENTE) | Q(status=Lancamento.STATUS_ATRASADO)
    ).select_related("categoria", "cartao", "orcamento_compartilhado")
    lancamentos, inicio_mes = aplicar_filtros_lancamentos(queryset, request)

    contexto = {
        "titulo": "Contas pendentes",
        "lancamentos": lancamentos,
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "categorias": Categoria.objects.filter(usuario=request.user),
        "tipos": Lancamento.TIPOS,
        "status_opcoes": Lancamento.STATUS_CHOICES,
    }
    return render(request, "lancamentos/lista.html", contexto)


def criar_parcelas(lancamento_base):
    """Cria várias despesas parceladas a partir de um lançamento base."""
    grupo = lancamento_base.grupo_parcelas or uuid.uuid4()

    for numero_parcela in range(1, lancamento_base.total_parcelas + 1):
        data_competencia = adicionar_meses(lancamento_base.data_competencia, numero_parcela - 1)
        data_vencimento = adicionar_meses(lancamento_base.data_vencimento, numero_parcela - 1)

        Lancamento.objects.create(
            usuario=lancamento_base.usuario,
            tipo=lancamento_base.tipo,
            escopo=lancamento_base.escopo,
            orcamento_compartilhado=lancamento_base.orcamento_compartilhado,
            descricao=lancamento_base.descricao,
            valor=lancamento_base.valor,
            categoria=lancamento_base.categoria,
            data_competencia=data_competencia,
            data_vencimento=data_vencimento,
            data_pagamento=lancamento_base.data_pagamento,
            status=lancamento_base.status,
            forma_pagamento=lancamento_base.forma_pagamento,
            observacao=lancamento_base.observacao,
            cartao=lancamento_base.cartao,
            compra_parcelada=True,
            parcela_atual=numero_parcela,
            total_parcelas=lancamento_base.total_parcelas,
            grupo_parcelas=grupo,
        )


@login_required
def criar_lancamento(request):
    """Cria receitas e despesas, inclusive parceladas."""
    familia_liberada = usuario_tem_modo_familia_liberado(request.user)

    if request.method == "POST":
        form = LancamentoForm(request.POST, usuario=request.user, familia_liberada=familia_liberada)
        if form.is_valid():
            lancamento = form.save(commit=False)
            lancamento.usuario = request.user

            if lancamento.compra_parcelada and lancamento.total_parcelas > 1:
                lancamento.grupo_parcelas = uuid.uuid4()
                criar_parcelas(lancamento)
                criar_notificacao(
                    request.user,
                    "Compra Parcelada Registrada",
                    f"{lancamento.total_parcelas} parcelas de {lancamento.descricao} foram cadastradas.",
                    Notificacao.TIPO_SUCESSO,
                    reverse("lista_lancamentos"),
                )
                messages.success(request, "Compra parcelada cadastrada com sucesso.")
            else:
                lancamento.save()
                criar_notificacao(
                    request.user,
                    "Transação Registrada",
                    f"{lancamento.descricao_completa} foi adicionada aos seus lançamentos.",
                    Notificacao.TIPO_SUCESSO,
                    reverse("lista_lancamentos"),
                )
                messages.success(request, "Lançamento criado com sucesso.")

            return redirect("lista_lancamentos")
        messages.error(request, "Não foi possível salvar a transação. Revise os campos destacados.")
    else:
        form = LancamentoForm(usuario=request.user, familia_liberada=familia_liberada)

    return render(request, "lancamentos/form.html", {"form": form, "titulo": "Novo lançamento"})


@login_required
def editar_lancamento(request, pk):
    """Edita um lançamento existente."""
    lancamento = obter_objeto_do_usuario(Lancamento, request.user, pk)
    familia_liberada = usuario_tem_modo_familia_liberado(request.user)

    if request.method == "POST":
        form = LancamentoForm(
            request.POST,
            instance=lancamento,
            usuario=request.user,
            familia_liberada=familia_liberada,
        )
        if form.is_valid():
            lancamento_editado = form.save(commit=False)
            lancamento_editado.usuario = request.user

            # Para manter a regra simples, a edição atualiza somente a parcela aberta.
            if lancamento.total_parcelas > 1:
                lancamento_editado.compra_parcelada = True

            lancamento_editado.save()
            criar_notificacao(
                request.user,
                "Transação Atualizada",
                f"{lancamento_editado.descricao_completa} foi atualizada.",
                Notificacao.TIPO_INFO,
                reverse("lista_lancamentos"),
            )
            messages.success(request, "Lançamento atualizado com sucesso.")
            return redirect("lista_lancamentos")
        messages.error(request, "Não foi possível atualizar a transação. Revise os campos destacados.")
    else:
        form = LancamentoForm(instance=lancamento, usuario=request.user, familia_liberada=familia_liberada)

    return render(
        request,
        "lancamentos/form.html",
        {"form": form, "titulo": "Editar lançamento", "lancamento": lancamento},
    )


def serializar_lancamento_para_restauracao(lancamento):
    """Transforma um lançamento em dados seguros para restaurar por até 5 segundos."""
    return {
        "tipo": lancamento.tipo,
        "escopo": lancamento.escopo,
        "orcamento_compartilhado_id": lancamento.orcamento_compartilhado_id,
        "descricao": lancamento.descricao,
        "valor": str(lancamento.valor),
        "categoria_id": lancamento.categoria_id,
        "data_competencia": lancamento.data_competencia.isoformat(),
        "data_vencimento": lancamento.data_vencimento.isoformat(),
        "data_pagamento": lancamento.data_pagamento.isoformat() if lancamento.data_pagamento else None,
        "status": lancamento.status,
        "forma_pagamento": lancamento.forma_pagamento,
        "observacao": lancamento.observacao,
        "cartao_id": lancamento.cartao_id,
        "compra_parcelada": lancamento.compra_parcelada,
        "parcela_atual": lancamento.parcela_atual,
        "total_parcelas": lancamento.total_parcelas,
        "grupo_parcelas": str(lancamento.grupo_parcelas) if lancamento.grupo_parcelas else None,
    }


def guardar_lancamentos_para_restauracao(request, lancamentos):
    """Salva uma cópia temporária na sessão para o botão Refazer."""
    request.session["undo_lancamentos"] = {
        "expira_em": (timezone.now() + timedelta(seconds=5)).isoformat(),
        "itens": [serializar_lancamento_para_restauracao(lancamento) for lancamento in lancamentos],
    }
    request.session.modified = True


@login_required
def excluir_lancamento(request, pk):
    """Exclui um lançamento ou uma parcela específica."""
    lancamento = obter_objeto_do_usuario(Lancamento, request.user, pk)

    if request.method == "POST":
        guardar_lancamentos_para_restauracao(request, [lancamento])
        descricao = lancamento.descricao_completa
        lancamento.delete()
        criar_notificacao(
            request.user,
            "Transação Excluída",
            f"{descricao} foi removida. Você teve 5 segundos para restaurar pela tela de lançamentos.",
            Notificacao.TIPO_ALERTA,
            reverse("lista_lancamentos"),
        )
        messages.success(request, "Lançamento excluído. Você pode clicar em Refazer por 5 segundos.")
        return redirect(f"{reverse('lista_lancamentos')}?undo=1")

    return render(request, "confirm_delete.html", {"objeto": lancamento, "titulo": "Excluir lançamento"})


@login_required
@require_POST
def excluir_lancamentos_selecionados(request):
    """Exclui várias transações de uma vez e permite desfazer por 5 segundos."""
    ids = request.POST.getlist("lancamentos")
    apagar_tudo = request.POST.get("apagar_tudo") == "1"

    queryset = Lancamento.objects.filter(usuario=request.user)

    if apagar_tudo:
        lancamentos = list(queryset)
    elif ids:
        lancamentos = list(queryset.filter(id__in=ids))
    else:
        messages.warning(request, "Selecione pelo menos uma transação para excluir.")
        return redirect("lista_lancamentos")

    if not lancamentos:
        messages.warning(request, "Nenhuma transação foi encontrada para exclusão.")
        return redirect("lista_lancamentos")

    guardar_lancamentos_para_restauracao(request, lancamentos)
    quantidade = len(lancamentos)
    Lancamento.objects.filter(id__in=[item.id for item in lancamentos], usuario=request.user).delete()
    criar_notificacao(
        request.user,
        "Transações Excluídas",
        f"{quantidade} transação(ões) foram removidas.",
        Notificacao.TIPO_ALERTA,
        reverse("lista_lancamentos"),
    )
    messages.success(request, f"{quantidade} transação(ões) excluída(s). Clique em Refazer em até 5 segundos.")
    return redirect(f"{reverse('lista_lancamentos')}?undo=1")


@login_required
@require_POST
def restaurar_lancamentos(request):
    """Restaura lançamentos apagados se a janela de 5 segundos ainda estiver ativa."""
    pacote = request.session.get("undo_lancamentos")

    if not pacote:
        messages.warning(request, "Não existe exclusão recente para refazer.")
        return redirect("lista_lancamentos")

    expira_em = timezone.datetime.fromisoformat(pacote["expira_em"])
    if timezone.is_naive(expira_em):
        expira_em = timezone.make_aware(expira_em, timezone.get_current_timezone())

    if timezone.now() > expira_em:
        request.session.pop("undo_lancamentos", None)
        messages.warning(request, "O prazo de 5 segundos terminou. A exclusão já é definitiva.")
        return redirect("lista_lancamentos")

    restaurados = 0
    for item in pacote.get("itens", []):
        categoria = Categoria.objects.filter(id=item["categoria_id"], usuario=request.user).first()
        if not categoria:
            continue

        Lancamento.objects.create(
            usuario=request.user,
            tipo=item["tipo"],
            escopo=item["escopo"],
            orcamento_compartilhado_id=item["orcamento_compartilhado_id"],
            descricao=item["descricao"],
            valor=Decimal(item["valor"]),
            categoria=categoria,
            data_competencia=date.fromisoformat(item["data_competencia"]),
            data_vencimento=date.fromisoformat(item["data_vencimento"]),
            data_pagamento=date.fromisoformat(item["data_pagamento"]) if item["data_pagamento"] else None,
            status=item["status"],
            forma_pagamento=item["forma_pagamento"],
            observacao=item["observacao"],
            cartao_id=item["cartao_id"],
            compra_parcelada=item["compra_parcelada"],
            parcela_atual=item["parcela_atual"],
            total_parcelas=item["total_parcelas"],
            grupo_parcelas=uuid.UUID(item["grupo_parcelas"]) if item["grupo_parcelas"] else None,
        )
        restaurados += 1

    request.session.pop("undo_lancamentos", None)
    criar_notificacao(
        request.user,
        "Transações Restauradas",
        f"{restaurados} transação(ões) foram restauradas.",
        Notificacao.TIPO_SUCESSO,
        reverse("lista_lancamentos"),
    )
    messages.success(request, f"{restaurados} transação(ões) restaurada(s) com sucesso.")
    return redirect("lista_lancamentos")


@login_required
def relatorios(request):
    """Exibe visões resumidas por categoria e diagnóstico financeiro por IA."""
    inicio_mes, fim_mes = obter_mes_referencia(request)
    plano_usuario = obter_plano_usuario(request.user)
    ia_liberada = usuario_tem_ia_liberada(request.user)
    lancamentos = Lancamento.objects.filter(
        usuario=request.user,
        data_competencia__range=(inicio_mes, fim_mes),
    )
    diagnostico_ia = AnaliseFinanceiraIA.objects.filter(
        usuario=request.user,
        periodo_inicio=inicio_mes,
        status=AnaliseFinanceiraIA.STATUS_SUCESSO,
    ).first()

    if request.method == "POST" and request.POST.get("acao") == "redefinir_relatorio":
        AnaliseFinanceiraIA.objects.filter(usuario=request.user, periodo_inicio=inicio_mes).delete()
        messages.success(request, "Relatório redefinido com sucesso.")
        return redirect(f"{request.path}?mes={inicio_mes:%Y-%m}")

    if request.method == "POST" and request.POST.get("acao") == "gerar_diagnostico_ia":
        bloqueio = bloquear_recurso_premium(request, "ia", "relatorios")
        if bloqueio:
            return bloqueio

        if atingiu_limite_temporario(request, "diagnostico_ia", limite=10, janela_segundos=3600):
            messages.warning(request, "Limite temporário de diagnósticos atingido. Tente novamente em alguns minutos.")
            return redirect("relatorios")

        contexto_ia = construir_contexto_analise_financeira(request.user, inicio_mes, fim_mes)

        try:
            analise_estruturada, resposta_bruta = chamar_openai_analise_financeira(contexto_ia)
        except OpenAIFinanceiroErro as erro:
            AnaliseFinanceiraIA.objects.create(
                usuario=request.user,
                periodo_inicio=inicio_mes,
                periodo_fim=fim_mes,
                status=AnaliseFinanceiraIA.STATUS_ERRO,
                modelo=settings.OPENAI_MODEL,
                contexto_enviado=contexto_ia,
                mensagem_erro=str(erro),
            )
            messages.error(request, str(erro))
            return redirect(f"{request.path}?mes={inicio_mes:%Y-%m}")

        AnaliseFinanceiraIA.objects.create(
            usuario=request.user,
            periodo_inicio=inicio_mes,
            periodo_fim=fim_mes,
            status=AnaliseFinanceiraIA.STATUS_SUCESSO,
            modelo=settings.OPENAI_MODEL,
            saude_financeira=analise_estruturada["saude_financeira"],
            resumo_executivo=analise_estruturada["resumo_executivo"],
            contexto_enviado=contexto_ia,
            metricas_resumo=analise_estruturada["metricas_resumo"],
            sinais_positivos=analise_estruturada["sinais_positivos"],
            alertas_prioritarios=analise_estruturada["alertas_prioritarios"],
            oportunidades=analise_estruturada["oportunidades"],
            plano_acao=analise_estruturada["plano_acao"],
            resposta_bruta=resposta_bruta,
        )
        criar_notificacao(
            request.user,
            "Diagnóstico Financeiro Gerado",
            f"O diagnóstico financeiro de {inicio_mes:%m/%Y} foi atualizado.",
            Notificacao.TIPO_SUCESSO,
            reverse("relatorios"),
        )
        messages.success(request, "Diagnóstico Financeiro por IA gerado com sucesso.")
        return redirect(f"{request.path}?mes={inicio_mes:%Y-%m}")

    resumo_por_categoria = (
        lancamentos.values("categoria__nome", "tipo")
        .annotate(total=Sum("valor"), quantidade=Count("id"))
        .order_by("tipo", "-total")
    )
    mapa_tipos = dict(Lancamento.TIPOS)

    resumo_por_categoria = [
        {**item, "tipo_label": mapa_tipos.get(item["tipo"], item["tipo"])}
        for item in resumo_por_categoria
    ]

    contexto = {
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "inicio_mes": inicio_mes,
        "fim_mes": fim_mes,
        "plano_usuario": plano_usuario,
        "ia_liberada": ia_liberada,
        "diagnostico_ia": diagnostico_ia,
        "resumo_por_categoria": resumo_por_categoria,
    }
    return render(request, "relatorios/relatorios.html", contexto)


@login_required
def ranking_viloes(request):
    """Mostra os maiores vilões financeiros do mês com leitura visual e divertida."""
    inicio_mes, fim_mes = obter_mes_referencia(request)
    despesas_mes = Lancamento.objects.filter(
        usuario=request.user,
        tipo=Lancamento.TIPO_DESPESA,
        data_competencia__range=(inicio_mes, fim_mes),
    ).select_related("categoria", "cartao", "orcamento_compartilhado")
    total_despesas = somar_valores(despesas_mes)

    resumo_categorias = (
        despesas_mes.values("categoria_id", "categoria__nome")
        .annotate(total=Sum("valor"), quantidade=Count("id"))
        .order_by("-total", "categoria__nome")
    )

    ranking = []
    for indice, item in enumerate(resumo_categorias[:8], start=1):
        total_categoria = item["total"] or Decimal("0.00")
        percentual = Decimal("0.00")

        if total_despesas > 0:
            percentual = min((total_categoria / total_despesas) * Decimal("100"), Decimal("100.00"))

        nivel, selo = classificar_vilao(percentual)
        visual = obter_visual_vilao(item["categoria__nome"])
        ranking.append(
            {
                "posicao": indice,
                "categoria_id": item["categoria_id"],
                "nome": item["categoria__nome"],
                "total": total_categoria,
                "quantidade": item["quantidade"],
                "percentual": formatar_percentual(percentual),
                "percentual_css": f"{percentual:.2f}",
                "nivel": nivel,
                "selo": selo,
                "delay": (indice - 1) * 90,
                "link": f"/despesas/?mes={inicio_mes:%Y-%m}&categoria={item['categoria_id']}",
                **visual,
            }
        )

    total_cartao = somar_valores(despesas_mes.filter(forma_pagamento=Lancamento.FORMA_CREDITO))
    total_parcelado = somar_valores(despesas_mes.filter(compra_parcelada=True))
    maior_lancamento = despesas_mes.order_by("-valor").first()

    sinais = [
        montar_sinal_vilao(
            "Cartão de crédito",
            despesas_mes.filter(forma_pagamento=Lancamento.FORMA_CREDITO),
            total_despesas,
            "fa-credit-card",
            "Confira a fatura antes dela virar surpresa no fechamento.",
            "villain-purple",
        ),
        montar_sinal_vilao(
            "Compras parceladas",
            despesas_mes.filter(compra_parcelada=True),
            total_despesas,
            "fa-layer-group",
            "Parcelas pequenas competem com seu saldo dos próximos meses.",
            "villain-blue",
        ),
        montar_sinal_vilao(
            "Assinaturas",
            despesas_mes.filter(
                montar_busca_por_palavras_chave(
                    ["assinatura", "netflix", "spotify", "streaming", "prime", "disney", "mensalidade", "plano"]
                )
            ),
            total_despesas,
            "fa-repeat",
            "Revise recorrências: o que não é usado pode virar economia imediata.",
            "villain-pink",
        ),
        montar_sinal_vilao(
            "Delivery e restaurantes",
            despesas_mes.filter(
                montar_busca_por_palavras_chave(
                    ["delivery", "ifood", "restaurante", "lanche", "pizza", "hamburguer", "alimentacao"]
                )
            ),
            total_despesas,
            "fa-burger",
            "Defina um teto semanal para comer fora sem travar seu orçamento.",
            "villain-red",
        ),
        montar_sinal_vilao(
            "Transporte",
            despesas_mes.filter(
                montar_busca_por_palavras_chave(
                    ["transporte", "uber", "99", "combustivel", "gasolina", "onibus", "metro", "estacionamento"]
                )
            ),
            total_despesas,
            "fa-car-side",
            "Planejar trajetos e agrupar saídas costuma reduzir bastante esse gasto.",
            "villain-yellow",
        ),
    ]
    sinais = sorted([sinal for sinal in sinais if sinal], key=lambda item: item["total"], reverse=True)

    contexto = {
        "inicio_mes": inicio_mes,
        "fim_mes": fim_mes,
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "ranking": ranking,
        "maior_vilao": ranking[0] if ranking else None,
        "sinais": sinais[:5],
        "total_despesas": total_despesas,
        "total_cartao": total_cartao,
        "total_parcelado": total_parcelado,
        "quantidade_despesas": despesas_mes.count(),
        "maior_lancamento": maior_lancamento,
    }
    return render(request, "viloes/ranking.html", contexto)
