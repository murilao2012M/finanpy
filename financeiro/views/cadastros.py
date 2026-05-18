"""Views de cadastros do app financeiro."""

from .common import *


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
            criar_notificacao(
                request.user,
                "Categoria Criada",
                f"A categoria {categoria.nome} foi adicionada.",
                Notificacao.TIPO_SUCESSO,
                reverse("lista_categorias"),
            )
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
            categoria = form.save()
            criar_notificacao(
                request.user,
                "Categoria Atualizada",
                f"A categoria {categoria.nome} foi atualizada.",
                Notificacao.TIPO_INFO,
                reverse("lista_categorias"),
            )
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
        nome_categoria = categoria.nome
        try:
            categoria.delete()
            criar_notificacao(
                request.user,
                "Categoria Excluída",
                f"A categoria {nome_categoria} foi removida.",
                Notificacao.TIPO_ALERTA,
                reverse("lista_categorias"),
            )
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
    inicio_mes, fim_mes = obter_mes_referencia(request)
    cartoes = list(CartaoCredito.objects.filter(usuario=request.user))
    for cartao in cartoes:
        cartao.fatura_atual = montar_fatura_cartao(request.user, cartao.id, inicio_mes, fim_mes)

    contexto = {
        "cartoes": cartoes,
        "mes_filtro": inicio_mes.strftime("%Y-%m"),
        "parcelas_futuras": calcular_parcelas_futuras(request.user, meses=6),
    }
    return render(request, "cartoes/lista.html", contexto)

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
            criar_notificacao(
                request.user,
                "Cartão Criado",
                f"O cartão {cartao.nome} foi cadastrado.",
                Notificacao.TIPO_SUCESSO,
                reverse("lista_cartoes"),
            )
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
            cartao = form.save()
            criar_notificacao(
                request.user,
                "Cartão Atualizado",
                f"O cartão {cartao.nome} foi atualizado.",
                Notificacao.TIPO_INFO,
                reverse("lista_cartoes"),
            )
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
        nome_cartao = cartao.nome
        cartao.delete()
        criar_notificacao(
            request.user,
            "Cartão Excluído",
            f"O cartão {nome_cartao} foi removido.",
            Notificacao.TIPO_ALERTA,
            reverse("lista_cartoes"),
        )
        messages.success(request, "Cartão excluído com sucesso.")
        return redirect("lista_cartoes")

    return render(request, "confirm_delete.html", {"objeto": cartao, "titulo": "Excluir cartão"})

@login_required
def lista_investimentos(request):
    """Lista os investimentos cadastrados pelo usuário, com filtros e dados para gráfico."""
    investimentos = Investimento.objects.filter(usuario=request.user)

    data_inicio = request.GET.get("data_inicio")
    data_fim = request.GET.get("data_fim")
    tipo_filtro = request.GET.get("tipo")

    if data_inicio:
        investimentos = investimentos.filter(data_aplicacao__gte=data_inicio)

    if data_fim:
        investimentos = investimentos.filter(data_aplicacao__lte=data_fim)

    if tipo_filtro:
        investimentos = investimentos.filter(tipo=tipo_filtro)

    investimentos = investimentos.order_by("status", "-data_aplicacao", "-id")
    investimentos_ativos = investimentos.filter(status=Investimento.STATUS_ATIVO)

    total_aplicado = somar_campo(investimentos_ativos, "valor_aplicado")
    total_atual = somar_campo(investimentos_ativos, "valor_atual")
    rentabilidade_total = total_atual - total_aplicado
    mapa_tipos = dict(Investimento.TIPOS)
    resumo_por_tipo = list(
        investimentos_ativos.values("tipo")
        .annotate(total=Sum("valor_atual"), quantidade=Count("id"))
        .order_by("-total")
    )
    for item in resumo_por_tipo:
        item["tipo_label"] = mapa_tipos.get(item["tipo"], item["tipo"])
    grafico_labels = [mapa_tipos.get(item["tipo"], item["tipo"]) for item in resumo_por_tipo]
    grafico_valores = [float(item["total"] or Decimal("0.00")) for item in resumo_por_tipo]

    contexto = {
        "investimentos": investimentos,
        "total_investimentos": investimentos.count(),
        "investimentos_ativos": investimentos_ativos.count(),
        "total_aplicado": total_aplicado,
        "total_atual": total_atual,
        "rentabilidade_total": rentabilidade_total,
        "tipos_investimento": Investimento.TIPOS,
        "tipo_filtro": tipo_filtro,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "resumo_por_tipo": resumo_por_tipo,
        "grafico_labels": json.dumps(grafico_labels),
        "grafico_valores": json.dumps(grafico_valores),
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
            criar_notificacao(
                request.user,
                "Investimento Registrado",
                f"Você adicionou {investimento.nome} à sua carteira.",
                Notificacao.TIPO_SUCESSO,
                reverse("lista_investimentos"),
            )
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
            investimento = form.save()
            criar_notificacao(
                request.user,
                "Investimento Atualizado",
                f"As informações de {investimento.nome} foram atualizadas.",
                Notificacao.TIPO_INFO,
                reverse("lista_investimentos"),
            )
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
        nome_investimento = investimento.nome
        investimento.delete()
        criar_notificacao(
            request.user,
            "Investimento Excluído",
            f"O investimento {nome_investimento} foi removido.",
            Notificacao.TIPO_ALERTA,
            reverse("lista_investimentos"),
        )
        messages.success(request, "Investimento excluído com sucesso.")
        return redirect("lista_investimentos")

    return render(
        request,
        "confirm_delete.html",
        {"objeto": investimento, "titulo": "Excluir investimento"},
    )
