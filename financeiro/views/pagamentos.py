"""Views de pagamentos do app financeiro."""

from .common import *


@login_required
@require_POST
def iniciar_checkout_premium(request):
    """Cria uma assinatura recorrente no Mercado Pago e redireciona para o checkout."""
    plano_usuario = obter_plano_usuario(request.user)

    if plano_usuario.eh_premium and plano_usuario.mercado_pago_preapproval_id:
        messages.info(request, "Sua conta ja possui um plano premium em andamento.")
        return redirect("configuracoes")

    if (
        plano_usuario.mercado_pago_preapproval_id
        and plano_usuario.mercado_pago_checkout_url
        and plano_usuario.mercado_pago_status in {"pending", "authorized", "active"}
    ):
        return redirect(plano_usuario.mercado_pago_checkout_url)

    if not request.user.email:
        messages.error(request, "Cadastre um e-mail valido no perfil antes de iniciar a assinatura premium.")
        return redirect("configuracoes")

    if atingiu_limite_temporario(request, "checkout_premium", limite=5, janela_segundos=600):
        messages.warning(request, "Muitas tentativas de checkout em pouco tempo. Aguarde alguns minutos e tente novamente.")
        return redirect("configuracoes")

    referencia_externa = f"finanpy-premium-user-{request.user.id}"

    try:
        cliente = obter_cliente_mercado_pago()
        resposta = cliente.criar_assinatura_premium(request.user, referencia_externa)
    except MercadoPagoErro as erro:
        logger.warning("Falha ao iniciar checkout premium para usuario %s: %s", request.user.id, erro)
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_USUARIO,
            usuario=request.user,
            plano=plano_usuario,
            referencia_externa=referencia_externa,
            valor=Decimal("10.50"),
            mensagem=f"Falha ao iniciar checkout premium: {erro}",
        )
        messages.error(request, str(erro))
        return redirect("configuracoes")

    plano_usuario.mercado_pago_preapproval_id = resposta.identificador
    plano_usuario.mercado_pago_checkout_url = resposta.init_point
    plano_usuario.mercado_pago_status = resposta.status
    plano_usuario.mercado_pago_referencia_externa = resposta.referencia_externa
    plano_usuario.ultima_sincronizacao_gateway = timezone.now()
    plano_usuario.save()
    registrar_evento_assinatura(
        tipo=EventoAssinatura.TIPO_CHECKOUT_CRIADO,
        origem=EventoAssinatura.ORIGEM_USUARIO,
        usuario=request.user,
        plano=plano_usuario,
        mercado_pago_preapproval_id=resposta.identificador,
        status_gateway=resposta.status,
        referencia_externa=resposta.referencia_externa,
        valor=Decimal("10.50"),
        mensagem="Checkout premium criado e usuario redirecionado ao Mercado Pago.",
        payload={"init_point": resposta.init_point},
    )

    return redirect(resposta.init_point)

@login_required
@require_POST
def checkout_premium(request):
    """Rota semantica para abrir o checkout premium a partir de Configuracoes."""
    return iniciar_checkout_premium(request)

@login_required
def retorno_assinatura(request):
    """Recebe o usuario de volta do Mercado Pago e sincroniza o status da assinatura."""
    plano_usuario = obter_plano_usuario(request.user)

    if not plano_usuario.mercado_pago_preapproval_id:
        messages.warning(request, "Nenhuma assinatura premium foi encontrada para sincronizar.")
        return redirect("configuracoes")

    try:
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
            plano=plano_usuario,
            mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
            status_gateway=plano_usuario.mercado_pago_status,
            referencia_externa=plano_usuario.mercado_pago_referencia_externa,
            mensagem=f"Falha ao sincronizar retorno da assinatura: {erro}",
        )
        messages.error(request, str(erro))
        return redirect("configuracoes")

    if plano_usuario.eh_premium:
        messages.success(request, "Pagamento confirmado. Seu plano premium esta ativo.")
    else:
        messages.warning(
            request,
            "A assinatura ainda nao foi concluida no gateway. Assim que o pagamento for confirmado, o premium sera liberado.",
        )

    return redirect("configuracoes")

