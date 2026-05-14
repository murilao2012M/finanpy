"""Testes de seguranca e consistencia do backend do FinanPy."""

import hashlib
import hmac
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .mercado_pago import MercadoPagoClient, MercadoPagoErro, RespostaAssinaturaMercadoPago
from .models import EventoAssinatura, PlanoUsuario
from .views import sincronizar_plano_com_gateway, validar_assinatura_webhook_mercado_pago


class PlanoUsuarioBackendTests(TestCase):
    """Garante que o plano do usuario nao seja alterado de forma indevida."""

    def setUp(self):
        """Cria um usuario autenticavel para os testes do painel."""
        self.usuario = User.objects.create_user(
            username="cliente",
            email="cliente@finanpy.com",
            password="senha-teste-123",
        )

    def test_status_cancelado_do_freemium_nao_volta_para_ativo_ao_salvar(self):
        """Protege o fluxo de cancelamento para nao reativar status automaticamente."""
        plano = PlanoUsuario.objects.create(usuario=self.usuario)

        plano.ativar_freemium(status=PlanoUsuario.STATUS_CANCELADO)
        plano.save()
        plano.refresh_from_db()

        self.assertEqual(plano.nome_plano, PlanoUsuario.PLANO_FREEMIUM)
        self.assertEqual(plano.status, PlanoUsuario.STATUS_CANCELADO)
        self.assertFalse(plano.ia_habilitada)
        self.assertFalse(plano.eh_premium)

    def test_post_manual_em_configuracoes_nao_ativa_premium(self):
        """Impede ativacao premium por POST criado manualmente no navegador."""
        plano = PlanoUsuario.objects.create(usuario=self.usuario)
        self.client.force_login(self.usuario)

        resposta = self.client.post(reverse("configuracoes"), {"ativar_premium": "1"})

        plano.refresh_from_db()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(plano.nome_plano, PlanoUsuario.PLANO_FREEMIUM)
        self.assertFalse(plano.ia_habilitada)

    def test_sincronizacao_recusa_assinatura_de_outro_usuario(self):
        """Evita que uma assinatura do gateway seja vinculada ao usuario errado."""
        plano = PlanoUsuario.objects.create(
            usuario=self.usuario,
            mercado_pago_preapproval_id="assinatura-123",
            mercado_pago_referencia_externa=f"finanpy-premium-user-{self.usuario.id}",
        )

        class ClienteMercadoPagoFalso:
            """Cliente falso para simular resposta do Mercado Pago sem rede."""

            def buscar_assinatura(self, preapproval_id):
                return {
                    "status": "authorized",
                    "external_reference": "finanpy-premium-user-999999",
                    "init_point": "https://www.mercadopago.com.br/checkout",
                }

        with patch("financeiro.views.MercadoPagoClient", return_value=ClienteMercadoPagoFalso()):
            with self.assertRaises(MercadoPagoErro):
                sincronizar_plano_com_gateway(plano)

        plano.refresh_from_db()
        self.assertEqual(plano.nome_plano, PlanoUsuario.PLANO_FREEMIUM)
        self.assertFalse(plano.ia_habilitada)
        self.assertTrue(
            EventoAssinatura.objects.filter(
                plano=plano,
                tipo=EventoAssinatura.TIPO_ERRO,
                referencia_externa="finanpy-premium-user-999999",
            ).exists()
        )

    def test_sincronizacao_autorizada_ativa_premium(self):
        """Confirma que uma assinatura valida libera os beneficios premium."""
        plano = PlanoUsuario.objects.create(
            usuario=self.usuario,
            mercado_pago_preapproval_id="assinatura-123",
            mercado_pago_referencia_externa=f"finanpy-premium-user-{self.usuario.id}",
        )

        class ClienteMercadoPagoFalso:
            """Cliente falso para simular assinatura autorizada."""

            def buscar_assinatura(self, preapproval_id):
                return {
                    "status": "authorized",
                    "external_reference": f"finanpy-premium-user-{self.usuario.id}",
                    "init_point": "https://www.mercadopago.com.br/checkout",
                }

            def __init__(self, usuario):
                self.usuario = usuario

        cliente_falso = ClienteMercadoPagoFalso(self.usuario)

        with patch("financeiro.views.MercadoPagoClient", return_value=cliente_falso):
            sincronizar_plano_com_gateway(plano)

        plano.refresh_from_db()
        self.assertEqual(plano.nome_plano, PlanoUsuario.PLANO_PREMIUM)
        self.assertEqual(plano.status, PlanoUsuario.STATUS_ATIVO)
        self.assertTrue(plano.ia_habilitada)
        self.assertTrue(EventoAssinatura.objects.filter(plano=plano, tipo=EventoAssinatura.TIPO_SINCRONIZACAO).exists())
        self.assertTrue(EventoAssinatura.objects.filter(plano=plano, tipo=EventoAssinatura.TIPO_PREMIUM_ATIVADO).exists())


