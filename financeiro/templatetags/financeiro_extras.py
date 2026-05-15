"""Filtros auxiliares usados nos templates do FinanPy."""

from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def dict_get(dicionario, chave):
    """Permite acessar dicionarios por chave dinamica dentro do template."""
    if not dicionario:
        return None
    return dicionario.get(chave)


@register.filter
def brl(valor):
    """Formata valores monetarios no padrao brasileiro: R$ 20.000,00."""
    if valor is None or valor == "":
        valor = Decimal("0.00")

    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        numero = Decimal("0.00")

    sinal = "-" if numero < 0 else ""
    numero = abs(numero)
    texto = f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sinal}R$ {texto}"


@register.filter
def title_pt(valor):
    """Deixa titulos com a primeira letra de cada palavra em maiuscula."""
    if valor is None:
        return ""
    return str(valor).title()
