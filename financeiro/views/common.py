"""Utilidades compartilhadas pelas views do app financeiro."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
import hashlib
import hmac
import json
import logging
import smtplib
import socket
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

from ..forms import (
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
from ..email_backends import BrevoAPIEmailError, mascarar_brevo_api_key
from ..ia_financeira import (
    MODELO_ANALISE_LOCAL,
    OBJETIVO_ANALISE_IA,
    REGRAS_ANALISE_IA,
    gerar_analise_financeira_local,
)
from ..mercado_pago import MercadoPagoClient, MercadoPagoErro
from ..models import (
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
        "mensagem": "O plano Freemium permite cadastrar apenas 1 investimento. O Premium libera carteira completa e Inteligência Financeira.",
    },
}

RECURSOS_PREMIUM = {
    "ia": {
        "nome": "Análise Financeira Inteligente",
        "mensagem": "A Análise Financeira Inteligente faz parte do Premium. Finalize o upgrade para liberar esse recurso.",
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




def obter_cliente_mercado_pago():
    """Cria o cliente do Mercado Pago respeitando mocks aplicados em financeiro.views."""
    import sys

    pacote_views = sys.modules.get("financeiro.views")
    cliente_cls = getattr(pacote_views, "MercadoPagoClient", MercadoPagoClient) if pacote_views else MercadoPagoClient
    return cliente_cls()


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
    contexto = montar_contexto_bloqueio_premium(request.user, recurso_nome)
    contexto["rota_retorno"] = rota_retorno
    return render(request, "premium/bloqueio.html", contexto)

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
    cliente = obter_cliente_mercado_pago()
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
                "Seu Plano Premium está ativo. A Análise Financeira Inteligente foi liberada.",
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

def mensagem_erro_email_transacional(erro):
    """Traduz falhas comuns de SMTP em mensagens acionaveis sem expor senhas."""
    if isinstance(erro, BrevoAPIEmailError):
        status = erro.status_code
        corpo = str(erro.body or "").lower()

        if status == "MISSING_API_KEY":
            return (
                "A variável BREVO_API_KEY não está configurada no Render. Crie uma API Key na Brevo em "
                "SMTP & API > API Keys e salve no Environment do Render."
            )

        if status in {401, 403}:
            return (
                "A API Key da Brevo foi recusada. Confira se BREVO_API_KEY é uma API Key válida da Brevo, "
                "não a SMTP Key, e se ela foi salva no Environment do Render antes do redeploy."
            )

        if status == 429:
            return "A Brevo recusou o envio por limite de requisições ou quota. Verifique o painel transacional da Brevo."

        if status == 400 and any(palavra in corpo for palavra in ["sender", "remetente", "from", "email"]):
            return (
                "A Brevo recusou o remetente. Confira se DEFAULT_FROM_EMAIL usa exatamente um remetente verificado "
                "na Brevo, por exemplo: FinanPy <suporte.finanpy@gmail.com>."
            )

        resumo = str(erro.body or erro.message or "")[:240]
        return f"A API da Brevo recusou o envio. Status: {status}. Detalhe: {resumo}"

    if isinstance(erro, ValueError) and "BREVO_API_KEY" in str(erro):
        return (
            "A variável BREVO_API_KEY não está configurada no Render. Crie uma API Key na Brevo em "
            "SMTP & API > API Keys e salve no Environment do Render."
        )

    if isinstance(erro, smtplib.SMTPAuthenticationError):
        return (
            "A Brevo recusou o login SMTP. Confira EMAIL_HOST_USER e EMAIL_HOST_PASSWORD. "
            "O password precisa ser a SMTP Key da Brevo, não a senha do Gmail, não a senha da conta Brevo e não a API Key."
        )

    if isinstance(erro, smtplib.SMTPSenderRefused):
        return (
            "A Brevo recusou o remetente configurado. Confira se DEFAULT_FROM_EMAIL usa exatamente um remetente verificado "
            "na Brevo, por exemplo: FinanPy <suporte.finanpy@gmail.com>."
        )

    if isinstance(erro, smtplib.SMTPRecipientsRefused):
        return "A Brevo recusou o e-mail de destino. Teste com outro e-mail e verifique se o endereço não está bloqueado."

    if isinstance(erro, smtplib.SMTPDataError):
        return (
            "A Brevo conectou, mas recusou o conteúdo ou a política de envio. Verifique se o remetente está liberado, "
            "se sua conta SMTP transacional está ativa e se não existe bloqueio de quota/compliance."
        )

    if isinstance(erro, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError, socket.timeout, OSError)):
        return (
            "O FinanPy não conseguiu conectar ao servidor SMTP. No Render Free, as portas SMTP 25, 465 e 587 podem "
            "ficar bloqueadas. Use EMAIL_HOST=smtp-relay.brevo.com, EMAIL_PORT=2525, EMAIL_USE_TLS=True e "
            "EMAIL_USE_SSL=False."
        )

    return (
        "Não foi possível enviar o e-mail de confirmação. Se você estiver no Render Free, prefira "
        "EMAIL_BACKEND=financeiro.email_backends.BrevoAPIEmailBackend com BREVO_API_KEY configurada."
    )

def calcular_percentual_limite(total_usado, limite_total):
    """Calcula o percentual visual de uso sem ultrapassar 100%."""
    limite = int(limite_total or 0)
    usado = int(total_usado or 0)

    if limite <= 0:
        return 0

    if limite >= 999:
        return min(usado * 10, 100)

    return min(round((usado / limite) * 100), 100)

def formatar_variacao_percentual(valor_atual, valor_anterior):
    """Compara dois valores e devolve um percentual pronto para a tela."""
    atual = Decimal(str(valor_atual or "0.00"))
    anterior = Decimal(str(valor_anterior or "0.00"))

    if anterior <= 0:
        if atual <= 0:
            return "0%"
        return "+100%"

    variacao = ((atual - anterior) / anterior) * Decimal("100")
    sinal = "+" if variacao > 0 else ""
    return f"{sinal}{float(variacao):.1f}%".replace(".", ",")

def formatar_brl_texto(valor):
    """Formata moeda em texto no padrao brasileiro."""
    valor_decimal = Decimal(str(valor or "0.00"))
    texto = f"{valor_decimal:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"

def prever_fechamento_mes(usuario, inicio_mes, fim_mes):
    """Calcula uma previsao simples de fechamento financeiro do mes."""
    hoje = timezone.localdate()
    lancamentos_mes = Lancamento.objects.filter(
        usuario=usuario,
        data_competencia__range=(inicio_mes, fim_mes),
    )

    receitas_mes = lancamentos_mes.filter(tipo=Lancamento.TIPO_RECEITA)
    despesas_mes = lancamentos_mes.filter(tipo=Lancamento.TIPO_DESPESA)
    despesas_atrasadas = despesas_mes.filter(
        Q(status=Lancamento.STATUS_ATRASADO)
        | Q(status=Lancamento.STATUS_PENDENTE, data_vencimento__lt=hoje)
    )
    despesas_pendentes = despesas_mes.filter(
        status=Lancamento.STATUS_PENDENTE,
        data_vencimento__gte=hoje,
    )

    total_receitas = somar_valores(receitas_mes)
    total_despesas_pagas = somar_valores(despesas_mes.filter(status=Lancamento.STATUS_PAGO))
    total_pendentes = somar_valores(despesas_pendentes)
    total_atrasados = somar_valores(despesas_atrasadas)
    saldo_previsto = total_receitas - total_despesas_pagas - total_pendentes - total_atrasados

    if saldo_previsto > Decimal("0.00"):
        status = "POSITIVO"
        mensagem = "Você tende a fechar o mês positivo."
        risco = "baixo"
    elif saldo_previsto < Decimal("0.00"):
        status = "NEGATIVO"
        mensagem = "Você tende a fechar o mês negativo se nada mudar."
        risco = "alto"
    else:
        status = "ZERO"
        mensagem = "Você tende a fechar o mês no zero a zero."
        risco = "medio"

    if status == "POSITIVO" and total_atrasados > Decimal("0.00"):
        risco = "medio"
        mensagem = "Você tende a fechar positivo, mas ainda tem contas atrasadas para resolver."
    elif status == "POSITIVO" and total_pendentes > saldo_previsto:
        risco = "medio"
        mensagem = "Você tende a fechar positivo, mas as contas pendentes ainda exigem atenção."
    elif status == "ZERO" and total_atrasados > Decimal("0.00"):
        risco = "alto"
        mensagem = "Você tende a fechar no zero, mas contas atrasadas aumentam o risco do mês."

    return {
        "saldo_previsto": saldo_previsto,
        "status": status,
        "mensagem": mensagem,
        "risco": risco,
    }


def obter_proxima_conta_importante(usuario, inicio_mes, fim_mes):
    """Encontra a conta pendente mais urgente do mes."""
    hoje = timezone.localdate()
    fim_alerta = hoje + timedelta(days=3)
    contas_mes = Lancamento.objects.filter(
        usuario=usuario,
        tipo=Lancamento.TIPO_DESPESA,
        data_competencia__range=(inicio_mes, fim_mes),
    ).filter(
        Q(status=Lancamento.STATUS_PENDENTE) | Q(status=Lancamento.STATUS_ATRASADO)
    )

    def montar_retorno(conta, status, mensagem):
        """Padroniza a resposta esperada pelo dashboard e pelos testes."""
        if not conta:
            return None

        return {
            "descricao": conta.descricao_completa,
            "valor": conta.valor,
            "vencimento": conta.data_vencimento,
            "status": status,
            "mensagem": mensagem,
        }

    conta_atrasada = (
        contas_mes.filter(
            Q(status=Lancamento.STATUS_ATRASADO)
            | Q(status=Lancamento.STATUS_PENDENTE, data_vencimento__lt=hoje)
        )
        .order_by("data_vencimento", "-valor", "id")
        .first()
    )
    if conta_atrasada:
        dias_atraso = max((hoje - conta_atrasada.data_vencimento).days, 1)
        plural = "s" if dias_atraso != 1 else ""
        return montar_retorno(
            conta_atrasada,
            "atrasada",
            f"Essa conta está atrasada há {dias_atraso} dia{plural}.",
        )

    conta_vence_hoje = (
        contas_mes.filter(status=Lancamento.STATUS_PENDENTE, data_vencimento=hoje)
        .order_by("-valor", "id")
        .first()
    )
    if conta_vence_hoje:
        return montar_retorno(
            conta_vence_hoje,
            "vence_hoje",
            "Essa conta vence hoje.",
        )

    conta_vence_em_breve = (
        contas_mes.filter(
            status=Lancamento.STATUS_PENDENTE,
            data_vencimento__gt=hoje,
            data_vencimento__lte=fim_alerta,
        )
        .order_by("data_vencimento", "-valor", "id")
        .first()
    )
    if conta_vence_em_breve:
        dias_para_vencer = (conta_vence_em_breve.data_vencimento - hoje).days
        plural = "s" if dias_para_vencer != 1 else ""
        return montar_retorno(
            conta_vence_em_breve,
            "vence_em_breve",
            f"Essa conta vence em {dias_para_vencer} dia{plural}.",
        )

    proxima_conta_pendente = (
        contas_mes.filter(
            status=Lancamento.STATUS_PENDENTE,
            data_vencimento__gt=fim_alerta,
        )
        .order_by("data_vencimento", "-valor", "id")
        .first()
    )
    if proxima_conta_pendente:
        dias_para_vencer = (proxima_conta_pendente.data_vencimento - hoje).days
        return montar_retorno(
            proxima_conta_pendente,
            "pendente",
            f"Essa é a próxima conta pendente do mês e vence em {dias_para_vencer} dias.",
        )

    return None


def extrair_descricao_base_recorrente(descricao):
    """Reduz descricoes parecidas a uma base simples para agrupar gastos pequenos."""
    texto_normalizado = normalizar_texto(descricao)
    texto_limpo = "".join(caractere if caractere.isalnum() else " " for caractere in texto_normalizado)
    palavras_ignoradas = {
        "a",
        "as",
        "com",
        "da",
        "de",
        "do",
        "dos",
        "e",
        "em",
        "na",
        "no",
        "os",
        "para",
        "por",
        "um",
        "uma",
    }
    palavras = [
        palavra
        for palavra in texto_limpo.split()
        if palavra not in palavras_ignoradas and not palavra.isdigit() and len(palavra) > 1
    ]

    if not palavras:
        return texto_normalizado.strip() or "gasto"

    return " ".join(palavras[:2])


def detectar_gastos_pequenos_recorrentes(usuario, inicio_mes, fim_mes):
    """Encontra despesas pequenas que se repetem e acumulam valor relevante."""
    valor_maximo_pequeno = Decimal("50.00")
    quantidade_minima = 3
    total_minimo_relevante = Decimal("50.00")
    grupos = {}

    despesas_pequenas = (
        Lancamento.objects.filter(
            usuario=usuario,
            tipo=Lancamento.TIPO_DESPESA,
            valor__lt=valor_maximo_pequeno,
            data_competencia__range=(inicio_mes, fim_mes),
        )
        .select_related("categoria")
        .order_by("categoria__nome", "descricao")
    )

    def adicionar_grupo(chave, descricao_base, lancamento):
        """Acumula quantidade e total por descricao parecida ou categoria."""
        if chave not in grupos:
            grupos[chave] = {
                "descricao_base": descricao_base,
                "quantidade": 0,
                "total": Decimal("0.00"),
            }

        grupos[chave]["quantidade"] += 1
        grupos[chave]["total"] += lancamento.valor

    for lancamento in despesas_pequenas:
        descricao_base = extrair_descricao_base_recorrente(lancamento.descricao)
        adicionar_grupo(f"descricao:{descricao_base}", descricao_base.title(), lancamento)

        if lancamento.categoria_id:
            adicionar_grupo(
                f"categoria:{lancamento.categoria_id}",
                lancamento.categoria.nome,
                lancamento,
            )

    recorrencias = []
    descricoes_ja_usadas = set()
    for grupo in grupos.values():
        if grupo["quantidade"] < quantidade_minima or grupo["total"] < total_minimo_relevante:
            continue

        chave_visual = normalizar_texto(grupo["descricao_base"])
        if chave_visual in descricoes_ja_usadas:
            continue
        descricoes_ja_usadas.add(chave_visual)

        descricao_base = grupo["descricao_base"]
        recorrencias.append(
            {
                "descricao_base": descricao_base,
                "quantidade": grupo["quantidade"],
                "total": grupo["total"],
                "mensagem": (
                    f"Pequenos gastos com {descricao_base.lower()} somaram "
                    f"{formatar_brl_texto(grupo['total'])}."
                ),
            }
        )

    return sorted(recorrencias, key=lambda item: item["total"], reverse=True)


def calcular_uso_categoria(usuario, categoria, inicio_mes, fim_mes):
    """Calcula quanto do limite mensal de uma categoria ja foi usado."""
    limite = Decimal(str(categoria.limite_mensal or Decimal("0.00")))
    gasto = somar_valores(
        Lancamento.objects.filter(
            usuario=usuario,
            categoria=categoria,
            tipo=Lancamento.TIPO_DESPESA,
            data_competencia__range=(inicio_mes, fim_mes),
        )
    )

    percentual = 0.0
    nivel = "SEM_LIMITE"
    if limite > Decimal("0.00"):
        percentual_decimal = (gasto / limite) * Decimal("100")
        percentual = round(float(percentual_decimal), 1)
        if percentual >= 100:
            nivel = "ESTOURO"
        elif percentual >= 80:
            nivel = "ATENCAO"
        else:
            nivel = "OK"

    return {
        "categoria": categoria.nome,
        "limite": limite,
        "gasto": gasto,
        "percentual": percentual,
        "nivel": nivel,
    }


def gerar_alertas_limite_categoria(usuario, inicio_mes, fim_mes):
    """Gera alertas para categorias que passaram de 80% do limite mensal."""
    categorias_com_limite = Categoria.objects.filter(
        usuario=usuario,
        tipo=Categoria.TIPO_DESPESA,
        limite_mensal__gt=Decimal("0.00"),
    ).order_by("nome")

    alertas = []
    for categoria in categorias_com_limite:
        uso = calcular_uso_categoria(usuario, categoria, inicio_mes, fim_mes)
        if uso["nivel"] in {"ATENCAO", "ESTOURO"}:
            alertas.append(uso)

    return sorted(alertas, key=lambda item: item["percentual"], reverse=True)


def converter_decimal_formulario(valor):
    """Converte valores digitados no padrao brasileiro ou internacional para Decimal."""
    texto = str(valor or "").strip().replace("R$", "").replace(" ", "")
    if not texto:
        raise ValueError("Informe um valor valido para o lancamento.")

    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")

    try:
        valor_decimal = Decimal(texto)
    except Exception as exc:
        raise ValueError("Informe um valor valido para o lancamento.") from exc

    if valor_decimal <= Decimal("0.00"):
        raise ValueError("O valor precisa ser maior que zero.")

    return valor_decimal.quantize(Decimal("0.01"))


def converter_data_formulario(valor, campo="data"):
    """Converte uma data ISO vinda do formulario HTML para objeto date."""
    if not valor:
        return timezone.localdate()

    try:
        return date.fromisoformat(str(valor))
    except ValueError as exc:
        raise ValueError(f"Informe uma {campo} valida.") from exc


def criar_lancamento_rapido(usuario, dados):
    """Cria uma receita ou despesa diretamente pelo dashboard."""
    tipo = dados.get("tipo")
    status = dados.get("status") or Lancamento.STATUS_PENDENTE
    descricao = str(dados.get("descricao") or "").strip()
    data_lancamento = converter_data_formulario(dados.get("data"), "data")

    if tipo not in {Lancamento.TIPO_RECEITA, Lancamento.TIPO_DESPESA}:
        raise ValueError("Escolha se o lançamento é receita ou despesa.")

    if status not in {codigo for codigo, _ in Lancamento.STATUS_CHOICES}:
        raise ValueError("Escolha um status valido para o lançamento.")

    if not descricao:
        raise ValueError("Informe uma descrição para o lançamento.")

    valor = converter_decimal_formulario(dados.get("valor"))
    categoria = Categoria.objects.filter(pk=dados.get("categoria"), usuario=usuario, tipo=tipo).first()
    if not categoria:
        raise ValueError("Escolha uma categoria válida para o tipo selecionado.")
    data_pagamento = data_lancamento if status == Lancamento.STATUS_PAGO else None

    lancamento = Lancamento.objects.create(
        usuario=usuario,
        tipo=tipo,
        descricao=descricao,
        valor=valor,
        categoria=categoria,
        data_competencia=data_lancamento,
        data_vencimento=data_lancamento,
        data_pagamento=data_pagamento,
        status=status,
        forma_pagamento=Lancamento.FORMA_PIX,
    )
    criar_notificacao(
        usuario,
        "Lançamento Rápido Criado",
        f"{lancamento.descricao_completa} foi registrado pelo dashboard.",
        Notificacao.TIPO_SUCESSO,
        reverse("lista_lancamentos"),
    )
    return lancamento


def duplicar_lancamento_para_proximo_mes(usuario, lancamento_id):
    """Duplica um lançamento do próprio usuário para o mês seguinte."""
    lancamento = obter_objeto_do_usuario(Lancamento, usuario, lancamento_id)
    nova_data_competencia = adicionar_meses(lancamento.data_competencia, 1)
    nova_data_vencimento = adicionar_meses(lancamento.data_vencimento, 1)
    novo_status = Lancamento.STATUS_PENDENTE if lancamento.tipo == Lancamento.TIPO_DESPESA else lancamento.status
    if novo_status == Lancamento.STATUS_PAGO:
        novo_status = Lancamento.STATUS_PENDENTE

    duplicado = Lancamento.objects.create(
        usuario=usuario,
        tipo=lancamento.tipo,
        escopo=lancamento.escopo,
        orcamento_compartilhado=lancamento.orcamento_compartilhado,
        descricao=lancamento.descricao,
        valor=lancamento.valor,
        categoria=lancamento.categoria,
        data_competencia=nova_data_competencia,
        data_vencimento=nova_data_vencimento,
        data_pagamento=None,
        status=novo_status,
        forma_pagamento=lancamento.forma_pagamento,
        observacao=lancamento.observacao,
        cartao=lancamento.cartao,
        compra_parcelada=False,
        parcela_atual=1,
        total_parcelas=1,
        grupo_parcelas=None,
    )
    criar_notificacao(
        usuario,
        "Lançamento Duplicado",
        f"{duplicado.descricao_completa} foi criado para o próximo mês.",
        Notificacao.TIPO_INFO,
        reverse("lista_lancamentos"),
    )
    return duplicado


def marcar_lancamento_como_pago(usuario, lancamento_id):
    """Marca uma conta do próprio usuário como paga com a data de hoje."""
    lancamento = obter_objeto_do_usuario(Lancamento, usuario, lancamento_id)
    lancamento.status = Lancamento.STATUS_PAGO
    lancamento.data_pagamento = timezone.localdate()
    lancamento.save()
    criar_notificacao(
        usuario,
        "Conta Paga",
        f"{lancamento.descricao_completa} foi marcada como paga.",
        Notificacao.TIPO_SUCESSO,
        reverse("lista_lancamentos"),
    )
    return lancamento


def criar_lancamentos_recorrentes(usuario, lancamento_id, quantidade_meses):
    """Gera lançamentos futuros sem duplicar lançamentos iguais no mesmo mês."""
    lancamento_base = obter_objeto_do_usuario(Lancamento, usuario, lancamento_id)

    try:
        quantidade = int(quantidade_meses)
    except (TypeError, ValueError) as exc:
        raise ValueError("Informe uma quantidade de meses valida.") from exc

    if quantidade < 1 or quantidade > 24:
        raise ValueError("A recorrência deve ter entre 1 e 24 meses.")

    criados = []
    for indice in range(1, quantidade + 1):
        data_competencia = adicionar_meses(lancamento_base.data_competencia, indice)
        data_vencimento = adicionar_meses(lancamento_base.data_vencimento, indice)
        inicio_mes = date(data_competencia.year, data_competencia.month, 1)
        fim_mes = date(
            data_competencia.year,
            data_competencia.month,
            monthrange(data_competencia.year, data_competencia.month)[1],
        )

        ja_existe = Lancamento.objects.filter(
            usuario=usuario,
            tipo=lancamento_base.tipo,
            descricao__iexact=lancamento_base.descricao,
            valor=lancamento_base.valor,
            categoria=lancamento_base.categoria,
            data_competencia__range=(inicio_mes, fim_mes),
        ).exists()
        if ja_existe:
            continue

        criados.append(
            Lancamento.objects.create(
                usuario=usuario,
                tipo=lancamento_base.tipo,
                escopo=lancamento_base.escopo,
                orcamento_compartilhado=lancamento_base.orcamento_compartilhado,
                descricao=lancamento_base.descricao,
                valor=lancamento_base.valor,
                categoria=lancamento_base.categoria,
                data_competencia=data_competencia,
                data_vencimento=data_vencimento,
                data_pagamento=None,
                status=Lancamento.STATUS_PENDENTE,
                forma_pagamento=lancamento_base.forma_pagamento,
                observacao=lancamento_base.observacao,
                cartao=lancamento_base.cartao,
                compra_parcelada=False,
                parcela_atual=1,
                total_parcelas=1,
                grupo_parcelas=None,
            )
        )

    if criados:
        criar_notificacao(
            usuario,
            "Lançamentos Recorrentes Criados",
            f"{len(criados)} lançamento(s) futuro(s) foram gerados.",
            Notificacao.TIPO_SUCESSO,
            reverse("lista_lancamentos"),
        )

    return criados


def calcular_parcelas_futuras(usuario, meses=6, cartao=None):
    """Mostra quanto o usuário já comprometeu em parcelas nos próximos meses."""
    hoje = timezone.localdate()
    inicio_referencia = date(hoje.year, hoje.month, 1)
    parcelas = []

    for indice in range(1, int(meses) + 1):
        inicio_mes = adicionar_meses(inicio_referencia, indice)
        fim_mes = date(inicio_mes.year, inicio_mes.month, monthrange(inicio_mes.year, inicio_mes.month)[1])
        queryset = Lancamento.objects.filter(
            usuario=usuario,
            tipo=Lancamento.TIPO_DESPESA,
            compra_parcelada=True,
            data_competencia__range=(inicio_mes, fim_mes),
        )
        if cartao:
            queryset = queryset.filter(cartao=cartao)

        parcelas.append(
            {
                "mes": inicio_mes.strftime("%m/%Y"),
                "total_parcelado": somar_valores(queryset),
                "quantidade_parcelas": queryset.count(),
            }
        )

    return parcelas


def montar_fatura_cartao(usuario, cartao_id, inicio_mes, fim_mes):
    """Monta uma visão mensal de fatura, limite usado e parcelas futuras."""
    cartao = get_object_or_404(CartaoCredito, pk=cartao_id, usuario=usuario)
    compras = (
        Lancamento.objects.filter(
            usuario=usuario,
            cartao=cartao,
            tipo=Lancamento.TIPO_DESPESA,
            forma_pagamento=Lancamento.FORMA_CREDITO,
            data_competencia__range=(inicio_mes, fim_mes),
        )
        .select_related("categoria")
        .order_by("data_competencia", "descricao")
    )
    total_fatura = somar_valores(compras)
    limite = Decimal(str(cartao.limite or Decimal("0.00")))
    limite_disponivel = limite - total_fatura
    if limite_disponivel < Decimal("0.00"):
        limite_disponivel = Decimal("0.00")

    percentual_usado = 0.0
    if limite > Decimal("0.00"):
        percentual_usado = round(float((total_fatura / limite) * Decimal("100")), 1)

    return {
        "cartao": cartao,
        "limite": limite,
        "total_fatura": total_fatura,
        "limite_disponivel": limite_disponivel,
        "percentual_usado": percentual_usado,
        "compras": compras,
        "parcelas_futuras": calcular_parcelas_futuras(usuario, meses=6, cartao=cartao),
    }


def prever_conclusao_meta(meta):
    """Calcula ritmo, valor faltante e chance de concluir a meta no prazo."""
    hoje = timezone.localdate()
    valor_alvo = Decimal(str(meta.valor_alvo or Decimal("0.00")))
    valor_atual = Decimal(str(meta.valor_atual or Decimal("0.00")))
    valor_faltante = meta.valor_restante
    percentual = meta.progresso_percentual

    if valor_alvo <= Decimal("0.00") or valor_faltante <= Decimal("0.00"):
        return {
            "percentual": percentual,
            "valor_faltante": Decimal("0.00"),
            "status_previsao": "CONCLUIDA",
            "data_prevista": hoje,
            "mensagem": "Meta alcançada. Agora é hora de proteger essa conquista.",
        }

    dias_decorridos = max((hoje - meta.data_inicio).days, 1)
    meses_decorridos = max(Decimal(dias_decorridos) / Decimal("30"), Decimal("1"))
    media_historica = (valor_atual / meses_decorridos).quantize(Decimal("0.01"))
    media_planejada = (Decimal(str(meta.valor_semanal_planejado or Decimal("0.00"))) * Decimal("4.33")).quantize(
        Decimal("0.01")
    )
    media_mensal = max(media_historica, media_planejada)

    if media_mensal <= Decimal("0.00"):
        return {
            "percentual": percentual,
            "valor_faltante": valor_faltante,
            "status_previsao": "SEM_RITMO",
            "data_prevista": None,
            "mensagem": "Ainda não há ritmo suficiente para prever a conclusão. Defina um valor semanal para guardar.",
        }

    meses_necessarios = int((valor_faltante / media_mensal).to_integral_value(rounding=ROUND_CEILING))
    data_prevista = adicionar_meses(hoje, max(meses_necessarios, 1))
    status_previsao = "NO_PRAZO" if data_prevista <= meta.data_limite else "FORA_DO_PRAZO"
    mensagem = (
        "Mantendo esse ritmo, você alcança a meta antes do prazo."
        if status_previsao == "NO_PRAZO"
        else "Nesse ritmo, a meta pode passar do prazo. Aumente o valor semanal ou revise gastos."
    )

    return {
        "percentual": percentual,
        "valor_faltante": valor_faltante,
        "status_previsao": status_previsao,
        "data_prevista": data_prevista,
        "mensagem": mensagem,
    }


def sugerir_valor_para_guardar(usuario, inicio_mes, fim_mes):
    """Sugere um valor seguro para guardar no mês com base no saldo previsto."""
    previsao = prever_fechamento_mes(usuario, inicio_mes, fim_mes)
    saldo_previsto = Decimal(str(previsao["saldo_previsto"]))
    despesas_mes = Lancamento.objects.filter(
        usuario=usuario,
        tipo=Lancamento.TIPO_DESPESA,
        data_competencia__range=(inicio_mes, fim_mes),
    )
    atrasados = despesas_mes.filter(status=Lancamento.STATUS_ATRASADO).count()
    pendentes = despesas_mes.filter(status=Lancamento.STATUS_PENDENTE).count()

    if atrasados:
        return {
            "valor_sugerido": Decimal("0.00"),
            "mensagem": "Regularize as contas atrasadas antes de guardar dinheiro neste mês.",
        }

    if saldo_previsto <= Decimal("0.00"):
        return {
            "valor_sugerido": Decimal("0.00"),
            "mensagem": "O mês ainda não tem sobra prevista. Priorize reduzir despesas antes de guardar.",
        }

    percentual = Decimal("0.20") if pendentes else Decimal("0.40")
    valor_sugerido = (saldo_previsto * percentual).quantize(Decimal("0.01"))
    return {
        "valor_sugerido": valor_sugerido,
        "mensagem": f"Você pode guardar {formatar_brl_texto(valor_sugerido)} sem comprometer o mês.",
    }


def comparar_mes_atual_com_anterior(usuario, inicio_mes, fim_mes):
    """Compara receitas, despesas e saldo do mês atual com o mês anterior."""
    inicio_anterior = adicionar_meses(inicio_mes, -1)
    fim_anterior = date(inicio_anterior.year, inicio_anterior.month, monthrange(inicio_anterior.year, inicio_anterior.month)[1])

    def totais_periodo(inicio, fim):
        lancamentos = Lancamento.objects.filter(usuario=usuario, data_competencia__range=(inicio, fim))
        receitas = somar_valores(lancamentos.filter(tipo=Lancamento.TIPO_RECEITA))
        despesas = somar_valores(lancamentos.filter(tipo=Lancamento.TIPO_DESPESA))
        return receitas, despesas, receitas - despesas

    def variacao_percentual(atual, anterior):
        if anterior == Decimal("0.00"):
            return 100.0 if atual > Decimal("0.00") else 0.0
        return round(float(((atual - anterior) / abs(anterior)) * Decimal("100")), 1)

    receitas_atual, despesas_atual, saldo_atual = totais_periodo(inicio_mes, fim_mes)
    receitas_anterior, despesas_anterior, saldo_anterior = totais_periodo(inicio_anterior, fim_anterior)
    despesas_variacao = variacao_percentual(despesas_atual, despesas_anterior)

    if despesas_variacao < 0:
        mensagem = "Você gastou menos que no mês passado."
    elif despesas_variacao > 0:
        mensagem = "Você gastou mais que no mês passado. Vale revisar as categorias principais."
    else:
        mensagem = "Seus gastos ficaram estáveis em relação ao mês passado."

    return {
        "receitas_variacao": variacao_percentual(receitas_atual, receitas_anterior),
        "despesas_variacao": despesas_variacao,
        "saldo_variacao": variacao_percentual(saldo_atual, saldo_anterior),
        "mensagem": mensagem,
    }


def montar_resumo_semanal(usuario):
    """Monta uma leitura objetiva da semana atual para o dashboard e futuro e-mail."""
    hoje = timezone.localdate()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    fim_semana = inicio_semana + timedelta(days=6)
    inicio_mes = date(hoje.year, hoje.month, 1)
    fim_mes = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])

    despesas_semana = Lancamento.objects.filter(
        usuario=usuario,
        tipo=Lancamento.TIPO_DESPESA,
        data_competencia__range=(inicio_semana, min(hoje, fim_semana)),
    )
    maior_categoria = despesas_semana.values("categoria__nome").annotate(total=Sum("valor")).order_by("-total").first()
    contas_vencendo = Lancamento.objects.filter(
        usuario=usuario,
        tipo=Lancamento.TIPO_DESPESA,
        status=Lancamento.STATUS_PENDENTE,
        data_vencimento__range=(hoje, hoje + timedelta(days=7)),
    )
    contas_atrasadas = Lancamento.objects.filter(
        usuario=usuario,
        tipo=Lancamento.TIPO_DESPESA,
        status=Lancamento.STATUS_ATRASADO,
    )
    previsao = prever_fechamento_mes(usuario, inicio_mes, fim_mes)
    acao = montar_acao_recomendada_hoje(usuario, inicio_mes, fim_mes)
    total_gasto = somar_valores(despesas_semana)
    nome_maior_categoria = maior_categoria["categoria__nome"] if maior_categoria else "sem categoria dominante"

    alertas = []
    if contas_atrasadas.exists():
        alertas.append(f"{contas_atrasadas.count()} conta(s) atrasada(s) precisam de atenção.")
    if contas_vencendo.exists():
        alertas.append(f"{contas_vencendo.count()} conta(s) vencem nos próximos 7 dias.")
    if previsao["status"] == "NEGATIVO":
        alertas.append("A previsão indica risco de fechar o mês negativo.")

    return {
        "titulo": "Resumo Da Semana",
        "mensagem": (
            f"Você gastou {formatar_brl_texto(total_gasto)} nesta semana. "
            f"A maior concentração está em {nome_maior_categoria}."
        ),
        "total_gasto": total_gasto,
        "maior_categoria": maior_categoria,
        "contas_vencendo": contas_vencendo.count(),
        "contas_atrasadas": contas_atrasadas.count(),
        "saldo_previsto": previsao["saldo_previsto"],
        "alertas": alertas,
        "acoes": [acao] if acao else [],
    }


def gerar_notificacoes_inteligentes(usuario, inicio_mes, fim_mes):
    """Cria notificações automáticas sem repetir o mesmo aviso no mesmo dia."""
    hoje = timezone.localdate()
    criadas = []

    def criar_unica(titulo, mensagem, tipo=Notificacao.TIPO_INFO, link=""):
        ja_existe = Notificacao.objects.filter(
            usuario=usuario,
            titulo=titulo,
            criada_em__date=hoje,
        ).exists()
        if ja_existe:
            return None

        notificacao = criar_notificacao(usuario, titulo, mensagem, tipo, link)
        if notificacao:
            criadas.append(notificacao)
        return notificacao

    despesas_mes = Lancamento.objects.filter(
        usuario=usuario,
        tipo=Lancamento.TIPO_DESPESA,
        data_competencia__range=(inicio_mes, fim_mes),
    )
    vence_amanha = despesas_mes.filter(status=Lancamento.STATUS_PENDENTE, data_vencimento=hoje + timedelta(days=1))
    atrasadas = despesas_mes.filter(Q(status=Lancamento.STATUS_ATRASADO) | Q(status=Lancamento.STATUS_PENDENTE, data_vencimento__lt=hoje))
    previsao = prever_fechamento_mes(usuario, inicio_mes, fim_mes)

    if vence_amanha.exists():
        criar_unica(
            "Conta Vence Amanhã",
            f"{vence_amanha.count()} conta(s) vencem amanhã. Confira antes de gerar atraso.",
            Notificacao.TIPO_ALERTA,
            reverse("contas_pendentes"),
        )

    if atrasadas.exists():
        criar_unica(
            "Conta Atrasada",
            f"Você tem {atrasadas.count()} conta(s) atrasada(s), somando {formatar_brl_texto(somar_valores(atrasadas))}.",
            Notificacao.TIPO_ERRO,
            reverse("contas_pendentes"),
        )

    if previsao["status"] == "NEGATIVO":
        criar_unica(
            "Saldo Previsto Negativo",
            f"Seu saldo previsto está em {formatar_brl_texto(previsao['saldo_previsto'])}.",
            Notificacao.TIPO_ALERTA,
            reverse("relatorios"),
        )

    for alerta in gerar_alertas_limite_categoria(usuario, inicio_mes, fim_mes):
        criar_unica(
            f"Limite De {alerta['categoria']}",
            f"A categoria {alerta['categoria']} já usou {alerta['percentual']}% do limite mensal.",
            Notificacao.TIPO_ALERTA,
            reverse("lista_categorias"),
        )

    metas_prazo = MetaFinanceira.objects.filter(
        usuario=usuario,
        status__in=[MetaFinanceira.STATUS_EM_ANDAMENTO, MetaFinanceira.STATUS_ATRASADA],
        data_limite__lte=hoje + timedelta(days=7),
    )
    for meta in metas_prazo[:3]:
        criar_unica(
            f"Meta Perto Do Prazo: {meta.titulo}",
            f"Faltam {formatar_brl_texto(meta.valor_restante)} para concluir essa meta.",
            Notificacao.TIPO_ALERTA,
            reverse("lista_metas"),
        )

    for cartao in CartaoCredito.objects.filter(usuario=usuario, ativo=True):
        fatura = montar_fatura_cartao(usuario, cartao.id, inicio_mes, fim_mes)
        if fatura["percentual_usado"] >= 70:
            criar_unica(
                f"Cartão Acima De 70%: {cartao.nome}",
                f"A fatura atual já usou {fatura['percentual_usado']}% do limite.",
                Notificacao.TIPO_ALERTA,
                reverse("lista_cartoes"),
            )

    return criadas


def montar_diagnostico_visual_ia(analise):
    """Organiza a análise local em blocos prontos para cards visuais."""
    if not analise:
        return {
            "saude": "ATENCAO",
            "resumo": "Nenhuma análise financeira foi gerada ainda.",
            "sinais_positivos": [],
            "alertas": [],
            "oportunidades": [],
            "acoes_7_dias": [],
            "acoes_30_dias": [],
        }

    def obter(campo, padrao):
        if isinstance(analise, dict):
            return analise.get(campo, padrao)
        return getattr(analise, campo, padrao)

    plano_acao = obter("plano_acao", []) or []
    acoes_7_dias = []
    acoes_30_dias = []
    for acao in plano_acao:
        prazo = acao.get("prazo") if isinstance(acao, dict) else getattr(acao, "prazo", "")
        if prazo in {"AGORA", "7_DIAS"}:
            acoes_7_dias.append(acao)
        else:
            acoes_30_dias.append(acao)

    return {
        "saude": obter("saude_financeira", "ATENCAO"),
        "resumo": obter("resumo_executivo", ""),
        "sinais_positivos": obter("sinais_positivos", []) or [],
        "alertas": obter("alertas_prioritarios", []) or [],
        "oportunidades": obter("oportunidades", []) or [],
        "acoes_7_dias": acoes_7_dias,
        "acoes_30_dias": acoes_30_dias,
    }


def montar_contexto_bloqueio_premium(usuario, recurso_nome):
    """Monta a tela de conversão para recursos premium bloqueados."""
    recurso = RECURSOS_PREMIUM.get(recurso_nome, {})
    nome_recurso = recurso.get("nome", recurso_nome.replace("_", " ").title())
    beneficios = [
        "Diagnóstico financeiro inteligente com regras locais do FinanPy.",
        "Simulador de decisões para testar compras, parcelas e cenários.",
        "Modo Anti-Descontrole com limites e plano de contenção.",
        "Modo Casal/Família para organizar orçamento compartilhado.",
        "Mais cartões, metas e investimentos cadastrados no painel.",
    ]

    exemplos = {
        "ia": "Exemplo: você gastou mais com delivery e pode economizar R$ 180,00 em 7 dias.",
        "simulador": "Exemplo: comprar agora reduz seu saldo previsto em 32% no fim do mês.",
        "contencao": "Exemplo: seu limite diário seguro para os próximos 15 dias é R$ 42,00.",
        "familia": "Exemplo: separe contas individuais e conjuntas sem misturar o orçamento.",
    }

    return {
        "recurso": nome_recurso,
        "titulo": f"Desbloqueie {nome_recurso}",
        "beneficios": beneficios,
        "exemplo_resultado": exemplos.get(recurso_nome, "Exemplo: receba uma leitura clara do que fazer com seu dinheiro hoje."),
        "botao_texto": "Assinar Premium",
        "checkout_url": reverse("checkout_premium"),
        "comparacao": [
            {
                "plano": "Free",
                "descricao": "Controle essencial com lançamentos, categorias, metas básicas e relatórios simples.",
            },
            {
                "plano": "Premium",
                "descricao": "Recursos inteligentes, limites maiores, simuladores e diagnóstico financeiro completo.",
            },
        ],
        "plano_usuario": obter_plano_usuario(usuario),
    }


def montar_acao_recomendada_hoje(usuario, inicio_mes, fim_mes):
    """Escolhe a acao financeira mais importante para o usuario executar hoje."""
    hoje = timezone.localdate()
    fim_alerta = hoje + timedelta(days=3)
    mes_param = inicio_mes.strftime("%Y-%m")

    lancamentos_mes = Lancamento.objects.filter(
        usuario=usuario,
        data_competencia__range=(inicio_mes, fim_mes),
    ).select_related("categoria")
    despesas_mes = lancamentos_mes.filter(tipo=Lancamento.TIPO_DESPESA)
    receitas_mes = lancamentos_mes.filter(tipo=Lancamento.TIPO_RECEITA)

    contas_atrasadas = despesas_mes.filter(
        Q(status=Lancamento.STATUS_ATRASADO)
        | Q(status=Lancamento.STATUS_PENDENTE, data_vencimento__lt=hoje)
    )
    quantidade_atrasadas = contas_atrasadas.count()

    if quantidade_atrasadas:
        total_atrasado = somar_valores(contas_atrasadas)
        return {
            "titulo": "Resolva Uma Conta Atrasada Hoje",
            "descricao": (
                f"Existem {quantidade_atrasadas} conta(s) atrasada(s), somando "
                f"{formatar_brl_texto(total_atrasado)}. Quitar ou renegociar isso evita juros "
                "e protege seu saldo previsto."
            ),
            "prioridade": "alta",
            "tipo": "danger",
            "icone": "fa-triangle-exclamation",
            "botao_texto": "Ver Lançamentos",
            "botao_url": (
                f"{reverse('lista_lancamentos')}?mes={mes_param}"
                f"&tipo={Lancamento.TIPO_DESPESA}&status={Lancamento.STATUS_ATRASADO}"
            ),
        }

    contas_vencendo = despesas_mes.filter(
        status=Lancamento.STATUS_PENDENTE,
        data_vencimento__range=(hoje, fim_alerta),
    ).order_by("data_vencimento", "-valor")
    quantidade_vencendo = contas_vencendo.count()

    if quantidade_vencendo:
        proxima_conta = contas_vencendo.first()
        total_vencendo = somar_valores(contas_vencendo)
        return {
            "titulo": "Prepare O Pagamento Dos Próximos Vencimentos",
            "descricao": (
                f"Você tem {quantidade_vencendo} conta(s) vencendo nos próximos 3 dias, "
                f"somando {formatar_brl_texto(total_vencendo)}. A próxima é "
                f"'{proxima_conta.descricao_completa}', com vencimento em "
                f"{proxima_conta.data_vencimento.strftime('%d/%m/%Y')}."
            ),
            "prioridade": "alta",
            "tipo": "warning",
            "icone": "fa-calendar-day",
            "botao_texto": "Ver Pendências",
            "botao_url": (
                f"{reverse('lista_lancamentos')}?mes={mes_param}"
                f"&tipo={Lancamento.TIPO_DESPESA}&status={Lancamento.STATUS_PENDENTE}"
            ),
        }

    total_receitas = somar_valores(receitas_mes)
    total_despesas = somar_valores(despesas_mes)
    saldo_previsto = total_receitas - total_despesas

    if saldo_previsto < Decimal("0.00"):
        return {
            "titulo": "Reduza Gastos Para Evitar Fechar Negativo",
            "descricao": (
                f"Seu saldo previsto do mês está em {formatar_brl_texto(saldo_previsto)}. "
                "Revise despesas não essenciais antes de assumir novos compromissos."
            ),
            "prioridade": "alta",
            "tipo": "danger",
            "icone": "fa-arrow-trend-down",
            "botao_texto": "Ver Relatório",
            "botao_url": f"{reverse('relatorios')}?mes={mes_param}",
        }

    categoria_mais_cara = (
        despesas_mes.values("categoria_id", "categoria__nome")
        .annotate(total=Sum("valor"))
        .order_by("-total")
        .first()
    )

    if categoria_mais_cara and categoria_mais_cara["total"]:
        return {
            "titulo": "Ataque A Categoria Mais Cara Do Mês",
            "descricao": (
                f"A categoria '{categoria_mais_cara['categoria__nome']}' já soma "
                f"{formatar_brl_texto(categoria_mais_cara['total'])}. Escolha uma despesa "
                "para cortar, adiar ou substituir por uma alternativa mais barata."
            ),
            "prioridade": "media",
            "tipo": "warning",
            "icone": "fa-chart-pie",
            "botao_texto": "Ver Categoria",
            "botao_url": (
                f"{reverse('lista_lancamentos')}?mes={mes_param}&tipo={Lancamento.TIPO_DESPESA}"
                f"&categoria={categoria_mais_cara['categoria_id']}"
            ),
        }

    metas_abertas = MetaFinanceira.objects.filter(
        usuario=usuario,
        status__in=[MetaFinanceira.STATUS_EM_ANDAMENTO, MetaFinanceira.STATUS_ATRASADA],
    ).order_by("data_limite")
    quantidade_metas_abertas = metas_abertas.count()

    if quantidade_metas_abertas:
        meta_prioritaria = metas_abertas.first()
        return {
            "titulo": "Dê Um Passo Pequeno Na Sua Meta",
            "descricao": (
                f"Sua meta '{meta_prioritaria.titulo}' ainda precisa de "
                f"{formatar_brl_texto(meta_prioritaria.valor_restante)}. Guardar um valor hoje "
                "mantém o objetivo vivo e evita deixar tudo para o fim do prazo."
            ),
            "prioridade": "media",
            "tipo": "success",
            "icone": "fa-bullseye",
            "botao_texto": "Ver Metas",
            "botao_url": reverse("lista_metas"),
        }

    if usuario_tem_ia_liberada(usuario):
        return {
            "titulo": "Gere Sua Análise Financeira Do Mês",
            "descricao": (
                "Sua Inteligência Financeira está liberada. Gere uma leitura do mês para "
                "encontrar alertas, oportunidades e um plano de ação com base nos seus dados."
            ),
            "prioridade": "baixa",
            "tipo": "info",
            "icone": "fa-robot",
            "botao_texto": "Análise Financeira",
            "botao_url": reverse("analise_ia"),
        }

    return {
        "titulo": "Libere A Inteligência Financeira",
        "descricao": (
            "Seu painel já está funcionando, mas a análise inteligente está bloqueada no "
            "Freemium. O Premium libera diagnóstico do mês, simulador e recursos avançados."
        ),
        "prioridade": "baixa",
        "tipo": "premium",
        "icone": "fa-crown",
        "botao_texto": "Conhecer Premium",
        "botao_url": reverse("configuracoes"),
    }


def montar_contexto_configuracoes(usuario, configuracao, plano_usuario, ultima_analise_ia):
    """Monta indicadores inteligentes para a nova tela de configurações."""
    hoje = timezone.localdate()
    inicio_mes = date(hoje.year, hoje.month, 1)
    fim_mes = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])
    inicio_mes_anterior = adicionar_meses(inicio_mes, -1)
    fim_mes_anterior = date(
        inicio_mes_anterior.year,
        inicio_mes_anterior.month,
        monthrange(inicio_mes_anterior.year, inicio_mes_anterior.month)[1],
    )

    lancamentos_usuario = Lancamento.objects.filter(usuario=usuario)
    lancamentos_mes = lancamentos_usuario.filter(data_competencia__range=(inicio_mes, fim_mes))
    lancamentos_mes_anterior = lancamentos_usuario.filter(
        data_competencia__range=(inicio_mes_anterior, fim_mes_anterior)
    )
    receitas_mes = lancamentos_mes.filter(tipo=Lancamento.TIPO_RECEITA)
    despesas_mes = lancamentos_mes.filter(tipo=Lancamento.TIPO_DESPESA)
    despesas_mes_anterior = lancamentos_mes_anterior.filter(tipo=Lancamento.TIPO_DESPESA)
    atrasados_mes = lancamentos_mes.filter(status=Lancamento.STATUS_ATRASADO)

    total_receitas_mes = somar_valores(receitas_mes)
    total_despesas_mes = somar_valores(despesas_mes)
    total_despesas_mes_anterior = somar_valores(despesas_mes_anterior)
    total_atrasado_mes = somar_valores(atrasados_mes)
    economia_mes = max(total_receitas_mes - total_despesas_mes, Decimal("0.00"))

    percentual_despesas = Decimal("0.00")
    if total_receitas_mes > 0:
        percentual_despesas = (total_despesas_mes / total_receitas_mes) * Decimal("100")
    elif total_despesas_mes > 0:
        percentual_despesas = Decimal("100.00")

    cartoes_usados = CartaoCredito.objects.filter(usuario=usuario).count()
    metas_queryset = MetaFinanceira.objects.filter(usuario=usuario)
    metas_usadas = metas_queryset.count()
    metas_concluidas = metas_queryset.filter(status=MetaFinanceira.STATUS_CONCLUIDA).count()
    investimentos_usados = Investimento.objects.filter(usuario=usuario).count()
    transacoes_count = lancamentos_usuario.count()
    dias_controle = lancamentos_usuario.values("data_competencia").distinct().count()
    dias_usando = max((hoje - timezone.localtime(usuario.date_joined).date()).days + 1, 1)

    principal_categoria = despesas_mes.values("categoria__nome").annotate(total=Sum("valor")).order_by("-total").first()
    principal_categoria_nome = principal_categoria["categoria__nome"] if principal_categoria else ""
    principal_categoria_total = principal_categoria["total"] if principal_categoria else Decimal("0.00")

    risco_descontrole = "Baixo"
    if total_atrasado_mes > 0 or economia_mes == 0 and total_despesas_mes > 0 or percentual_despesas >= Decimal("90"):
        risco_descontrole = "Alto"
    elif percentual_despesas >= Decimal("70") or lancamentos_mes.filter(status=Lancamento.STATUS_PENDENTE).exists():
        risco_descontrole = "Médio"

    if transacoes_count < 3:
        perfil_financeiro = "Em Análise"
    elif risco_descontrole == "Alto":
        perfil_financeiro = "Atenção"
    elif economia_mes > 0 and metas_usadas > 0:
        perfil_financeiro = "Planejador"
    elif economia_mes > 0:
        perfil_financeiro = "Organizado"
    else:
        perfil_financeiro = "Em Evolução"

    if economia_mes > 0 and risco_descontrole == "Baixo":
        tendencia_financeira = "Positiva"
    elif risco_descontrole == "Alto":
        tendencia_financeira = "Atenção"
    else:
        tendencia_financeira = "Estável"

    if total_atrasado_mes > 0:
        sugestao_titulo = "Priorize contas atrasadas."
        sugestao_texto = "Regularize os atrasos primeiro para evitar multas, juros e perda de previsibilidade no mês."
    elif principal_categoria_nome:
        sugestao_titulo = f"Revise gastos em {principal_categoria_nome}."
        sugestao_texto = (
            f"Essa categoria soma {formatar_brl_texto(principal_categoria_total)} no mês. "
            "Uma redução pequena nela já melhora o saldo previsto."
        )
    elif metas_usadas == 0:
        sugestao_titulo = "Crie sua primeira meta financeira."
        sugestao_texto = "Metas ajudam o FinanPy a transformar sobra de caixa em objetivo claro e acompanhamento visual."
    else:
        sugestao_titulo = "Mantenha a revisão semanal."
        sugestao_texto = "Atualize lançamentos, confira contas pendentes e ajuste limites antes do fim do mês."

    conta_score = 35
    if usuario.email:
        conta_score += 10
    if transacoes_count > 0:
        conta_score += 15
    if metas_usadas > 0:
        conta_score += 10
    if investimentos_usados > 0:
        conta_score += 8
    if configuracao.receber_alertas_vencimento:
        conta_score += 7
    if configuracao.receber_alertas_email:
        conta_score += 5
    if plano_usuario.eh_premium:
        conta_score += 10
    if total_atrasado_mes > 0:
        conta_score -= 10
    if economia_mes == 0 and total_despesas_mes > 0:
        conta_score -= 8

    perfil_confianca_percentual = min(95, 35 + (transacoes_count * 5) + (metas_usadas * 5) + (investimentos_usados * 3))

    return {
        "conta_score": max(min(conta_score, 100), 5),
        "transacoes_count": transacoes_count,
        "metas_count": metas_usadas,
        "metas_concluidas": metas_concluidas,
        "economia_mes": economia_mes,
        "dias_usando": dias_usando,
        "dias_controle": dias_controle,
        "perfil_financeiro": perfil_financeiro,
        "perfil_confianca_percentual": perfil_confianca_percentual,
        "risco_descontrole": risco_descontrole,
        "gastos_variacao_percentual": formatar_variacao_percentual(total_despesas_mes, total_despesas_mes_anterior),
        "cartoes_usados": cartoes_usados,
        "cartoes_usados_percentual": calcular_percentual_limite(cartoes_usados, plano_usuario.limite_cartoes),
        "metas_usadas": metas_usadas,
        "metas_usadas_percentual": calcular_percentual_limite(metas_usadas, plano_usuario.limite_metas),
        "investimentos_usados": investimentos_usados,
        "investimentos_usados_percentual": calcular_percentual_limite(
            investimentos_usados,
            plano_usuario.limite_investimentos,
        ),
        "tendencia_financeira": tendencia_financeira,
        "sugestao_ia_titulo": sugestao_titulo,
        "sugestao_ia_texto": sugestao_texto,
        "ultima_analise_data": ultima_analise_ia.criada_em if ultima_analise_ia else None,
        "saldo_oculto": not configuracao.exibir_saldo_dashboard,
        "notificacoes_ativas": configuracao.receber_alertas_email or configuracao.receber_alertas_vencimento,
    }

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
