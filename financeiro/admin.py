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
    PlanoContencao,
    PlanoUsuario,
)


admin.site.site_header = "FinanPy Admin"
admin.site.site_title = "FinanPy"
admin.site.index_title = "Painel administrativo do FinanPy"


def formatar_brl(valor):
    """Formata valores monetários no padrão brasileiro."""
    if valor is None:
        valor = 0

    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


@admin.action(description="Marcar notificações selecionadas como lidas")
def marcar_notificacoes_como_lidas(modeladmin, request, queryset):
    """Marca notificações selecionadas como lidas."""
    queryset.update(lida=True)


@admin.action(description="Marcar notificações selecionadas como não lidas")
def marcar_notificacoes_como_nao_lidas(modeladmin, request, queryset):
    """Marca notificações selecionadas como não lidas."""
    queryset.update(lida=False)


@admin.register(Notificacao)
class NotificacaoAdmin(admin.ModelAdmin):
    """Central de alertas gerados pelo FinanPy."""

    list_display = (
        "titulo",
        "usuario",
        "tipo",
        "lida",
        "criada_em",
    )
    list_filter = (
        "tipo",
        "lida",
        "criada_em",
    )
    search_fields = (
        "titulo",
        "mensagem",
        "usuario__username",
        "usuario__email",
    )
    list_select_related = (
        "usuario",
    )
    date_hierarchy = "criada_em"
    list_per_page = 30
    actions = [
        marcar_notificacoes_como_lidas,
        marcar_notificacoes_como_nao_lidas,
    ]


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    """Configuração visual da categoria no admin."""

    list_display = (
        "nome",
        "tipo",
        "usuario",
        "criada_em",
    )
    list_filter = (
        "tipo",
        "usuario",
        "criada_em",
    )
    search_fields = (
        "nome",
        "usuario__username",
        "usuario__email",
    )
    list_select_related = (
        "usuario",
    )
    date_hierarchy = "criada_em"
    list_per_page = 30


@admin.register(CartaoCredito)
class CartaoCreditoAdmin(admin.ModelAdmin):
    """Configuração visual do cartão no admin."""

    list_display = (
        "nome",
        "usuario",
        "limite_formatado",
        "dia_fechamento",
        "dia_vencimento",
        "ativo",
        "criado_em",
    )
    list_filter = (
        "ativo",
        "usuario",
        "criado_em",
    )
    search_fields = (
        "nome",
        "usuario__username",
        "usuario__email",
    )
    list_select_related = (
        "usuario",
    )
    date_hierarchy = "criado_em"
    list_per_page = 30

    @admin.display(description="Limite")
    def limite_formatado(self, obj):
        """Exibe o limite do cartão formatado."""
        return formatar_brl(obj.limite)


@admin.register(Lancamento)
class LancamentoAdmin(admin.ModelAdmin):
    """Configuração visual do lançamento no admin."""

    list_display = (
        "descricao",
        "tipo",
        "escopo",
        "usuario",
        "valor_formatado",
        "categoria",
        "status",
        "forma_pagamento",
        "data_competencia",
        "data_vencimento",
    )
    list_filter = (
        "tipo",
        "escopo",
        "status",
        "forma_pagamento",
        "data_competencia",
        "data_vencimento",
        "usuario",
    )
    search_fields = (
        "descricao",
        "observacao",
        "usuario__username",
        "usuario__email",
        "categoria__nome",
        "cartao__nome",
        "orcamento_compartilhado__nome",
    )
    list_select_related = (
        "usuario",
        "categoria",
        "cartao",
        "orcamento_compartilhado",
    )
    autocomplete_fields = (
        "usuario",
        "categoria",
        "cartao",
        "orcamento_compartilhado",
    )
    date_hierarchy = "data_competencia"
    list_per_page = 30

    fieldsets = (
        (
            "Dados principais",
            {
                "fields": (
                    "usuario",
                    "tipo",
                    "escopo",
                    "orcamento_compartilhado",
                    "descricao",
                    "valor",
                    "categoria",
                )
            },
        ),
        (
            "Datas e status",
            {
                "fields": (
                    "status",
                    "data_competencia",
                    "data_vencimento",
                    "data_pagamento",
                )
            },
        ),
        (
            "Pagamento",
            {
                "fields": (
                    "forma_pagamento",
                    "cartao",
                )
            },
        ),
        (
            "Parcelamento",
            {
                "fields": (
                    "compra_parcelada",
                    "parcela_atual",
                    "total_parcelas",
                    "grupo_parcelas",
                )
            },
        ),
        (
            "Observações",
            {
                "fields": (
                    "observacao",
                )
            },
        ),
    )

    @admin.display(description="Valor")
    def valor_formatado(self, obj):
        """Exibe o valor do lançamento formatado."""
        return formatar_brl(obj.valor)


