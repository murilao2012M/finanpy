"""Views de relatorios do app financeiro."""

from .common import *


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
        analise_estruturada, resposta_bruta = gerar_analise_financeira_local(contexto_ia)

        AnaliseFinanceiraIA.objects.create(
            usuario=request.user,
            periodo_inicio=inicio_mes,
            periodo_fim=fim_mes,
            status=AnaliseFinanceiraIA.STATUS_SUCESSO,
            modelo=MODELO_ANALISE_LOCAL,
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
        messages.success(request, "Diagnóstico financeiro inteligente gerado com sucesso.")
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
