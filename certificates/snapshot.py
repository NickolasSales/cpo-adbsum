"""
Traducao entre o mundo do banco e o mundo do renderizador.

Duas responsabilidades, e a segunda e uma decisao de seguranca.

1. Congelar a configuracao
--------------------------
`montar_snapshot(template)` produz o dicionario que o renderizador consome, e
que fica gravado dentro de cada certificado emitido. A partir dali o modelo
pode ganhar versoes novas, mudar de arte, ser arquivado — o documento antigo
continua sendo desenhado exatamente como foi.

2. Resolver o valor de cada campo
---------------------------------
`resolve_certificate_field(field_type, certificado)` traduz um tipo de campo
no dado correspondente. A traducao e um DICIONARIO EXPLICITO, e nao getattr:

    getattr(certificado, nome_vindo_do_navegador)

seria um leitor de atributos arbitrarios com a chave do lado de fora. Basta
que um dia alguem consiga gravar "attempt" ou "_state" como field_type para o
certificado passar a imprimir o que ninguem pediu. Com o mapa fixo, um tipo
desconhecido simplesmente nao existe.
"""

from django.conf import settings

from certificates.models.template import FieldType
from common.datas import data_curta, data_por_extenso

# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

# Os atributos de um campo que entram no snapshot. Lista explicita: um campo
# novo no modelo so chega ao documento se for declarado aqui, e nada que nao
# seja layout (datas, chaves estrangeiras, pk) escorre para dentro do JSON.
ATRIBUTOS_DO_CAMPO = (
    "field_type",
    "x",
    "y",
    "width",
    "height",
    "font_family",
    "bold",
    "italic",
    "font_size",
    "min_font_size",
    "auto_fit",
    "line_height",
    "text_align",
    "text_color",
    "rotation",
    "wrap",
    "is_visible",
    "z_index",
    # O texto do bloco personalizado, com as variaveis AINDA por resolver.
    # Congelar a frase e nao o resultado e o que faz o snapshot cumprir o
    # papel dele: reeditar o modelo depois nao muda o documento, e a
    # resolucao continua acontecendo contra os dados congelados do proprio
    # certificado.
    "content",
)

NUMERICOS = {"x", "y", "width", "height", "line_height"}


def _valor_do_campo(campo, atributo):
    valor = getattr(campo, atributo)
    # Decimal nao e serializavel em JSON. Vira float aqui, e nao no momento
    # de gravar: o snapshot precisa ser o mesmo dicionario que o renderizador
    # recebe no preview, senao preview e documento divergem no arredondamento.
    if atributo in NUMERICOS:
        return float(valor)
    return valor


def montar_snapshot(template, *, campos=None):
    """
    Configuracao congelada de um modelo, pronta para o renderizador.

    `campos` permite passar uma lista ja carregada, evitando uma consulta a
    mais quando quem chama acabou de busca-la.

    O caminho da arte entra como caminho absoluto de arquivo. Guardar a URL
    nao serviria: o renderizador roda no servidor e le do disco, e uma URL
    exigiria que o processo fizesse uma requisicao HTTP para si mesmo.
    """
    if campos is None:
        campos = list(template.fields.all().order_by("z_index", "pk"))

    lista = []
    for ordem, campo in enumerate(campos):
        dados = {
            atributo: _valor_do_campo(campo, atributo)
            for atributo in ATRIBUTOS_DO_CAMPO
        }
        # Desempate estavel entre campos de mesmo z_index.
        dados["_ordem"] = ordem
        if campo.field_type == FieldType.STATIC_IMAGE and campo.image:
            dados["image_path"] = campo.image.path
        lista.append(dados)

    return {
        "template_id": template.pk,
        "template_name": template.name,
        "template_version": template.version,
        "page_width_mm": float(template.page_width_mm),
        "page_height_mm": float(template.page_height_mm),
        "page_orientation": template.page_orientation,
        "background_path": template.background.path if template.background else "",
        # O checksum identifica a versao exata da arte. Se um dia alguem
        # trocar o arquivo no disco por outro de mesmo nome, o snapshot
        # continua dizendo qual era o conteudo original.
        "background_checksum": template.background_checksum,
        "fields": lista,
    }


# ---------------------------------------------------------------------------
# Resolucao dos valores
# ---------------------------------------------------------------------------


def _carga_horaria(certificado):
    horas = certificado.workload_hours_snapshot
    if horas is None:
        return ""
    if horas == 1:
        return "1 hora"
    return "{:02d} horas".format(horas)


def _data_de_emissao(certificado):
    return data_curta(certificado.issued_at)


def _data_de_conclusao(certificado):
    """
    A data em que a avaliacao foi fechada, por extenso.

    Le `data_de_conclusao` do certificado — que e o snapshot gravado na
    emissao, com issued_at como ultimo recurso para os documentos anteriores
    a esta etapa. Nunca timezone.now(): o PDF e gerado sob demanda, e uma
    data calculada na hora do download mudaria a cada vez que o aluno
    abrisse o proprio certificado.
    """
    return data_por_extenso(certificado.data_de_conclusao)