@login_required
@require_POST
def sincronizar_assinatura_premium(request):
    """Sincroniza manualmente o status da assinatura premium com o gateway."""
    plano_usuario = obter_plano_usuario(request.user)

    if not plano_usuario.mercado_pago_preapproval_id:
        messages.warning(request, "Nenhuma assinatura premium foi encontrada para sincronizar.")
        return redirect("configuracoes")

    try:
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_USUARIO,
            plano=plano_usuario,
            mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
            status_gateway=plano_usuario.mercado_pago_status,
            referencia_externa=plano_usuario.mercado_pago_referencia_externa,
            mensagem=f"Falha ao sincronizar assinatura manualmente: {erro}",
        )
        messages.error(request, str(erro))
        return redirect("configuracoes")

    messages.success(request, "Status da assinatura sincronizado com sucesso.")
    return redirect("configuracoes")

@login_required
@require_POST
def cancelar_assinatura_premium(request):
    """Cancela a assinatura premium no Mercado Pago e atualiza o plano local."""
    plano_usuario = obter_plano_usuario(request.user)

    if not plano_usuario.mercado_pago_preapproval_id:
        messages.warning(request, "Nao existe assinatura premium ativa para cancelar.")
        return redirect("configuracoes")

    try:
        cliente = obter_cliente_mercado_pago()
        cliente.cancelar_assinatura(plano_usuario.mercado_pago_preapproval_id)
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_USUARIO,
            plano=plano_usuario,
            mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
            status_gateway=plano_usuario.mercado_pago_status,
            referencia_externa=plano_usuario.mercado_pago_referencia_externa,
            mensagem=f"Falha ao cancelar assinatura premium: {erro}",
        )
        messages.error(request, str(erro))
        return redirect("configuracoes")

    messages.success(request, "Assinatura premium cancelada com sucesso.")
    return redirect("configuracoes")

