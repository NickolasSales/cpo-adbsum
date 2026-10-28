"""
Formatacao de dinheiro para as telas de mensalidade.

    {% load financeiro %}
    {{ mensalidade.amount|brl }}   ->  R$ 1.234,50

Um filtro so, para que nenhuma tela monte "R$" a mao com floatformat e
saia com separador diferente da outra.
"""

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.numberformat import format as formatar_numero

register = template.Library()


@register.filter
def brl(valor):
    if valor in (None, ""):
        return ""
    try:
        valor = Decimal(str(valor)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return ""
    texto = formatar_numero(
        valor, ",", decimal_pos=2, grouping=3, thousand_sep=".", force_grouping=True
    )
    return "R$ {}".format(texto)