def _curso(certificado):
    # Certificados da versao 1 nao gravaram course_name_snapshot. O titulo da
    # prova era o que eles tinham, e e melhor que um espaco em branco.
    return certificado.course_name_snapshot or certificado.exam_title_snapshot


# O mapa. Cada entrada e uma funcao de um certificado para um texto.
#
# QR_CODE nao esta aqui: o conteudo dele e a URL publica de validacao, que
# depende de reverse() e vive em certificates.pdf. STATIC_IMAGE tambem nao:
# a imagem vem do proprio campo, e nao do certificado.
RESOLVEDORES = {
    FieldType.STUDENT_NAME: lambda c: c.student_name_snapshot,
    FieldType.COMPLETION_DATE: _data_de_conclusao,
    FieldType.COURSE_NAME: _curso,
    FieldType.MODULE_NAME: lambda c: c.modulo_impresso,
    FieldType.COURSE_DATES: lambda c: c.course_dates_snapshot,
    FieldType.COURSE_LOCATION: lambda c: c.course_location_snapshot,
    FieldType.WORKLOAD: _carga_horaria,
    FieldType.YEAR: lambda c: (
        str(c.certificate_year_snapshot) if c.certificate_year_snapshot else ""
    ),
    FieldType.ISSUED_AT: _data_de_emissao,
    FieldType.INSTITUTION: lambda c: c.institution_name_snapshot,
    FieldType.SIGNATORY_NAME: lambda c: c.signatory_name_snapshot,
    FieldType.SIGNATORY_TITLE: lambda c: c.signatory_title_snapshot,
    FieldType.VERIFICATION_CODE: lambda c: str(c.verification_code),
}


def resolve_certificate_field(field_type, certificado):
    """
    Valor de um campo para este certificado, ou "" se nao houver.

    Le exclusivamente os campos *_snapshot do certificado. O documento e o que
    foi emitido, e nao o que o banco diz hoje: corrigir a data do modulo no
    ano que vem nao pode reescrever um certificado assinado.
    """
    resolvedor = RESOLVEDORES.get(field_type)
    if resolvedor is None:
        return ""
    valor = resolvedor(certificado)
    return "" if valor is None else str(valor)


def valores_do_certificado(certificado):
    """Dicionario {field_type: texto} para o renderizador."""
    from certificates.pdf import url_de_validacao

    valores = {
        tipo: resolve_certificate_field(tipo, certificado) for tipo in RESOLVEDORES
    }
    valores[FieldType.QR_CODE] = url_de_validacao(certificado)
    return valores


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

# Nomes deliberadamente longos. Um preview com "Joao Silva" mentiria sobre o
# comportamento do campo: o que estressa a caixa e o nome comprido, e e ele
# que precisa aparecer enquanto se posiciona.
VALORES_DE_EXEMPLO = {
    FieldType.STUDENT_NAME: "João da Silva de Oliveira",
    FieldType.COMPLETION_DATE: "02 de setembro de 2026",
    FieldType.COURSE_NAME: "CPO - Curso de Preparação de Obreiros",
    FieldType.MODULE_NAME: "Módulo I - Cooperadores e Diáconos",
    FieldType.COURSE_DATES: "10 e 17 de outubro de 2026",
    FieldType.COURSE_LOCATION: "Igreja Sede",
    FieldType.WORKLOAD: "08 horas",
    FieldType.YEAR: "2026",
    FieldType.ISSUED_AT: "17/10/2026",
    FieldType.SIGNATORY_NAME: "Rodrigo Montenegro",
    FieldType.SIGNATORY_TITLE: "Pastor Presidente ADBrás Sumaré",
    FieldType.VERIFICATION_CODE: "00000000-0000-0000-0000-000000000000",
}


def valores_de_preview():
    """
    Dados ficticios para o preview do editor.

    A instituicao e o signatario vem de settings, e nao inventados: sao os
    mesmos textos que sairao no documento real, e ve-los no preview e parte
    de conferir o posicionamento.

    O QR aponta para um endereco de preview que NAO corresponde a certificado
    nenhum. Nenhum Certificate e criado, nenhuma matricula muda, nada e
    registrado: o preview e uma imagem, e nao um ato academico.
    """
    valores = dict(VALORES_DE_EXEMPLO)
    valores[FieldType.INSTITUTION] = settings.INSTITUTION_NAME
    valores[FieldType.COURSE_NAME] = settings.CERTIFICATE_COURSE_NAME
    valores[FieldType.SIGNATORY_NAME] = settings.CERTIFICATE_SIGNATORY_NAME
    valores[FieldType.SIGNATORY_TITLE] = settings.CERTIFICATE_SIGNATORY_TITLE
    valores[FieldType.QR_CODE] = "{}/certificados/validar/preview/".format(
        settings.SITE_URL.rstrip("/")
    )
    return valores
