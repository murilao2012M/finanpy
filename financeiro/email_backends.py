"""Backends de e-mail do FinanPy."""

import json
from email.utils import parseaddr
from urllib import error, request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class BrevoAPIEmailError(Exception):
    """Erro controlado para falhas da API transacional da Brevo."""

    def __init__(self, message, status_code=None, body=""):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body


class BrevoAPIEmailBackend(BaseEmailBackend):
    """Envia e-mails transacionais pela API HTTP da Brevo.

    Esse backend evita bloqueios de portas SMTP no Render Free, porque usa HTTPS
    pela porta 443 em vez das portas SMTP 25, 465, 587 ou 2525.
    """

    def __init__(self, *args, api_key=None, api_url=None, timeout=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.api_key = api_key or getattr(settings, "BREVO_API_KEY", "")
        self.api_url = api_url or getattr(settings, "BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
        self.timeout = timeout or getattr(settings, "BREVO_API_TIMEOUT", 20)

    def send_messages(self, email_messages):
        """Envia uma lista de mensagens do Django para a API da Brevo."""
        if not email_messages:
            return 0

        if not self.api_key:
            if self.fail_silently:
                return 0
            raise BrevoAPIEmailError("BREVO_API_KEY não está configurada.", status_code="MISSING_API_KEY")

        enviados = 0
        for email_message in email_messages:
            try:
                self._send_message(email_message)
            except Exception:
                if not self.fail_silently:
                    raise
            else:
                enviados += 1

        return enviados

    def _send_message(self, email_message):
        """Converte EmailMessage do Django no payload esperado pela Brevo."""
        sender_name, sender_email = parseaddr(email_message.from_email or settings.DEFAULT_FROM_EMAIL)
        if not sender_email:
            _, sender_email = parseaddr(settings.DEFAULT_FROM_EMAIL)

        payload = {
            "sender": {
                "name": sender_name or "FinanPy",
                "email": sender_email,
            },
            "to": [{"email": email} for email in email_message.to],
            "subject": email_message.subject,
            "textContent": email_message.body or "",
        }

        if email_message.cc:
            payload["cc"] = [{"email": email} for email in email_message.cc]

        if email_message.bcc:
            payload["bcc"] = [{"email": email} for email in email_message.bcc]

        reply_to = email_message.extra_headers.get("Reply-To") if email_message.extra_headers else ""
        if reply_to:
            reply_name, reply_email = parseaddr(reply_to)
            payload["replyTo"] = {"email": reply_email, "name": reply_name or reply_email}

        if getattr(email_message, "content_subtype", "") == "html":
            payload.pop("textContent", None)
            payload["htmlContent"] = email_message.body or ""

        for content, mimetype in getattr(email_message, "alternatives", []):
            if mimetype == "text/html":
                payload["htmlContent"] = content
                break

        self._postar_payload(payload)

    def _postar_payload(self, payload):
        """Executa a chamada HTTP para a Brevo e trata respostas de erro."""
        dados = json.dumps(payload).encode("utf-8")
        requisicao = request.Request(
            self.api_url,
            data=dados,
            method="POST",
            headers={
                "accept": "application/json",
                "api-key": self.api_key,
                "content-type": "application/json",
            },
        )

        try:
            with request.urlopen(requisicao, timeout=self.timeout) as resposta:
                if 200 <= resposta.status < 300:
                    return True
                corpo = resposta.read().decode("utf-8", errors="replace")
                raise BrevoAPIEmailError(
                    f"Brevo API retornou status {resposta.status}.",
                    status_code=resposta.status,
                    body=corpo,
                )
        except error.HTTPError as exc:
            corpo = exc.read().decode("utf-8", errors="replace")
            raise BrevoAPIEmailError(
                f"Brevo API retornou status {exc.code}.",
                status_code=exc.code,
                body=corpo,
            ) from exc
