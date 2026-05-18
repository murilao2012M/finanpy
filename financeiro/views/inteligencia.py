"""Views de inteligencia do app financeiro."""

from .common import *


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
                analise_estruturada, resposta_bruta = gerar_analise_financeira_local(contexto_ia)

                analise_atual = AnaliseFinanceiraIA.objects.create(
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
                messages.success(request, "Analise inteligente gerada com sucesso.")

            analises_recentes = AnaliseFinanceiraIA.objects.filter(usuario=request.user)[:6]

    contexto = {
        "form": form,
        "plano_usuario": plano_usuario,
        "ia_liberada": usuario_tem_ia_liberada(request.user),
        "analise_atual": analise_atual,
        "diagnostico_visual": montar_diagnostico_visual_ia(analise_atual),
        "analises_recentes": analises_recentes,
        "objetivos_ia": OBJETIVO_ANALISE_IA,
        "regras_ia": REGRAS_ANALISE_IA,
    }
    return render(request, "ia/analise.html", contexto)