class MembroOrcamentoInline(admin.TabularInline):
    """Exibe os participantes dentro do orçamento compartilhado."""

    model = MembroOrcamento
    extra = 0
    autocomplete_fields = (
        "usuario",
    )


@admin.register(OrcamentoCompartilhado)
class OrcamentoCompartilhadoAdmin(admin.ModelAdmin):
    """Configuração visual dos orçamentos compartilhados no admin."""

    list_display = (
        "nome",
        "tipo",
        "dono",
        "codigo_convite",
        "ativo",
        "criado_em",
    )
    list_filter = (
        "tipo",
        "ativo",
        "dono",
        "criado_em",
    )
    search_fields = (
        "nome",
        "codigo_convite",
        "dono__username",
        "dono__email",
    )
    list_select_related = (
        "dono",
    )
    autocomplete_fields = (
        "dono",
    )
    readonly_fields = (
        "codigo_convite",
    )
    date_hierarchy = "criado_em"
    list_per_page = 30
    inlines = [
        MembroOrcamentoInline,
    ]


class LimiteCategoriaContencaoInline(admin.TabularInline):
    """Permite visualizar limites por categoria dentro do plano."""

    model = LimiteCategoriaContencao
    extra = 0
    autocomplete_fields = (
        "categoria",
    )


@admin.register(PlanoContencao)
class PlanoContencaoAdmin(admin.ModelAdmin):
    """Configuração visual do modo anti-descontrole no admin."""

    list_display = (
        "titulo",
        "usuario",
        "duracao_dias",
        "data_inicio",
        "data_fim",
        "orcamento_total_formatado",
        "status",
    )
    list_filter = (
        "status",
        "duracao_dias",
        "usuario",
        "data_inicio",
        "data_fim",
    )
    search_fields = (
        "titulo",
        "usuario__username",
        "usuario__email",
    )
    list_select_related = (
        "usuario",
    )
    autocomplete_fields = (
        "usuario",
    )
    date_hierarchy = "data_inicio"
    list_per_page = 30
    inlines = [
        LimiteCategoriaContencaoInline,
    ]

    @admin.display(description="Orçamento total")
    def orcamento_total_formatado(self, obj):
        """Exibe o orçamento total formatado."""
        return formatar_brl(obj.orcamento_total)


@admin.register(MetaFinanceira)
class MetaFinanceiraAdmin(admin.ModelAdmin):
    """Configuração visual das metas no admin."""

    list_display = (
        "titulo",
        "usuario",
        "estrategia",
        "valor_alvo_formatado",
        "valor_atual_formatado",
        "status",
        "data_limite",
    )
    list_filter = (
        "status",
        "estrategia",
        "prioridade",
        "usuario",
        "data_limite",
    )
    search_fields = (
        "titulo",
        "descricao",
        "usuario__username",
        "usuario__email",
    )
    list_select_related = (
        "usuario",
    )
    autocomplete_fields = (
        "usuario",
    )
    date_hierarchy = "data_limite"
    list_per_page = 30

    @admin.display(description="Valor alvo")
    def valor_alvo_formatado(self, obj):
        """Exibe o valor alvo formatado."""
        return formatar_brl(obj.valor_alvo)

    @admin.display(description="Valor atual")
    def valor_atual_formatado(self, obj):
        """Exibe o valor atual formatado."""
        return formatar_brl(obj.valor_atual)


