"""Registro dos models no Django Admin."""

from django.contrib import admin

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
        "usuario",
        "valor",
        "categoria",
        "status",
        "data_competencia",
    )
    list_filter = ("tipo", "status", "forma_pagamento", "usuario")
    search_fields = ("descricao", "observacao")


@admin.register(MetaFinanceira)
class MetaFinanceiraAdmin(admin.ModelAdmin):
    """Configuração visual das metas no admin."""

    list_display = ("titulo", "usuario", "valor_alvo", "valor_atual", "status", "data_limite")
    list_filter = ("status", "prioridade", "usuario")
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


@admin.register(AnaliseFinanceiraIA)
class AnaliseFinanceiraIAAdmin(admin.ModelAdmin):
    """Configuracao visual do historico de analises inteligentes."""

    list_display = ("usuario", "periodo_inicio", "periodo_fim", "saude_financeira", "status", "modelo", "criada_em")
    list_filter = ("saude_financeira", "status", "modelo")
    search_fields = ("usuario__username", "usuario__email", "resumo_executivo", "mensagem_erro")
