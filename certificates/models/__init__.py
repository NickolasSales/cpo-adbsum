"""
Modelos de certificados.

Dividido em dois assuntos que nao se misturam:

    certificate.py   o documento emitido, com os textos congelados
    template.py      o MODELO que descreve como o documento e desenhado

A separacao nasceu na Etapa 10, quando o layout deixou de ser desenhado em
Python e passou a ser configurado pelo administrador: uma arte oficial no
fundo e campos posicionados sobre ela. Os dois assuntos somariam mais de
seiscentas linhas num arquivo unico.

Este pacote reexporta tudo, de modo que quem chama continua escrevendo
`from certificates.models import Certificate`, igual antes.
"""

from certificates.models.certificate import (  # noqa: F401
    VERSAO_ATUAL_DO_MODELO,
    Certificate,
    CertificateQuerySet,
    CertificateStatus,
)
from certificates.models.template import (  # noqa: F401
    ALINHAMENTOS,
    CORES_ACEITAS,
    FAMILIAS_DE_FONTE,
    FAMILIAS_PERMITIDAS,
    FONTES_PERMITIDAS,
    LIMITE_DA_FONTE,
    LIMITE_DA_ROTACAO,
    TAMANHO_MAXIMO_DO_FUNDO,
    CertificateTemplate,
    CertificateTemplateField,
    CertificateTemplateQuerySet,
    FieldType,
    PageOrientation,
    TIPOS_COM_REPETICAO,
    TIPOS_DA_PALETA,
    TIPOS_DE_IMAGEM,
    TemplateStatus,
    TextAlign,
    aceita_repeticao,
    caminho_do_fundo,
    caminho_do_asset,
    decompor_fonte,
    resolver_fonte,
)

__all__ = [
    "VERSAO_ATUAL_DO_MODELO",
    "Certificate",
    "CertificateQuerySet",
    "CertificateStatus",
    "ALINHAMENTOS",
    "CORES_ACEITAS",
    "FAMILIAS_DE_FONTE",
    "FAMILIAS_PERMITIDAS",
    "FONTES_PERMITIDAS",
    "LIMITE_DA_FONTE",
    "LIMITE_DA_ROTACAO",
    "TAMANHO_MAXIMO_DO_FUNDO",
    "CertificateTemplate",
    "CertificateTemplateField",
    "CertificateTemplateQuerySet",
    "FieldType",
    "PageOrientation",
    "TIPOS_COM_REPETICAO",
    "TIPOS_DA_PALETA",
    "TIPOS_DE_IMAGEM",
    "TemplateStatus",
    "TextAlign",
    "aceita_repeticao",
    "caminho_do_fundo",
    "caminho_do_asset",
    "decompor_fonte",
    "resolver_fonte",
]