@admin.register(Investimento)
class InvestimentoAdmin(admin.ModelAdmin):
    """Configuração visual dos investimentos no admin."""

    list_display = (
        "nome",
        "tipo",
        "instituicao",
        "usuario",
        "valor_aplicado_formatado",
        "valor_atual_formatado",
        "status",
    )
    list_filter = (
        "tipo",
        "status",
        "instituicao",
        "usuario",
    )
    search_fields = (
        "nome",
        "instituicao",
        "objetivo",
        "usuario__username",
        "usuario__email",
    )
    list_select_related = (
        "usuario",
    )
    autocomplete_fields = (
        "usuario",
    )
    list_per_page = 30

    @admin.display(description="Valor aplicado")
    def valor_aplicado_formatado(self, obj):
        """Exibe o valor aplicado formatado."""
        return formatar_brl(obj.valor_aplicado)

    @admin.display(description="Valor atual")
    def valor_atual_formatado(self, obj):
        """Exibe o valor atual formatado."""
        return formatar_brl(obj.valor_atual)


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
        "possui_foto",
    )
    list_filter = (
        "moeda_padrao",
        "formato_data",
        "receber_alertas_email",
        "receber_alertas_vencimento",
        "exibir_saldo_dashboard",
    )
    search_fields = (
        "usuario__username",
        "usuario__email",
    )
    list_select_related = (
        "usuario",
    )
    autocomplete_fields = (
        "usuario",
    )
    list_per_page = 30

    @admin.display(description="Tem foto?")
    def possui_foto(self, obj):
        """Mostra se o usuário possui foto cadastrada."""
        return "Sim" if obj.foto_perfil else "Não"


@admin.register(PlanoUsuario)
class PlanoUsuarioAdmin(admin.ModelAdmin):
    """Configuração visual dos planos dos usuários no admin."""

    list_display = (
        "usuario",
        "nome_plano",
        "status",
        "valor_mensal_formatado",
        "ia_habilitada",
        "limite_cartoes",
        "limite_metas",
        "limite_investimentos",
        "mercado_pago_status",
    )
    list_filter = (
        "nome_plano",
        "status",
        "ia_habilitada",
        "mercado_pago_status",
    )
    search_fields = (
        "usuario__username",
        "usuario__email",
        "mercado_pago_preapproval_id",
        "mercado_pago_referencia_externa",
    )
    list_select_related = (
        "usuario",
    )
    autocomplete_fields = (
        "usuario",
    )
    readonly_fields = (
        "mercado_pago_preapproval_id",
        "mercado_pago_referencia_externa",
        "mercado_pago_checkout_url",
        "mercado_pago_status",
        "ultima_sincronizacao_gateway",
    )
    list_per_page = 30

    fieldsets = (
        (
            "Usuário e plano",
            {
                "fields": (
                    "usuario",
                    "nome_plano",
                    "status",
                    "valor_mensal",
                )
            },
        ),
        (
            "Recursos liberados",
            {
                "fields": (
                    "ia_habilitada",
                    "limite_cartoes",
                    "limite_metas",
                    "limite_investimentos",
                )
            },
        ),
        (
            "Mercado Pago",
            {
                "fields": (
                    "mercado_pago_preapproval_id",
                    "mercado_pago_referencia_externa",
                    "mercado_pago_checkout_url",
                    "mercado_pago_status",
                    "ultima_sincronizacao_gateway",
                )
            },
        ),
    )

    @admin.display(description="Valor mensal")
    def valor_mensal_formatado(self, obj):
        """Exibe o valor mensal formatado."""
        return formatar_brl(obj.valor_mensal)


