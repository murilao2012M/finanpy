"""Views de dashboard do app financeiro."""

from .common import *


@login_required
def dashboard(request):
    """Mostra resumo mensal, saldos, metas e investimentos."""
    inicio_mes, fim_mes = obter_mes_referencia(request)
    plano_usuario = obter_plano_usuario(request.user)

    if request.method == "POST" and "criar_lancamento_rapido" in request.POST:
        mes_retorno = request.POST.get("mes_retorno") or inicio_mes.strftime("%Y-%m")
        try:
            lancamento = criar_lancamento_rapido(request.user, request.POST)
            messages.success(request, f"{lancamento.descricao_completa} foi cadastrado pelo dashboard.")
        except ValueError as erro:
            messages.error(request, str(erro))
        return redirect(f"{reverse('dashboard')}?mes={mes_retorno}")

    gerar_notificacoes_inteligentes(request.user, inicio_mes, fim_mes)

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
    previsao_fechamento_mes = prever_fechamento_mes(request.user, inicio_mes, fim_mes)

    contexto = {
        "inicio_mes": inicio_mes,
        "fim_mes": fim_mes,
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "lancamentos_recentes": lancamentos_mes[:8],
        "total_receitas": total_receitas,
        "total_despesas": total_despesas,
        "saldo_previsto": previsao_fechamento_mes["saldo_previsto"],
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
        "acao_recomendada_hoje": montar_acao_recomendada_hoje(request.user, inicio_mes, fim_mes),
        "previsao_fechamento_mes": previsao_fechamento_mes,
        "proxima_conta_importante": obter_proxima_conta_importante(request.user, inicio_mes, fim_mes),
        "gastos_pequenos_recorrentes": detectar_gastos_pequenos_recorrentes(request.user, inicio_mes, fim_mes)[:3],
        "alertas_limite_categoria": gerar_alertas_limite_categoria(request.user, inicio_mes, fim_mes)[:3],
        "categorias_lancamento_rapido": Categoria.objects.filter(usuario=request.user).order_by("tipo", "nome"),
        "tipos_lancamento_rapido": Lancamento.TIPOS,
        "status_lancamento_rapido": Lancamento.STATUS_CHOICES,
        "data_lancamento_rapido": timezone.localdate().isoformat(),
        "resumo_semanal": montar_resumo_semanal(request.user),
        "comparativo_mes": comparar_mes_atual_com_anterior(request.user, inicio_mes, fim_mes),
        "sugestao_guardar": sugerir_valor_para_guardar(request.user, inicio_mes, fim_mes),
        "parcelas_futuras": calcular_parcelas_futuras(request.user, meses=6),
    }
    return render(request, "dashboard.html", contexto)