@csrf_exempt
def webhook_mercado_pago(request):
    """Recebe notificacoes do Mercado Pago e sincroniza o plano automaticamente."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}

    data_id = (
        request.GET.get("data.id")
        or request.GET.get("id")
        or str(payload.get("data", {}).get("id", ""))
    )
    tipo = request.GET.get("type") or payload.get("type") or ""
    acao = payload.get("action", "")
    request_id = request.headers.get("x-request-id", "")
    evento_id = str(payload.get("id") or request.GET.get("id") or request_id or "")
    tipos_assinatura = {"subscription_preapproval", "preapproval"}
    tipos_pagamento = {"payment", "payments"}

    if not validar_assinatura_webhook_mercado_pago(request, data_id):
        return JsonResponse({"ok": False, "erro": "assinatura_invalida"}, status=400)

    plano_usuario = None
    pagamento_payload = {}

    if data_id and tipo in tipos_assinatura:
        plano_usuario = PlanoUsuario.objects.filter(mercado_pago_preapproval_id=data_id).first()
    elif data_id and tipo in tipos_pagamento:
        try:
            pagamento_payload = obter_cliente_mercado_pago().buscar_pagamento(data_id)
        except MercadoPagoErro as erro:
            registrar_evento_assinatura(
                tipo=EventoAssinatura.TIPO_ERRO,
                origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
                mercado_pago_evento_id=evento_id,
                mercado_pago_tipo=tipo,
                mercado_pago_acao=acao,
                mensagem=f"Falha ao consultar pagamento Mercado Pago: {erro}",
                payload=payload,
            )
            return JsonResponse({"ok": False, "erro": str(erro)}, status=500)

        metadata = pagamento_payload.get("metadata") or {}
        referencia_pagamento = str(pagamento_payload.get("external_reference") or metadata.get("external_reference") or "")
        preapproval_pagamento = str(
            pagamento_payload.get("preapproval_id")
            or metadata.get("preapproval_id")
            or metadata.get("subscription_id")
            or ""
        )

        plano_usuario = (
            PlanoUsuario.objects.filter(mercado_pago_preapproval_id=preapproval_pagamento).first()
            if preapproval_pagamento
            else None
        )
        if not plano_usuario and referencia_pagamento:
            plano_usuario = PlanoUsuario.objects.filter(
                mercado_pago_referencia_externa=referencia_pagamento
            ).first()
    registrar_evento_assinatura(
        tipo=EventoAssinatura.TIPO_WEBHOOK_RECEBIDO,
        origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
        plano=plano_usuario,
        mercado_pago_preapproval_id=data_id if tipo in tipos_assinatura else getattr(plano_usuario, "mercado_pago_preapproval_id", ""),
        mercado_pago_evento_id=evento_id,
        mercado_pago_tipo=tipo,
        mercado_pago_acao=acao,
        status_gateway=plano_usuario.mercado_pago_status if plano_usuario else "",
        referencia_externa=plano_usuario.mercado_pago_referencia_externa if plano_usuario else "",
        mensagem="Webhook recebido e validado pelo FinanPy.",
        payload={
            "query_params": request.GET.dict(),
            "body": payload,
            "pagamento": pagamento_payload,
            "x_request_id": request_id,
        },
    )

    if tipo in tipos_pagamento and plano_usuario:
        status_pagamento = str(pagamento_payload.get("status") or "")
        if status_pagamento in {"approved", "authorized"}:
            plano_usuario.mercado_pago_status = "authorized"
            plano_usuario.ultima_sincronizacao_gateway = timezone.now()
            plano_usuario.ativar_premium()
            plano_usuario.save()
            registrar_evento_assinatura(
                tipo=EventoAssinatura.TIPO_PREMIUM_ATIVADO,
                origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
                plano=plano_usuario,
                mercado_pago_preapproval_id=plano_usuario.mercado_pago_preapproval_id,
                mercado_pago_evento_id=evento_id,
                mercado_pago_tipo=tipo,
                mercado_pago_acao=acao,
                status_gateway=status_pagamento,
                referencia_externa=plano_usuario.mercado_pago_referencia_externa,
                valor=plano_usuario.valor_mensal,
                mensagem="Pagamento recebido e Premium ativado automaticamente.",
                payload=pagamento_payload,
            )
            criar_notificacao(
                plano_usuario.usuario,
                "Pagamento Recebido",
                "Seu Plano Premium está ativo. Todos os recursos foram liberados.",
                Notificacao.TIPO_SUCESSO,
                reverse("configuracoes"),
            )
        return JsonResponse(
            {
                "ok": True,
                "tipo": tipo,
                "acao": acao,
                "pagamento": data_id,
                "status_pagamento": status_pagamento,
                "plano": plano_usuario.nome_plano,
            },
            status=200,
        )

    if tipo not in tipos_assinatura:
        return JsonResponse(
            {"ok": True, "ignorado": True, "tipo": tipo, "acao": acao},
            status=200,
        )

    if not plano_usuario:
        return JsonResponse(
            {"ok": True, "ignorado": True, "motivo": "assinatura_nao_encontrada", "data_id": data_id},
            status=200,
        )

    try:
        sincronizar_plano_com_gateway(plano_usuario)
    except MercadoPagoErro as erro:
        registrar_evento_assinatura(
            tipo=EventoAssinatura.TIPO_ERRO,
            origem=EventoAssinatura.ORIGEM_MERCADO_PAGO,
            plano=plano_usuario,
            mercado_pago_preapproval_id=data_id,
            mercado_pago_evento_id=evento_id,
            mercado_pago_tipo=tipo,
            mercado_pago_acao=acao,
            status_gateway=plano_usuario.mercado_pago_status,
            referencia_externa=plano_usuario.mercado_pago_referencia_externa,
            mensagem=f"Falha ao processar webhook Mercado Pago: {erro}",
            payload=payload,
        )
        return JsonResponse({"ok": False, "erro": str(erro)}, status=500)

    return JsonResponse(
        {
            "ok": True,
            "tipo": tipo,
            "acao": acao,
            "assinatura": data_id,
            "plano": plano_usuario.nome_plano,
            "status_gateway": plano_usuario.mercado_pago_status,
        },
        status=200,
    )
