"""Views de lancamentos do app financeiro."""

from .common import *


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
def duplicar_lancamento_proximo_mes(request, pk):
    """Ação rápida para copiar uma transação para o mês seguinte."""
    duplicado = duplicar_lancamento_para_proximo_mes(request.user, pk)
    messages.success(request, f"{duplicado.descricao_completa} foi duplicado para o próximo mês.")
    return redirect(request.POST.get("next") or "lista_lancamentos")

@login_required
@require_POST
def marcar_lancamento_pago(request, pk):
    """Ação rápida para marcar uma conta como paga."""
    lancamento = marcar_lancamento_como_pago(request.user, pk)
    messages.success(request, f"{lancamento.descricao_completa} foi marcado como pago.")
    return redirect(request.POST.get("next") or "lista_lancamentos")

@login_required
@require_POST
def criar_recorrencia_lancamento(request, pk):
    """Ação rápida para gerar lançamentos recorrentes a partir de uma transação."""
    try:
        criados = criar_lancamentos_recorrentes(
            request.user,
            pk,
            request.POST.get("quantidade_meses", 6),
        )
    except ValueError as erro:
        messages.error(request, str(erro))
    else:
        if criados:
            messages.success(request, f"{len(criados)} lançamento(s) recorrente(s) foram criados.")
        else:
            messages.info(request, "Nenhum lançamento novo foi criado porque os meses futuros já possuíam registros iguais.")
    return redirect(request.POST.get("next") or "lista_lancamentos")

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
