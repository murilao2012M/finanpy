"""Integracao server-side do FinanPy com Mercado Pago."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib import error, request

from django.conf import settings


class MercadoPagoErro(Exception):
    """Erro controlado da integracao com o Mercado Pago."""


@dataclass
class RespostaAssinaturaMercadoPago:
    """Representa os dados centrais retornados pelo gateway."""

    identificador: str
    init_point: str
    status: str
    referencia_externa: str


class MercadoPagoClient:
    """Cliente HTTP simples para integracao de assinaturas recorrentes."""

    def __init__(self):
        self.base_url = getattr(settings, "MERCADO_PAGO_BASE_URL", "https://api.mercadopago.com")
        self.access_token = getattr(settings, "MERCADO_PAGO_ACCESS_TOKEN", "")
        self.site_url = getattr(settings, "FINANPY_SITE_URL", "").rstrip("/")

        if not self.access_token:
            raise MercadoPagoErro("Defina MERCADO_PAGO_ACCESS_TOKEN para habilitar a cobranca real.")

        if not self.site_url:
            raise MercadoPagoErro("Defina FINANPY_SITE_URL com a URL publica do sistema para receber o retorno do checkout.")

    def criar_assinatura_premium(self, usuario, referencia_externa: str) -> RespostaAssinaturaMercadoPago:
        """Cria uma assinatura mensal pendente e retorna o link de checkout."""
        agora = datetime.now(timezone.utc)
        fim = agora + timedelta(days=365 * 5)

        corpo = {
            "reason": "FinanPy Premium",
            "external_reference": referencia_externa,
            "payer_email": usuario.email,
            "auto_recurring": {
                "frequency": 1,
                "frequency_type": "months",
                "start_date": agora.isoformat(),
                "end_date": fim.isoformat(),
                "transaction_amount": 10.50,
                "currency_id": "BRL",
            },
            "back_url": f"{self.site_url}/assinatura/retorno/",
            "notification_url": f"{self.site_url}/webhooks/mercado-pago/?source_news=webhooks",
            "status": "pending",
        }

        resposta = self._request("POST", "/preapproval", corpo)
        return RespostaAssinaturaMercadoPago(
            identificador=resposta["id"],
            init_point=resposta["init_point"],
            status=resposta.get("status", ""),
            referencia_externa=str(resposta.get("external_reference", referencia_externa)),
        )

    def buscar_assinatura(self, preapproval_id: str) -> dict:
        """Consulta os dados atualizados de uma assinatura recorrente."""
        return self._request("GET", f"/preapproval/{preapproval_id}")

    def cancelar_assinatura(self, preapproval_id: str) -> dict:
        """Cancela a assinatura recorrente no gateway."""
        return self._request("PUT", f"/preapproval/{preapproval_id}", {"status": "cancelled"})

    def _request(self, metodo: str, caminho: str, payload: dict | None = None) -> dict:
        """Executa uma chamada autenticada para a API do Mercado Pago."""
        dados = None
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        if payload is not None:
            dados = json.dumps(payload).encode("utf-8")

        requisicao = request.Request(
            f"{self.base_url}{caminho}",
            data=dados,
            headers=headers,
            method=metodo,
        )

        try:
            with request.urlopen(requisicao, timeout=30) as resposta:
                corpo = resposta.read().decode("utf-8")
                return json.loads(corpo) if corpo else {}
        except error.HTTPError as exc:
            corpo = exc.read().decode("utf-8", errors="ignore")
            raise MercadoPagoErro(f"Mercado Pago respondeu com erro HTTP {exc.code}: {corpo}") from exc
        except error.URLError as exc:
            raise MercadoPagoErro(f"Nao foi possivel conectar ao Mercado Pago: {exc.reason}") from exc