@admin.register(EventoAssinatura)
class EventoAssinaturaAdmin(admin.ModelAdmin):
    """Auditoria dos eventos de assinatura e cobrança."""

    list_display = (
        "tipo",
        "origem",
        "usuario",
        "status_gateway",
        "mercado_pago_preapproval_id",
        "mercado_pago_tipo",
        "valor_formatado",
        "criado_em",
    )
    list_filter = (
        "tipo",
        "origem",
        "status_gateway",
        "mercado_pago_tipo",
        "criado_em",
    )
    search_fields = (
        "usuario__username",
        "usuario__email",
        "mercado_pago_preapproval_id",
        "mercado_pago_evento_id",
        "referencia_externa",
        "mensagem",
    )
    list_select_related = (
        "usuario",
        "plano",
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
    date_hierarchy = "criado_em"
    list_per_page = 30

    fieldsets = (
        (
            "Identificação",
            {
                "fields": (
                    "usuario",
                    "plano",
                    "tipo",
                    "origem",
                    "criado_em",
                )
            },
        ),
        (
            "Mercado Pago",
            {
                "fields": (
                    "mercado_pago_preapproval_id",
                    "mercado_pago_evento_id",
                    "mercado_pago_tipo",
                    "mercado_pago_acao",
                    "status_gateway",
                    "referencia_externa",
                    "valor",
                    "moeda",
                )
            },
        ),
        (
            "Mensagem e payload",
            {
                "fields": (
                    "mensagem",
                    "payload",
                )
            },
        ),
    )

    @admin.display(description="Valor")
    def valor_formatado(self, obj):
        """Exibe o valor do evento formatado."""
        return formatar_brl(obj.valor)

    def has_add_permission(self, request):
        """Impede criação manual de eventos de assinatura."""
        return False

    def has_change_permission(self, request, obj=None):
        """Impede alteração manual da auditoria de assinatura."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Permite exclusão apenas para superusuários."""
        return request.user.is_superuser


@admin.register(AnaliseFinanceiraIA)
class AnaliseFinanceiraIAAdmin(admin.ModelAdmin):
    """Configuração visual do histórico de análises inteligentes."""

    list_display = (
        "usuario",
        "periodo_inicio",
        "periodo_fim",
        "saude_financeira",
        "status",
        "modelo",
        "criada_em",
    )

    list_filter = (
        "saude_financeira",
        "status",
        "modelo",
        "periodo_inicio",
        "periodo_fim",
        "criada_em",
    )

    search_fields = (
        "usuario__username",
        "usuario__email",
        "resumo_executivo",
        "mensagem_erro",
    )

    list_select_related = (
        "usuario",
    )

    autocomplete_fields = (
        "usuario",
    )

    readonly_fields = (
        "usuario",
        "periodo_inicio",
        "periodo_fim",
        "modelo",
        "status",
        "saude_financeira",
        "resumo_executivo",
        "contexto_enviado",
        "metricas_resumo",
        "sinais_positivos",
        "alertas_prioritarios",
        "oportunidades",
        "plano_acao",
        "resposta_bruta",
        "mensagem_erro",
        "criada_em",
    )

    date_hierarchy = "criada_em"
    list_per_page = 30

    fieldsets = (
        (
            "Usuário e período",
            {
                "fields": (
                    "usuario",
                    "periodo_inicio",
                    "periodo_fim",
                    "modelo",
                    "status",
                    "saude_financeira",
                    "criada_em",
                )
            },
        ),
        (
            "Resumo principal",
            {
                "fields": (
                    "resumo_executivo",
                    "metricas_resumo",
                    "sinais_positivos",
                )
            },
        ),
        (
            "Diagnóstico estruturado",
            {
                "fields": (
                    "alertas_prioritarios",
                    "oportunidades",
                    "plano_acao",
                )
            },
        ),
        (
            "Dados técnicos",
            {
                "fields": (
                    "contexto_enviado",
                    "resposta_bruta",
                    "mensagem_erro",
                )
            },
        ),
    )

    def has_add_permission(self, request):
        """Impede criação manual de análises pelo admin."""
        return False