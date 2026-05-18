"""Motor local de diagnostico financeiro inteligente do FinanPy."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


MODELO_ANALISE_LOCAL = "FinanPy Inteligencia Local"

OBJETIVO_ANALISE_IA = [
    "Traduzir os numeros do usuario em um resumo financeiro simples.",
    "Apontar riscos reais de curto prazo antes que virem problema.",
    "Sugerir acoes praticas para economizar, organizar e crescer.",
]

REGRAS_ANALISE_IA = [
    "Usar somente os dados cadastrados no FinanPy para esta analise.",
    "Gerar o diagnostico dentro do proprio sistema, sem depender de API externa.",
    "Nao inventar valores, datas ou tendencias que nao estejam no contexto.",
    "Tratar a resposta como orientacao financeira educativa, nao como promessa de resultado.",
    "Priorizar acoes praticas que o usuario consiga executar nos proximos 7 e 30 dias.",
]


def decimal_seguro(valor):
    """Converte textos numericos do contexto em Decimal sem quebrar a tela."""
    try:
        return Decimal(str(valor or "0").replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def brl(valor):
    """Formata valores no padrao brasileiro usado no FinanPy."""
    valor = decimal_seguro(valor)
    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def percentual(valor):
    """Formata percentual com uma casa decimal."""
    valor = decimal_seguro(valor)
    return f"{valor:.1f}%".replace(".", ",")


def obter_indicadores(contexto_financeiro):
    """Extrai indicadores principais do contexto consolidado pela view."""
    indicadores = contexto_financeiro.get("indicadores", {})
    contagens = contexto_financeiro.get("contagens", {})
    categorias = contexto_financeiro.get("categorias_despesa", [])
    investimentos = contexto_financeiro.get("investimentos", {})

    return {
        "total_receitas": decimal_seguro(indicadores.get("total_receitas")),
        "total_despesas": decimal_seguro(indicadores.get("total_despesas")),
        "saldo_previsto": decimal_seguro(indicadores.get("saldo_previsto")),
        "saldo_mes": decimal_seguro(indicadores.get("saldo_mes")),
        "total_pendente": decimal_seguro(indicadores.get("total_pendente")),
        "total_atrasado": decimal_seguro(indicadores.get("total_atrasado")),
        "percentual_despesas": decimal_seguro(indicadores.get("percentual_despesas_sobre_receitas")),
        "contas_pendentes": int(contagens.get("contas_pendentes") or 0),
        "contas_atrasadas": int(contagens.get("contas_atrasadas") or 0),
        "metas_total": int(contagens.get("metas_total") or 0),
        "metas_concluidas": int(contagens.get("metas_concluidas") or 0),
        "investimentos_total": int(contagens.get("investimentos_total") or 0),
        "investimentos_ativos": int(contagens.get("investimentos_ativos") or 0),
        "total_atual_investimentos": decimal_seguro(investimentos.get("total_atual")),
        "rentabilidade_total": decimal_seguro(investimentos.get("rentabilidade_total")),
        "categorias": categorias,
        "principal_categoria": contexto_financeiro.get("principal_categoria_despesa") or "Nenhuma",
    }


def classificar_saude(dados):
    """Classifica a saude financeira com regras simples e explicaveis."""
    if dados["total_receitas"] <= 0 and dados["total_despesas"] > 0:
        return "CRITICA"

    if dados["saldo_previsto"] < 0 or dados["percentual_despesas"] >= Decimal("100"):
        return "CRITICA"

    if dados["total_atrasado"] > 0 or dados["contas_atrasadas"] > 0:
        return "ATENCAO"

    if dados["percentual_despesas"] >= Decimal("80") or dados["saldo_mes"] < 0:
        return "ATENCAO"

    if (
        dados["saldo_previsto"] > 0
        and dados["percentual_despesas"] <= Decimal("60")
        and dados["total_pendente"] == 0
        and dados["total_atrasado"] == 0
    ):
        return "OTIMA"

    return "ESTAVEL"


def montar_resumo(saude, dados):
    """Cria o resumo principal da analise local."""
    categoria = dados["principal_categoria"]
    saldo = dados["saldo_previsto"]

    if saude == "CRITICA":
        return (
            f"Seu mes exige atencao imediata: o saldo previsto esta em {brl(saldo)} "
            f"e as despesas representam {percentual(dados['percentual_despesas'])} das receitas."
        )

    if saude == "ATENCAO":
        return (
            f"O mes ainda pode ser ajustado, mas ha sinais de risco. A categoria {categoria} "
            f"esta pesando no orcamento e o saldo previsto esta em {brl(saldo)}."
        )

    if saude == "OTIMA":
        return (
            f"Seu mes esta bem encaminhado: o saldo previsto e {brl(saldo)} e as despesas "
            f"ficaram em {percentual(dados['percentual_despesas'])} das receitas."
        )

    return (
        f"Sua organizacao esta estavel, com saldo previsto de {brl(saldo)}. "
        f"O principal ponto de acompanhamento e {categoria}."
    )


def montar_metricas(dados):
    """Monta os cards de metricas exibidos na tela."""
    contas_em_risco = dados["contas_pendentes"] + dados["contas_atrasadas"]
    return {
        "Saldo do mes": brl(dados["saldo_mes"]),
        "Saldo previsto": brl(dados["saldo_previsto"]),
        "Despesas sobre receitas": percentual(dados["percentual_despesas"]),
        "Categoria mais pesada": dados["principal_categoria"],
        "Contas em risco": f"{contas_em_risco} conta(s)",
    }


def limitar_lista(itens, minimo=2, maximo=5):
    """Garante listas consistentes para os templates."""
    itens_limpos = [item for item in itens if item]
    if len(itens_limpos) < minimo:
        itens_limpos.extend(
            [
                "Continue registrando seus lancamentos para melhorar a precisao do diagnostico.",
                "Revise o mes semanalmente para agir antes que pequenos gastos se acumulem.",
            ]
        )
    return itens_limpos[:maximo]


def montar_sinais_positivos(dados):
    """Identifica pontos bons do mes do usuario."""
    sinais = []

    if dados["total_receitas"] > 0:
        sinais.append(f"Voce registrou {brl(dados['total_receitas'])} em receitas no periodo.")

    if dados["saldo_previsto"] > 0:
        sinais.append(f"O saldo previsto esta positivo em {brl(dados['saldo_previsto'])}.")

    if dados["contas_atrasadas"] == 0 and dados["total_atrasado"] == 0:
        sinais.append("Nao ha contas atrasadas registradas para este periodo.")

    if dados["metas_total"] > 0:
        sinais.append(f"Voce possui {dados['metas_total']} meta(s) financeira(s) acompanhada(s).")

    if dados["investimentos_ativos"] > 0:
        sinais.append(f"Sua carteira possui {dados['investimentos_ativos']} investimento(s) ativo(s).")

    return limitar_lista(sinais, minimo=2, maximo=4)


def montar_alertas(dados):
    """Identifica riscos que merecem atencao do usuario."""
    alertas = []

    if dados["total_receitas"] <= 0:
        alertas.append("Nao ha receitas registradas no periodo, o que dificulta medir o equilibrio do mes.")

    if dados["saldo_previsto"] < 0:
        alertas.append(f"Se nada mudar, o mes pode fechar negativo em {brl(abs(dados['saldo_previsto']))}.")

    if dados["percentual_despesas"] >= Decimal("80"):
        alertas.append(
            f"As despesas ja consomem {percentual(dados['percentual_despesas'])} das receitas."
        )

    if dados["total_atrasado"] > 0:
        alertas.append(f"Existem {brl(dados['total_atrasado'])} em contas atrasadas.")

    if dados["contas_pendentes"] > 0:
        alertas.append(f"Ha {dados['contas_pendentes']} conta(s) pendente(s) para acompanhar.")

    if dados["categorias"]:
        principal = dados["categorias"][0]
        alertas.append(
            f"A categoria {principal.get('nome') or 'Sem categoria'} concentrou {brl(principal.get('total'))} em gastos."
        )

    return limitar_lista(alertas, minimo=2, maximo=5)


def montar_oportunidades(dados):
    """Sugere caminhos de melhoria sem depender de API externa."""
    oportunidades = []

    if dados["categorias"]:
        principal = dados["categorias"][0]
        nome = principal.get("nome") or "Sem categoria"
        total = decimal_seguro(principal.get("total"))
        corte_10 = total * Decimal("0.10")
        oportunidades.append(
            f"Reduzir 10% em {nome} liberaria cerca de {brl(corte_10)} neste mes."
        )

    if dados["saldo_previsto"] > 0:
        guardar = dados["saldo_previsto"] * Decimal("0.20")
        oportunidades.append(f"Separar 20% do saldo previsto permitiria guardar {brl(guardar)}.")

    if dados["metas_total"] > 0:
        oportunidades.append("Use suas metas como prioridade antes de assumir novos gastos parcelados.")

    if dados["investimentos_total"] == 0:
        oportunidades.append("Quando o caixa estiver positivo, crie uma reserva inicial antes de buscar risco maior.")
    else:
        oportunidades.append("Acompanhe seus investimentos por data para comparar aporte, saldo atual e rentabilidade.")

    if dados["total_pendente"] > 0:
        oportunidades.append("Organizar as contas pendentes por vencimento reduz chance de atraso e multa.")

    return limitar_lista(oportunidades, minimo=2, maximo=4)


def montar_plano_acao(saude, dados):
    """Cria um plano de acao pratico para o usuario executar."""
    categoria = dados["principal_categoria"]
    plano = [
        {
            "prazo": "AGORA",
            "titulo": "Conferir O Saldo Previsto",
            "descricao": (
                f"Compare receitas e despesas do mes. O saldo previsto atual e {brl(dados['saldo_previsto'])}."
            ),
        },
        {
            "prazo": "7_DIAS",
            "titulo": f"Revisar {categoria}",
            "descricao": "Abra os lancamentos dessa categoria e corte ou adie o que nao for essencial.",
        },
        {
            "prazo": "30_DIAS",
            "titulo": "Criar Uma Rotina De Revisao",
            "descricao": "Reserve um dia fixo da semana para atualizar lancamentos, metas e contas pendentes.",
        },
    ]

    if saude in {"CRITICA", "ATENCAO"}:
        plano.insert(
            1,
            {
                "prazo": "AGORA",
                "titulo": "Congelar Gastos Nao Essenciais",
                "descricao": "Evite novas compras por alguns dias ate entender quais despesas estao pressionando o mes.",
            },
        )

    if dados["total_atrasado"] > 0:
        plano.insert(
            1,
            {
                "prazo": "AGORA",
                "titulo": "Priorizar Contas Atrasadas",
                "descricao": f"Negocie ou pague primeiro os {brl(dados['total_atrasado'])} em atraso.",
            },
        )

    if dados["metas_total"] > 0:
        plano.append(
            {
                "prazo": "30_DIAS",
                "titulo": "Reforcar Metas Financeiras",
                "descricao": "Direcione parte do saldo positivo para a meta mais importante antes de criar novos compromissos.",
            }
        )

    return plano[:6]


def gerar_analise_financeira_local(contexto_financeiro):
    """Gera diagnostico financeiro usando somente regras locais do FinanPy."""
    dados = obter_indicadores(contexto_financeiro)
    saude = classificar_saude(dados)
    analise = {
        "saude_financeira": saude,
        "resumo_executivo": montar_resumo(saude, dados),
        "metricas_resumo": montar_metricas(dados),
        "sinais_positivos": montar_sinais_positivos(dados),
        "alertas_prioritarios": montar_alertas(dados),
        "oportunidades": montar_oportunidades(dados),
        "plano_acao": montar_plano_acao(saude, dados),
    }
    resposta_bruta = {
        "motor": "local",
        "modelo": MODELO_ANALISE_LOCAL,
        "versao": "1.0",
        "observacao": "Diagnostico gerado por regras locais do FinanPy, sem API externa.",
    }
    return analise, resposta_bruta