class MercadoPagoClientTests(TestCase):
    """Testa detalhes do payload enviado ao Mercado Pago."""

    def setUp(self):
        """Cria usuario com e-mail para montar assinatura."""
        self.usuario = User.objects.create_user(
            username="pagador",
            email="pagador@finanpy.com",
            password="senha-teste-123",
        )

    @override_settings(
        MERCADO_PAGO_ACCESS_TOKEN="token-fake",
        FINANPY_SITE_URL="https://finanpy.onrender.com",
    )
    def test_criacao_de_assinatura_envia_webhook_publico(self):
        """Assinaturas precisam apontar para o webhook publico do FinanPy."""
        payloads = []

        def request_falsa(self, metodo, caminho, payload=None):
            payloads.append(payload)
            return {
                "id": "preapproval-123",
                "init_point": "https://www.mercadopago.com.br/checkout/fake",
                "status": "pending",
                "external_reference": payload["external_reference"],
            }

        with patch.object(MercadoPagoClient, "_request", request_falsa):
            resposta = MercadoPagoClient().criar_assinatura_premium(
                self.usuario,
                "finanpy-premium-user-1",
            )

        self.assertEqual(resposta.identificador, "preapproval-123")
        self.assertEqual(
            payloads[0]["notification_url"],
            "https://finanpy.onrender.com/webhooks/mercado-pago/?source_news=webhooks",
        )
        self.assertEqual(payloads[0]["back_url"], "https://finanpy.onrender.com/assinatura/retorno/")


class AcoesSensiveisBackendTests(TestCase):
    """Valida que acoes sensiveis exigem POST e protecao CSRF na interface."""

    def setUp(self):
        """Prepara usuario autenticado para acessar rotas protegidas."""
        self.usuario = User.objects.create_user(
            username="assinante",
            email="assinante@finanpy.com",
            password="senha-teste-123",
        )
        self.client.force_login(self.usuario)

    def test_logout_e_pagamento_nao_aceitam_get(self):
        """Evita que links simples executem logout, checkout ou sincronizacao."""
        rotas_post = [
            "logout",
            "checkout_premium",
            "iniciar_checkout_premium",
            "sincronizar_assinatura_premium",
            "cancelar_assinatura_premium",
        ]

        for nome_rota in rotas_post:
            with self.subTest(nome_rota=nome_rota):
                resposta = self.client.get(reverse(nome_rota))
                self.assertEqual(resposta.status_code, 405)

    def test_checkout_cria_evento_de_assinatura(self):
        """Garante que o checkout real fica auditavel no banco."""

        class ClienteMercadoPagoFalso:
            """Cliente falso para criar checkout sem chamada de rede."""

            def criar_assinatura_premium(self, usuario, referencia_externa):
                return RespostaAssinaturaMercadoPago(
                    identificador="preapproval-abc",
                    init_point="https://www.mercadopago.com.br/checkout/fake",
                    status="pending",
                    referencia_externa=referencia_externa,
                )

        with patch("financeiro.views.MercadoPagoClient", return_value=ClienteMercadoPagoFalso()):
            resposta = self.client.post(reverse("checkout_premium"))

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], "https://www.mercadopago.com.br/checkout/fake")
        self.assertTrue(
            EventoAssinatura.objects.filter(
                usuario=self.usuario,
                tipo=EventoAssinatura.TIPO_CHECKOUT_CRIADO,
                mercado_pago_preapproval_id="preapproval-abc",
                status_gateway="pending",
            ).exists()
        )

    def test_recurso_premium_bloqueado_gera_evento(self):
        """Tentativas freemium em recurso premium entram no historico."""
        resposta = self.client.post(reverse("simulador_decisoes"), data={})

        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(
            EventoAssinatura.objects.filter(
                usuario=self.usuario,
                tipo=EventoAssinatura.TIPO_ACESSO_BLOQUEADO,
            ).exists()
        )

    @override_settings(DEBUG=False, MERCADO_PAGO_WEBHOOK_SECRET="")
    def test_webhook_sem_secret_e_recusado_em_producao(self):
        """Em producao, webhook sem secret configurado nao pode ser aceito."""
        request = RequestFactory().post(reverse("webhook_mercado_pago"))

        assinatura_valida = validar_assinatura_webhook_mercado_pago(request, "preapproval-123")

        self.assertFalse(assinatura_valida)

    @override_settings(DEBUG=False, MERCADO_PAGO_WEBHOOK_SECRET="webhook-secret-test")
    def test_webhook_valido_registra_evento_e_sincroniza_plano(self):
        """Webhook valido precisa registrar auditoria e sincronizar o Premium."""
        plano = PlanoUsuario.objects.create(
            usuario=self.usuario,
            mercado_pago_preapproval_id="abc123",
            mercado_pago_referencia_externa=f"finanpy-premium-user-{self.usuario.id}",
        )
        payload = {
            "type": "subscription_preapproval",
            "action": "updated",
            "data": {"id": "abc123"},
        }
        timestamp = "1710000000"
        request_id = "request-123"
        manifesto = f"id:abc123;request-id:{request_id};ts:{timestamp};"
        assinatura = hmac.new(
            b"webhook-secret-test",
            manifesto.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        class ClienteMercadoPagoFalso:
            """Cliente falso para simular assinatura ativa no webhook."""

            def buscar_assinatura(self, preapproval_id):
                return {
                    "status": "authorized",
                    "external_reference": f"finanpy-premium-user-{self.usuario.id}",
                    "init_point": "https://www.mercadopago.com.br/checkout",
                }

            def __init__(self, usuario):
                self.usuario = usuario

        with patch("financeiro.views.MercadoPagoClient", return_value=ClienteMercadoPagoFalso(self.usuario)):
            resposta = self.client.post(
                f"{reverse('webhook_mercado_pago')}?data.id=abc123",
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_X_SIGNATURE=f"ts={timestamp},v1={assinatura}",
                HTTP_X_REQUEST_ID=request_id,
            )

        plano.refresh_from_db()
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(plano.eh_premium)
        self.assertTrue(
            EventoAssinatura.objects.filter(
                plano=plano,
                tipo=EventoAssinatura.TIPO_WEBHOOK_RECEBIDO,
                mercado_pago_evento_id="request-123",
            ).exists()
        )
