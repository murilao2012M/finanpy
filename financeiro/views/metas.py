"""Views de metas do app financeiro."""

from .common import *


@login_required
def lista_metas(request):
    """Lista todas as metas financeiras do usuário."""
    metas = list(MetaFinanceira.objects.filter(usuario=request.user).order_by("status", "data_limite"))
    metas_inteligentes = calcular_metas_inteligentes(request.user, metas)

    for meta in metas:
        meta.inteligencia = metas_inteligentes.get(meta.id)
        meta.previsao_conclusao = prever_conclusao_meta(meta)

    contexto = {
        "metas": metas,
        "total_metas": len(metas),
        "metas_concluidas": sum(1 for meta in metas if meta.status == MetaFinanceira.STATUS_CONCLUIDA),
        "metas_em_andamento": sum(1 for meta in metas if meta.status == MetaFinanceira.STATUS_EM_ANDAMENTO),
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
            criar_notificacao(
                request.user,
                "Meta Criada",
                f"A meta {meta.titulo} foi criada com estratégia {meta.get_estrategia_display()}.",
                Notificacao.TIPO_SUCESSO,
                reverse("lista_metas"),
            )
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
            meta = form.save()
            criar_notificacao(
                request.user,
                "Meta Atualizada",
                f"A meta {meta.titulo} foi atualizada.",
                Notificacao.TIPO_INFO,
                reverse("lista_metas"),
            )
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
        titulo_meta = meta.titulo
        meta.delete()
        criar_notificacao(
            request.user,
            "Meta Excluída",
            f"A meta {titulo_meta} foi removida.",
            Notificacao.TIPO_ALERTA,
            reverse("lista_metas"),
        )
        messages.success(request, "Meta excluída com sucesso.")
        return redirect("lista_metas")

    return render(request, "confirm_delete.html", {"objeto": meta, "titulo": "Excluir meta"})

@login_required
@require_POST
def concluir_meta(request, pk):
    """Marca uma meta como alcançada pelo usuário."""
    meta = obter_objeto_do_usuario(MetaFinanceira, request.user, pk)
    meta.valor_atual = meta.valor_alvo
    meta.status = MetaFinanceira.STATUS_CONCLUIDA
    meta.save()
    criar_notificacao(
        request.user,
        "Meta Alcançada",
        f"Parabéns! A meta {meta.titulo} foi marcada como alcançada.",
        Notificacao.TIPO_SUCESSO,
        reverse("lista_metas"),
    )
    messages.success(request, "Meta marcada como alcançada. Parabéns pela conquista!")
    return redirect("lista_metas")
