"""Servico responsavel pela analise financeira premium com IA."""

from __future__ import annotations

import json
from urllib import error, request

from django.conf import settings


OBJETIVO_ANALISE_IA = [
    "Traduzir os numeros do usuario em um resumo financeiro simples.",
    "Apontar riscos reais de curto prazo antes que virem problema.",
    "Sugerir acoes praticas para economizar, organizar e crescer.",
]

REGRAS_ANALISE_IA = [
    "Usar somente os dados enviados pelo FinanPy para esta analise.",
    "Responder em portugues do Brasil, com tom claro, direto e profissional.",
    "Nao inventar valores, datas ou tendencias que nao estejam no contexto.",
    "Tratar a resposta como orientacao financeira educativa, nao como promessa de retorno.",
    "Sempre priorizar acoes praticas que o usuario consiga executar nos proximos 7 e 30 dias.",
]


class OpenAIFinanceiroErro(Exception):
    """Erro semantico para falhas da integracao de IA."""


SCHEMA_ANALISE_FINANCEIRA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "saude_financeira": {
            "type": "string",
            "enum": ["OTIMA", "ESTAVEL", "ATENCAO", "CRITICA"],
        },
        "resumo_executivo": {"type": "string"},
        "metricas_resumo": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "Saldo do mes": {"type": "string"},
                "Saldo previsto": {"type": "string"},
                "Despesas sobre receitas": {"type": "string"},
                "Categoria mais pesada": {"type": "string"},
                "Contas em risco": {"type": "string"},
            },
            "required": [
                "Saldo do mes",
                "Saldo previsto",
                "Despesas sobre receitas",
                "Categoria mais pesada",
                "Contas em risco",
            ],
        },
        "sinais_positivos": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 4,
        },
        "alertas_prioritarios": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 5,
        },
        "oportunidades": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 4,
        },
        "plano_acao": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "prazo": {
                        "type": "string",
                        "enum": ["AGORA", "7_DIAS", "30_DIAS"],
                    },
                    "titulo": {"type": "string"},
                    "descricao": {"type": "string"},
                },
                "required": ["prazo", "titulo", "descricao"],
            },
            "minItems": 3,
            "maxItems": 6,
        },
    },
    "required": [
        "saude_financeira",
        "resumo_executivo",
        "metricas_resumo",
        "sinais_positivos",
        "alertas_prioritarios",
        "oportunidades",
        "plano_acao",
    ],
}


def montar_prompt_sistema():
    """Cria a instrucao principal da IA do FinanPy."""
    objetivos = "\n".join(f"- {item}" for item in OBJETIVO_ANALISE_IA)
    regras = "\n".join(f"- {item}" for item in REGRAS_ANALISE_IA)
    return (
        "Voce e o Assistente Inteligente do FinanPy, um SaaS de controle financeiro pessoal.\n"
        "Sua tarefa e analisar o contexto financeiro recebido e devolver uma leitura util, confiavel "
        "e acionavel para o usuario final.\n\n"
        "Objetivos:\n"
        f"{objetivos}\n\n"
        "Regras:\n"
        f"{regras}\n"
    )


def extrair_texto_resposta(payload_resposta):
    """Lida com variacoes do formato de resposta da API."""
    texto_direto = payload_resposta.get("output_text")
    if texto_direto:
        return texto_direto

    partes = []
    for bloco in payload_resposta.get("output", []):
        for conteudo in bloco.get("content", []):
            if conteudo.get("type") in {"output_text", "text"} and conteudo.get("text"):
                partes.append(conteudo["text"])

    return "".join(partes).strip()


def chamar_openai_analise_financeira(contexto_financeiro):
    """Chama a Responses API da OpenAI com structured output."""
    if not settings.OPENAI_API_KEY:
        raise OpenAIFinanceiroErro(
            "A IA premium ainda nao pode ser usada porque a chave OPENAI_API_KEY nao foi configurada."
        )

    corpo = {
        "model": settings.OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": montar_prompt_sistema(),
            },
            {
                "role": "user",
                "content": (
                    "Analise o contexto financeiro abaixo e responda seguindo exatamente o schema JSON solicitado.\n\n"
                    f"{json.dumps(contexto_financeiro, ensure_ascii=False)}"
                ),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "finanpy_analise_financeira",
                "strict": True,
                "schema": SCHEMA_ANALISE_FINANCEIRA,
            }
        },
    }

    requisicao = request.Request(
        url="https://api.openai.com/v1/responses",
        data=json.dumps(corpo).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(requisicao, timeout=settings.OPENAI_TIMEOUT_SECONDS) as resposta:
            payload_resposta = json.loads(resposta.read().decode("utf-8"))
    except error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="ignore")
        raise OpenAIFinanceiroErro(
            f"Nao foi possivel gerar a analise da IA. Resposta da OpenAI: {detalhe or exc.reason}"
        ) from exc
    except error.URLError as exc:
        raise OpenAIFinanceiroErro(
            "Nao foi possivel conectar ao servico de IA da OpenAI no momento."
        ) from exc

    texto_estruturado = extrair_texto_resposta(payload_resposta)
    if not texto_estruturado:
        raise OpenAIFinanceiroErro("A OpenAI nao retornou um texto valido para a analise financeira.")

    try:
        analise = json.loads(texto_estruturado)
    except json.JSONDecodeError as exc:
        raise OpenAIFinanceiroErro("A resposta estruturada da IA nao veio em JSON valido.") from exc

    return analise, payload_resposta
