"""Views de recursos premium do app financeiro."""

from .common import *


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
