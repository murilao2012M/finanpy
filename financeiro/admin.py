"""Registro dos models no Django Admin."""

from django.contrib import admin

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
    PlanoUsuario,
    PlanoContencao,
)


@admin.register(Notificacao)
class NotificacaoAdmin(admin.ModelAdmin):
    """Central de alertas gerados pelo FinanPy."""

    list_display = ("titulo", "usuario", "tipo", "lida", "criada_em")
    list_filter = ("tipo", "lida", "criada_em")
    search_fields = ("titulo", "mensagem", "usuario__username", "usuario__email")


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    """Configuração visual da categoria no admin."""

    list_display = ("nome", "tipo", "usuario")
    list_filter = ("tipo", "usuario")
    search_fields = ("nome",)


@admin.register(CartaoCredito)
class CartaoCreditoAdmin(admin.ModelAdmin):
    """Configuração visual do cartão no admin."""

    list_display = ("nome", "usuario", "limite", "dia_fechamento", "dia_vencimento", "ativo")
    list_filter = ("ativo", "usuario")
    search_fields = ("nome",)


@admin.register(Lancamento)
class LancamentoAdmin(admin.ModelAdmin):
    """Configuração visual do lançamento no admin."""

    list_display = (
        "descricao",
        "tipo",
        "escopo",
        "orcamento_compartilhado",
        "usuario",
        "valor",
        "categoria",
        "status",
        "data_competencia",
    )
    list_filter = ("tipo", "escopo", "status", "forma_pagamento", "usuario")
    search_fields = ("descricao", "observacao")


class MembroOrcamentoInline(admin.TabularInline):
    """Exibe os participantes dentro do orçamento compartilhado."""

    model = MembroOrcamento
    extra = 0


@admin.register(OrcamentoCompartilhado)
class OrcamentoCompartilhadoAdmin(admin.ModelAdmin):
    """Configuração visual dos orçamentos compartilhados no admin."""

    list_display = ("nome", "tipo", "dono", "codigo_convite", "ativo", "criado_em")
    list_filter = ("tipo", "ativo", "dono")
    search_fields = ("nome", "codigo_convite", "dono__username", "dono__email")
    readonly_fields = ("codigo_convite",)
    inlines = [MembroOrcamentoInline]


class LimiteCategoriaContencaoInline(admin.TabularInline):
    """Permite visualizar limites por categoria dentro do plano."""

    model = LimiteCategoriaContencao
    extra = 0


@admin.register(PlanoContencao)
class PlanoContencaoAdmin(admin.ModelAdmin):
    """Configuracao visual do modo anti-descontrole no admin."""

    list_display = ("titulo", "usuario", "duracao_dias", "data_inicio", "data_fim", "orcamento_total", "status")
    list_filter = ("status", "duracao_dias", "usuario")
    search_fields = ("titulo", "usuario__username", "usuario__email")
    inlines = [LimiteCategoriaContencaoInline]


@admin.register(MetaFinanceira)
class MetaFinanceiraAdmin(admin.ModelAdmin):
    """Configuração visual das metas no admin."""

    list_display = ("titulo", "usuario", "estrategia", "valor_alvo", "valor_atual", "status", "data_limite")
    list_filter = ("status", "estrategia", "prioridade", "usuario")
    search_fields = ("titulo", "descricao")


@admin.register(Investimento)
class InvestimentoAdmin(admin.ModelAdmin):
    """Configuração visual dos investimentos no admin."""

    list_display = ("nome", "tipo", "instituicao", "usuario", "valor_aplicado", "valor_atual", "status")
    list_filter = ("tipo", "status", "instituicao", "usuario")
    search_fields = ("nome", "instituicao", "objetivo")


@admin.register(ConfiguracaoUsuario)
class ConfiguracaoUsuarioAdmin(admin.ModelAdmin):
    """Configuração visual das preferências dos usuários no admin."""

    list_display = (
        "usuario",
        "moeda_padrao",
        "formato_data",
        "receber_alertas_email",
        "receber_alertas_vencimento",
        "exibir_saldo_dashboard",
        "foto_perfil",
    )
    list_filter = ("moeda_padrao", "formato_data", "receber_alertas_email", "receber_alertas_vencimento")
    search_fields = ("usuario__username", "usuario__email")


@admin.register(PlanoUsuario)
class PlanoUsuarioAdmin(admin.ModelAdmin):
    """Configuracao visual dos planos dos usuarios no admin."""

    list_display = (
        "usuario",
        "nome_plano",
        "status",
        "valor_mensal",
        "ia_habilitada",
        "limite_cartoes",
        "limite_metas",
        "limite_investimentos",
    )
    list_filter = ("nome_plano", "status", "ia_habilitada")
    search_fields = ("usuario__username", "usuario__email")


@admin.register(EventoAssinatura)
class EventoAssinaturaAdmin(admin.ModelAdmin):
    """Auditoria dos eventos de assinatura e cobranca."""

    list_display = (
        "tipo",
        "origem",
        "usuario",
        "status_gateway",
        "mercado_pago_preapproval_id",
        "mercado_pago_tipo",
        "criado_em",
    )
    list_filter = ("tipo", "origem", "status_gateway", "mercado_pago_tipo", "criado_em")
    search_fields = (
        "usuario__username",
        "usuario__email",
        "mercado_pago_preapproval_id",
        "mercado_pago_evento_id",
        "referencia_externa",
        "mensagem",
    )
    readonly_fields = (
        "usuario",
        "plano",
        "tipo",
        "origem",
        "mercado_pago_preapproval_id",
        "mercado_pago_evento_id",
        "mercado_pago_tipo",
        "mercado_pago_acao",
        "status_gateway",
        "referencia_externa",
        "valor",
        "moeda",
        "mensagem",
        "payload",
        "criado_em",
    )


@admin.register(AnaliseFinanceiraIA)
class AnaliseFinanceiraIAAdmin(admin.ModelAdmin):
    """Configuracao visual do historico de analises inteligentes."""

    list_display = ("usuario", "periodo_inicio", "periodo_fim", "saude_financeira", "status", "modelo", "criada_em")
    list_filter = ("saude_financeira", "status", "modelo")
    search_fields = ("usuario__username", "usuario__email", "resumo_executivo", "mensagem_erro")
