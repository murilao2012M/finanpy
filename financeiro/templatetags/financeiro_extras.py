"""Filtros auxiliares usados nos templates do FinanPy."""

from django import template


register = template.Library()


@register.filter
def dict_get(dicionario, chave):
    """Permite acessar dicionarios por chave dinamica dentro do template."""
    if not dicionario:
        return None
    return dicionario.get(chave)
