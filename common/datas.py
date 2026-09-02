"""
Datas por extenso em portugues, sem depender do sistema operacional.

Por que nao `strftime("%d de %B de %Y")`
----------------------------------------
Porque `%B` devolve o nome do mes no locale do processo. No servidor Ubuntu
esse locale e `C` por padrao, e o resultado seria:

    02 de September de 2026

num certificado oficial. Fazer o processo mudar de locale resolveria o mes e
traria dois problemas piores: `setlocale` nao e seguro em processo com
threads, e a formatacao de numeros e datas do sistema inteiro passaria a
depender de um pacote de idioma instalado no servidor — algo que nenhuma
migration garante e que um `docker build` diferente derruba em silencio.

A tabela abaixo e o idioma. Ela nao depende de locale, de variavel de
ambiente, de LANG, nem de o servidor ter `pt_BR.UTF-8` gerado. O mesmo dado
produz o mesmo texto em qualquer maquina, e e isso que um documento assinado
precisa.
"""

from django.utils import timezone

# Indice 0 vazio para que MESES[data.month] funcione sem subtrair 1.
MESES = (
    "",
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def _para_data_local(valor):
    """
    Converte para a data do fuso da aplicacao.

    Importa: um certificado corrigido as 22h de Sumare e 01h do dia seguinte
    em UTC. Formatar o instante cru imprimiria a data errada — um dia a mais
    — em toda correcao feita a noite.
    """
    if valor is None:
        return None
    if hasattr(valor, "tzinfo") and valor.tzinfo is not None:
        return timezone.localtime(valor).date()
    if hasattr(valor, "date"):
        return valor.date()
    return valor


def data_por_extenso(valor):
    """
    `02 de setembro de 2026`.

    Aceita datetime (convertido para o fuso local) ou date. Devolve "" para
    None: um campo sem data nao imprime nada, e nunca a palavra "None".
    """
    data = _para_data_local(valor)
    if data is None:
        return ""
    return "{:02d} de {} de {}".format(data.day, MESES[data.month], data.year)


def data_curta(valor):
    """`02/09/2026`. Mesmo tratamento de fuso e de ausencia."""
    data = _para_data_local(valor)
    if data is None:
        return ""
    return "{:02d}/{:02d}/{}".format(data.day, data.month, data.year)
