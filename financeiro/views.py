"""Views do app financeiro."""

from calendar import monthrange
from datetime import date
from decimal import Decimal
import hashlib
import hmac
import json
import uuid

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.conf import settings
from django.db.models import Count, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .forms import (
    AnaliseFinanceiraIAForm,
    AlterarSenhaPerfilForm,
    CartaoCreditoForm,
    CategoriaForm,
    ConfiguracaoUsuarioForm,
    InvestimentoForm,
    LancamentoForm,
    LoginUsuarioForm,
    MetaFinanceiraForm,
    PerfilUsuarioForm,
    RegistroUsuarioForm,
)
from .ia_financeira import OBJETIVO_ANALISE_IA, REGRAS_ANALISE_IA, OpenAIFinanceiroErro, chamar_openai_analise_financeira
from .mercado_pago import MercadoPagoClient, MercadoPagoErro
from .models import (
    AnaliseFinanceiraIA,
    CartaoCredito,
    Categoria,
    ConfiguracaoUsuario,
    Investimento,
    Lancamento,
    MetaFinanceira,
    PlanoUsuario,
)


ABREVIACOES_MESES_PT = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

LIMITES_RECURSOS = {
    "cartoes": {
        "titulo": "cartoes",
        "nome": "cartoes",
        "rota": "lista_cartoes",
        "mensagem": "O plano Freemium permite cadastrar apenas 1 cartao. Faça upgrade para o Premium e libere mais cartoes.",
    },
    "metas": {
        "titulo": "metas",
        "nome": "metas",
        "rota": "lista_metas",
        "mensagem": "O plano Freemium permite cadastrar ate 2 metas. Faça upgrade para o Premium e libere metas ilimitadas.",
    },
    "investimentos": {
        "titulo": "investimentos",
        "nome": "investimentos",
        "rota": "lista_investimentos",
        "mensagem": "O plano Freemium permite cadastrar apenas 1 investimento. O Premium libera carteira completa e IA.",
    },
}


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


def sincronizar_plano_com_gateway(plano_usuario):
    """Atualiza o plano local de acordo com o status da assinatura no Mercado Pago."""
    if not plano_usuario.mercado_pago_preapproval_id:
        return plano_usuario

    cliente = MercadoPagoClient()
    resposta = cliente.buscar_assinatura(plano_usuario.mercado_pago_preapproval_id)
    status_gateway = resposta.get("status", "")

    plano_usuario.mercado_pago_status = status_gateway
    plano_usuario.mercado_pago_checkout_url = resposta.get("init_point", plano_usuario.mercado_pago_checkout_url)
    plano_usuario.ultima_sincronizacao_gateway = timezone.now()

    if status_gateway in {"authorized", "active"}:
        plano_usuario.ativar_premium()
    elif status_gateway in {"cancelled", "paused"}:
        plano_usuario.ativar_freemium()
        plano_usuario.status = PlanoUsuario.STATUS_CANCELADO

    plano_usuario.save()
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


def validar_assinatura_webhook_mercado_pago(request, data_id):
    """Valida a origem do webhook quando a secret key estiver configurada."""
    secret = getattr(settings, "MERCADO_PAGO_WEBHOOK_SECRET", "")

    if not secret:
        return True

    partes_assinatura = extrair_cabecalho_assinatura(request.headers.get("x-signature", ""))
    timestamp = partes_assinatura.get("ts")
    hash_recebido = partes_assinatura.get("v1")
    request_id = request.headers.get("x-request-id", "")

    if not timestamp or not hash_recebido or not request_id or not data_id:
        return False

    manifesto = f"id:{data_id};request-id:{request_id};ts:{timestamp};"
    hash_esperado = hmac.new(
        secret.encode("utf-8"),
        manifesto.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(hash_esperado, hash_recebido)


def validar_limite_plano(request, recurso_nome):
    """Confere se o usuario ainda pode cadastrar um recurso no plano atual."""
    plano_usuario = obter_plano_usuario(request.user)
    configuracao = LIMITES_RECURSOS[recurso_nome]

    if plano_usuario.eh_premium:
        return None

    total_atual = getattr(request.user, configuracao["nome"]).count()
    limite_atual = getattr(plano_usuario, f"limite_{recurso_nome}")

    if total_atual >= limite_atual:
        messages.warning(request, configuracao["mensagem"])
        return redirect("configuracoes")

    return None


def usuario_tem_ia_liberada(usuario):
    """Centraliza a regra de acesso ao modulo premium de IA."""
    plano_usuario = obter_plano_usuario(usuario)
    return plano_usuario.eh_premium and plano_usuario.ia_habilitada


def serializar_decimal(valor):
    """Padroniza valores monetarios para texto no contexto enviado a IA."""
    if valor is None:
        return "0.00"
    return f"{valor:.2f}"


def construir_contexto_analise_financeira(usuario, inicio_mes, fim_mes):
    """Monta um contexto rico, porem enxuto, para a IA analisar."""
    lancamentos_mes = Lancamento.objects.filter(
        usuario=usuario,
        data_competencia__range=(inicio_mes, fim_mes),
    ).select_related("categoria", "cartao")

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
    return redirect("registrar_usuario")


class EntrarUsuarioView(LoginView):
    """Tela de login com formulário customizado e layout do projeto."""

    template_name = "registration/login.html"
    authentication_form = LoginUsuarioForm
    redirect_authenticated_user = True


def sair_usuario(request):
    """
    Encerra a sessão do usuário com redirecionamento previsível.

    Aceitamos POST como fluxo principal e GET como fallback de usabilidade
    para o caso de navegadores ou layouts bloquearem o submit do formulário.
    """
    if request.method not in ["GET", "POST"]:
        return HttpResponseNotAllowed(["GET", "POST"])

    if request.user.is_authenticated:
        logout(request)
        messages.success(request, "Sua sessão foi encerrada com sucesso.")

    return redirect("registrar_usuario")


def registrar_usuario(request):
    """Permite criar um usuário e já efetuar login automaticamente."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = RegistroUsuarioForm(request.POST)
        if form.is_valid():
            usuario = form.save()
            login(request, usuario)
            messages.success(request, "Conta criada com sucesso. Bem-vindo ao FinanPy.")
            return redirect("dashboard")
        messages.error(request, "Revise os dados informados para concluir seu cadastro.")
    else:
        form = RegistroUsuarioForm()

    return render(request, "registration/register.html", {"form": form})


@login_required
def dashboard(request):
    """Mostra resumo mensal, saldos, metas e investimentos."""
    inicio_mes, fim_mes = obter_mes_referencia(request)
    plano_usuario = obter_plano_usuario(request.user)
    lancamentos_mes = Lancamento.objects.filter(
        usuario=request.user,
        data_competencia__range=(inicio_mes, fim_mes),
    ).select_related("categoria", "cartao")

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
            form.save()
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
        try:
            categoria.delete()
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
            form.save()
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
        cartao.delete()
        messages.success(request, "Cartão excluído com sucesso.")
        return redirect("lista_cartoes")

    return render(request, "confirm_delete.html", {"objeto": cartao, "titulo": "Excluir cartão"})


@login_required
def lista_investimentos(request):
    """Lista os investimentos cadastrados pelo usuário."""
    investimentos = Investimento.objects.filter(usuario=request.user).order_by("status", "-data_aplicacao", "-id")
    investimentos_ativos = investimentos.filter(status=Investimento.STATUS_ATIVO)

    total_aplicado = somar_campo(investimentos_ativos, "valor_aplicado")
    total_atual = somar_campo(investimentos_ativos, "valor_atual")
    rentabilidade_total = total_atual - total_aplicado

    contexto = {
        "investimentos": investimentos,
        "total_investimentos": investimentos.count(),
        "investimentos_ativos": investimentos_ativos.count(),
        "total_aplicado": total_aplicado,
        "total_atual": total_atual,
        "rentabilidade_total": rentabilidade_total,
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
            form.save()
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
        investimento.delete()
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

    perfil_form = PerfilUsuarioForm(instance=request.user, prefix="perfil")
    preferencias_form = ConfiguracaoUsuarioForm(instance=configuracao, prefix="preferencias")
    senha_form = AlterarSenhaPerfilForm(request.user, prefix="senha")

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

        elif "ativar_premium" in request.POST:
            plano_usuario.ativar_premium()
            plano_usuario.save()
            messages.success(request, "Plano Premium ativado com sucesso. Agora sua conta possui IA e limites ampliados.")
            return redirect("configuracoes")

        elif "voltar_freemium" in request.POST:
            plano_usuario.ativar_freemium()
            plano_usuario.save()
            messages.success(request, "Sua conta voltou para o plano Freemium.")
            return redirect("configuracoes")

    contexto = {
        "perfil_form": perfil_form,
        "preferencias_form": preferencias_form,
        "senha_form": senha_form,
        "configuracao_usuario": configuracao,
        "plano_usuario": plano_usuario,
        "ultima_analise_ia": ultima_analise_ia,
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

        if not usuario_tem_ia_liberada(request.user):
            messages.warning(
                request,
                "A Analise IA faz parte do plano Premium. Finalize o upgrade para liberar esse recurso.",
            )
            return redirect("configuracoes")

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

    referencia_externa = f"finanpy-premium-user-{request.user.id}"

    try:
        cliente = MercadoPagoClient()
        resposta = cliente.criar_assinatura_premium(request.user, referencia_externa)
    except MercadoPagoErro as erro:
        messages.error(request, str(erro))
        return redirect("configuracoes")

    plano_usuario.mercado_pago_preapproval_id = resposta.identificador
    plano_usuario.mercado_pago_checkout_url = resposta.init_point
    plano_usuario.mercado_pago_status = resposta.status
    plano_usuario.mercado_pago_referencia_externa = resposta.referencia_externa
    plano_usuario.ultima_sincronizacao_gateway = timezone.now()
    plano_usuario.save()

    return redirect(resposta.init_point)


@login_required
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
def sincronizar_assinatura_premium(request):
    """Sincroniza manualmente o status da assinatura premium com o gateway."""
    plano_usuario = obter_plano_usuario(request.user)

    if not plano_usuario.mercado_pago_preapproval_id:
        messages.warning(request, "Nenhuma assinatura premium foi encontrada para sincronizar.")
        return redirect("configuracoes")

    try:
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
        messages.error(request, str(erro))
        return redirect("configuracoes")

    messages.success(request, "Status da assinatura sincronizado com sucesso.")
    return redirect("configuracoes")


@login_required
def cancelar_assinatura_premium(request):
    """Cancela a assinatura premium no Mercado Pago e atualiza o plano local."""
    plano_usuario = obter_plano_usuario(request.user)

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    if not plano_usuario.mercado_pago_preapproval_id:
        messages.warning(request, "Nao existe assinatura premium ativa para cancelar.")
        return redirect("configuracoes")

    try:
        cliente = MercadoPagoClient()
        cliente.cancelar_assinatura(plano_usuario.mercado_pago_preapproval_id)
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
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

    if not validar_assinatura_webhook_mercado_pago(request, data_id):
        return JsonResponse({"ok": False, "erro": "assinatura_invalida"}, status=400)

    if tipo != "subscription_preapproval":
        return JsonResponse(
            {"ok": True, "ignorado": True, "tipo": tipo, "acao": acao},
            status=200,
        )

    plano_usuario = PlanoUsuario.objects.filter(mercado_pago_preapproval_id=data_id).first()

    if not plano_usuario:
        return JsonResponse(
            {"ok": True, "ignorado": True, "motivo": "assinatura_nao_encontrada", "data_id": data_id},
            status=200,
        )

    try:
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
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
    metas = MetaFinanceira.objects.filter(usuario=request.user).order_by("status", "data_limite")

    contexto = {
        "metas": metas,
        "total_metas": metas.count(),
        "metas_concluidas": metas.filter(status=MetaFinanceira.STATUS_CONCLUIDA).count(),
        "metas_em_andamento": metas.filter(status=MetaFinanceira.STATUS_EM_ANDAMENTO).count(),
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
            form.save()
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
        meta.delete()
        messages.success(request, "Meta excluída com sucesso.")
        return redirect("lista_metas")

    return render(request, "confirm_delete.html", {"objeto": meta, "titulo": "Excluir meta"})


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
    queryset = Lancamento.objects.filter(usuario=request.user).select_related("categoria", "cartao")
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
    ).select_related("categoria", "cartao")
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
    ).select_related("categoria", "cartao")
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
    ).select_related("categoria", "cartao")
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
    ).select_related("categoria", "cartao")
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
            descricao=lancamento_base.descricao,
            valor=lancamento_base.valor,
            categoria=lancamento_base.categoria,
            data_competencia=data_competencia,
            data_vencimento=data_vencimento,
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
    if request.method == "POST":
        form = LancamentoForm(request.POST, usuario=request.user)
        if form.is_valid():
            lancamento = form.save(commit=False)
            lancamento.usuario = request.user

            if lancamento.compra_parcelada and lancamento.total_parcelas > 1:
                lancamento.grupo_parcelas = uuid.uuid4()
                criar_parcelas(lancamento)
                messages.success(request, "Compra parcelada cadastrada com sucesso.")
            else:
                lancamento.save()
                messages.success(request, "Lançamento criado com sucesso.")

            return redirect("lista_lancamentos")
    else:
        form = LancamentoForm(usuario=request.user)

    return render(request, "lancamentos/form.html", {"form": form, "titulo": "Novo lançamento"})


@login_required
def editar_lancamento(request, pk):
    """Edita um lançamento existente."""
    lancamento = obter_objeto_do_usuario(Lancamento, request.user, pk)

    if request.method == "POST":
        form = LancamentoForm(request.POST, instance=lancamento, usuario=request.user)
        if form.is_valid():
            lancamento_editado = form.save(commit=False)
            lancamento_editado.usuario = request.user

            # Para manter a regra simples, a edição atualiza somente a parcela aberta.
            if lancamento.total_parcelas > 1:
                lancamento_editado.compra_parcelada = True

            lancamento_editado.save()
            messages.success(request, "Lançamento atualizado com sucesso.")
            return redirect("lista_lancamentos")
    else:
        form = LancamentoForm(instance=lancamento, usuario=request.user)

    return render(
        request,
        "lancamentos/form.html",
        {"form": form, "titulo": "Editar lançamento", "lancamento": lancamento},
    )


@login_required
def excluir_lancamento(request, pk):
    """Exclui um lançamento ou uma parcela específica."""
    lancamento = obter_objeto_do_usuario(Lancamento, request.user, pk)

    if request.method == "POST":
        lancamento.delete()
        messages.success(request, "Lançamento excluído com sucesso.")
        return redirect("lista_lancamentos")

    return render(request, "confirm_delete.html", {"objeto": lancamento, "titulo": "Excluir lançamento"})


@login_required
def relatorios(request):
    """Exibe visões resumidas por categoria, status e tipo."""
    inicio_mes, fim_mes = obter_mes_referencia(request)
    lancamentos = Lancamento.objects.filter(
        usuario=request.user,
        data_competencia__range=(inicio_mes, fim_mes),
    )

    resumo_por_categoria = (
        lancamentos.values("categoria__nome", "tipo")
        .annotate(total=Sum("valor"), quantidade=Count("id"))
        .order_by("tipo", "-total")
    )
    resumo_por_status = (
        lancamentos.values("status")
        .annotate(total=Sum("valor"), quantidade=Count("id"))
        .order_by("status")
    )
    resumo_por_tipo = (
        lancamentos.values("tipo")
        .annotate(total=Sum("valor"), quantidade=Count("id"))
        .order_by("tipo")
    )

    mapa_tipos = dict(Lancamento.TIPOS)
    mapa_status = dict(Lancamento.STATUS_CHOICES)

    resumo_por_categoria = [
        {**item, "tipo_label": mapa_tipos.get(item["tipo"], item["tipo"])}
        for item in resumo_por_categoria
    ]
    resumo_por_status = [
        {**item, "status_label": mapa_status.get(item["status"], item["status"])}
        for item in resumo_por_status
    ]
    resumo_por_tipo = [
        {**item, "tipo_label": mapa_tipos.get(item["tipo"], item["tipo"])}
        for item in resumo_por_tipo
    ]

    contexto = {
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "inicio_mes": inicio_mes,
        "fim_mes": fim_mes,
        "resumo_por_categoria": resumo_por_categoria,
        "resumo_por_status": resumo_por_status,
        "resumo_por_tipo": resumo_por_tipo,
    }
    return render(request, "relatorios/relatorios.html", contexto)
