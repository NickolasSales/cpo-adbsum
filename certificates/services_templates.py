"""
Servicos dos modelos de certificado.

Quem decide o que pode ser feito com um modelo esta aqui — nao na tela, nao
no formulario. A tela esconde o botao impossivel por cortesia; a recusa de
verdade acontece neste modulo, e vale igual para um POST montado a mao.

Tres regras atravessam o arquivo:

    o modelo usado nao muda        duplique, edite a copia, ative
    o modelo usado nao some        arquiva-se
    so existe um padrao ativo      o fallback nao pode ter dois candidatos
"""

from django.db import transaction
from django.db.models import Max

from audit.models import AuditEvent
from audit.services import record
from certificates.models.template import (
    CORES_ACEITAS,
    FONTES_PERMITIDAS,
    LIMITE_DA_FONTE,
    LIMITE_DA_ROTACAO,
    CertificateTemplate,
    CertificateTemplateField,
    FieldType,
    PageOrientation,
    TemplateStatus,
    TextAlign,
)
from certificates.uploads import proporcao_compativel, validar_imagem_enviada
from common.exceptions import DomainError

# Faixas dos percentuais. O banco tambem impoe; a validacao aqui existe para
# devolver mensagem legivel em vez de IntegrityError.
LIMITE_PERCENTUAL = (0, 100)
LIMITE_ENTRELINHA = (0.8, 3.0)


class ModeloNaoEditavel(DomainError):
    """O modelo ja emitiu certificado, ou esta arquivado."""


class ModeloJaArquivado(DomainError):
    """Nada a arquivar."""


# ---------------------------------------------------------------------------
# Validacao
# ---------------------------------------------------------------------------


def _numero(valor, nome, minimo, maximo, *, inteiro=False):
    try:
        convertido = int(valor) if inteiro else float(valor)
    except (TypeError, ValueError):
        raise DomainError("{} precisa ser um numero.".format(nome))
    if convertido < minimo or convertido > maximo:
        raise DomainError(
            "{} precisa estar entre {} e {}.".format(nome, minimo, maximo)
        )
    return convertido


def normalizar_campo(dados):
    """
    Valida e converte o dicionario de um campo vindo do formulario.

    Lista branca dupla. As chaves aceitas sao as que aparecem no retorno, e
    nenhuma outra e lida; os valores de fonte, alinhamento e cor sao
    conferidos contra as listas do modelo. Nada que venha do navegador vira
    nome de arquivo, propriedade CSS ou atributo de objeto.
    """
    fonte = (dados.get("font_family") or "").strip()
    if fonte not in FONTES_PERMITIDAS:
        raise DomainError("Fonte nao permitida: {}.".format(fonte or "vazia"))

    alinhamento = (dados.get("text_align") or "").strip().upper()
    if alinhamento not in TextAlign.values:
        raise DomainError("Alinhamento invalido.")

    cor = (dados.get("text_color") or "").strip()
    if not CORES_ACEITAS.match(cor):
        raise DomainError(
            "Informe a cor no formato #RRGGBB. Recebido: {}".format(cor or "vazio")
        )

    tamanho = _numero(
        dados.get("font_size"), "O tamanho da fonte", *LIMITE_DA_FONTE, inteiro=True
    )
    minimo = _numero(
        dados.get("min_font_size"),
        "O tamanho minimo",
        *LIMITE_DA_FONTE,
        inteiro=True,
    )
    if minimo > tamanho:
        raise DomainError(
            "O tamanho minimo ({}) nao pode ser maior que o tamanho ({}).".format(
                minimo, tamanho
            )
        )

    largura = _numero(dados.get("width"), "A largura", 0.1, 100)
    altura = _numero(dados.get("height"), "A altura", 0.1, 100)

    return {
        "x": _numero(dados.get("x"), "A posicao X", *LIMITE_PERCENTUAL),
        "y": _numero(dados.get("y"), "A posicao Y", *LIMITE_PERCENTUAL),
        "width": largura,
        "height": altura,
        "font_family": fonte,
        "font_size": tamanho,
        "min_font_size": minimo,
        "auto_fit": bool(dados.get("auto_fit")),
        "line_height": _numero(
            dados.get("line_height"), "A entrelinha", *LIMITE_ENTRELINHA
        ),
        "text_align": alinhamento,
        "text_color": cor.upper(),
        "rotation": _numero(
            dados.get("rotation"), "A rotacao", *LIMITE_DA_ROTACAO, inteiro=True
        ),
        "is_visible": bool(dados.get("is_visible")),
        "z_index": _numero(dados.get("z_index"), "A ordem de desenho", 0, 999, inteiro=True),
    }


def exigir_editavel(template):
    """
    Recusa alteracao em modelo arquivado ou ja usado.

    O status e lido DO BANCO, e nao do objeto recebido. A instancia em memoria
    pode estar velha — quem arquivou foi outra requisicao, ou o proprio
    servico de arquivamento, que grava numa linha travada e devolve outro
    objeto. Confiar no atributo faria a recusa depender de quem carregou o
    modelo e quando.
    """
    atual = (
        CertificateTemplate.objects.filter(pk=template.pk)
        .values_list("status", flat=True)
        .first()
    )
    if atual == TemplateStatus.ARCHIVED:
        raise ModeloNaoEditavel(
            "Este modelo esta arquivado e nao aceita alteracoes."
        )
    if template.esta_em_uso():
        raise ModeloNaoEditavel(
            "Este modelo ja emitiu certificado e nao pode mais ser alterado. "
            "Duplique-o, edite a copia e ative a nova versao."
        )


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------


def create_template(
    *,
    name,
    description="",
    page_orientation=PageOrientation.LANDSCAPE,
    page_width_mm=297,
    page_height_mm=210,
    is_global=False,
    actor=None,
    request=None,
):
    """
    Cria um modelo em rascunho.

    Nasce sempre DRAFT, mesmo que quem chamou peca outra coisa: um modelo sem
    arte e sem campos nao pode estar ativo, e deixar o status vir do
    formulario seria oferecer esse caminho.
    """
    name = (name or "").strip()
    if not name:
        raise DomainError("O nome do modelo e obrigatorio.")
    if page_orientation not in PageOrientation.values:
        raise DomainError("Orientacao invalida.")

    largura = _numero(page_width_mm, "A largura da pagina", 50, 2000)
    altura = _numero(page_height_mm, "A altura da pagina", 50, 2000)

    with transaction.atomic():
        template = CertificateTemplate.objects.create(
            name=name,
            description=(description or "").strip(),
            status=TemplateStatus.DRAFT,
            page_orientation=page_orientation,
            page_width_mm=largura,
            page_height_mm=altura,
            is_global=bool(is_global),
            created_by=actor if getattr(actor, "pk", None) else None,
        )
        record(
            AuditEvent.CERTIFICATE_TEMPLATE_CREATED,
            request=request,
            actor=actor,
            entity_type="CertificateTemplate",
            entity_id=template.pk,
            metadata={"name": template.name, "version": template.version},
        )
    return template


def update_template(
    template,
    *,
    name,
    description="",
    page_orientation=PageOrientation.LANDSCAPE,
    page_width_mm=297,
    page_height_mm=210,
    is_global=False,
    actor=None,
    request=None,
):
    exigir_editavel(template)

    name = (name or "").strip()
    if not name:
        raise DomainError("O nome do modelo e obrigatorio.")
    if page_orientation not in PageOrientation.values:
        raise DomainError("Orientacao invalida.")

    largura = _numero(page_width_mm, "A largura da pagina", 50, 2000)
    altura = _numero(page_height_mm, "A altura da pagina", 50, 2000)

    with transaction.atomic():
        template.name = name
        template.description = (description or "").strip()
        template.page_orientation = page_orientation
        template.page_width_mm = largura
        template.page_height_mm = altura
        template.is_global = bool(is_global)
        template.save(
            update_fields=[
                "name",
                "description",
                "page_orientation",
                "page_width_mm",
                "page_height_mm",
                "is_global",
                "updated_at",
            ]
        )
        record(
            AuditEvent.CERTIFICATE_TEMPLATE_UPDATED,
            request=request,
            actor=actor,
            entity_type="CertificateTemplate",
            entity_id=template.pk,
            metadata={"name": template.name},
        )
    return template


def set_background(template, arquivo, *, actor=None, request=None):
    """
    Substitui a arte de fundo.

    O arquivo antigo NAO e apagado do disco. Um certificado emitido antes
    guarda no snapshot o caminho e o checksum da arte que usou, e apagar o
    arquivo faria esse documento perder o fundo na proxima vez que fosse
    baixado. Disco e barato; documento oficial reimpresso sem a moldura, nao.

    Devolve (template, avisos). Os avisos nao impedem nada — proporcao
    diferente da pagina produz um documento utilizavel, so esticado, e recusar
    o upload por isso deixaria o administrador sem saida.
    """
    exigir_editavel(template)

    formato, largura, altura, checksum = validar_imagem_enviada(arquivo)

    avisos = []
    if not proporcao_compativel(
        largura, altura, template.page_width_mm, template.page_height_mm
    ):
        avisos.append(
            "A arte tem proporcao {:.2f}:1 e a pagina configurada tem "
            "{:.2f}:1. A imagem sera esticada para cobrir a pagina.".format(
                largura / altura,
                float(template.page_width_mm) / float(template.page_height_mm),
            )
        )

    with transaction.atomic():
        # save=False para gravar tudo numa unica escrita: o arquivo entra no
        # storage aqui, e os metadados junto com ele.
        template.background.save(getattr(arquivo, "name", "arte"), arquivo, save=False)
        template.background_checksum = checksum
        template.background_width = largura
        template.background_height = altura
        template.save(
            update_fields=[
                "background",
                "background_checksum",
                "background_width",
                "background_height",
                "updated_at",
            ]
        )
        record(
            AuditEvent.CERTIFICATE_TEMPLATE_BACKGROUND_SET,
            request=request,
            actor=actor,
            entity_type="CertificateTemplate",
            entity_id=template.pk,
            # Sem o nome enviado: ele e texto do usuario e nao acrescenta
            # nada que o checksum ja nao identifique melhor.
            metadata={
                "format": formato,
                "width": largura,
                "height": altura,
                "checksum": checksum,
            },
        )
    return template, avisos


def save_fields(template, campos, *, actor=None, request=None):
    """
    Grava a configuracao de todos os campos de uma vez.

    `campos` e {field_type: dicionario cru do formulario}. Tudo passa por
    normalizar_campo antes de qualquer escrita, e a transacao e uma so: um
    valor invalido no ultimo campo nao deixa os primeiros gravados.

    Um tipo ausente do dicionario e REMOVIDO do modelo. A tela envia sempre a
    lista inteira, e essa e a forma de desconfigurar um campo — mais direta
    do que uma acao separada de exclusao.
    """
    exigir_editavel(template)

    limpos = {}
    for tipo, dados in campos.items():
        if tipo not in FieldType.values:
            raise DomainError("Campo desconhecido: {}.".format(tipo))
        if tipo == FieldType.STATIC_IMAGE:
            # Imagem fixa tem arquivo proprio e fluxo de upload proprio; ela
            # nao entra pelo formulario de posicoes.
            continue
        limpos[tipo] = normalizar_campo(dados)

    with transaction.atomic():
        existentes = {
            campo.field_type: campo
            for campo in CertificateTemplateField.objects.select_for_update().filter(
                template=template
            )
        }

        for tipo, valores in limpos.items():
            campo = existentes.get(tipo)
            if campo is None:
                CertificateTemplateField.objects.create(
                    template=template, field_type=tipo, **valores
                )
                continue
            for atributo, valor in valores.items():
                setattr(campo, atributo, valor)
            campo.save(update_fields=[*valores.keys(), "updated_at"])

        sobrando = [
            campo
            for tipo, campo in existentes.items()
            if tipo not in limpos and tipo != FieldType.STATIC_IMAGE
        ]
        for campo in sobrando:
            campo.delete()

        record(
            AuditEvent.CERTIFICATE_TEMPLATE_UPDATED,
            request=request,
            actor=actor,
            entity_type="CertificateTemplate",
            entity_id=template.pk,
            metadata={"fields": sorted(limpos.keys())},
        )
    return template


def activate_template(template, *, actor=None, request=None):
    """
    Coloca o modelo em uso.

    Um modelo global ativo substitui o anterior, que passa a ARCHIVED na
    mesma transacao. Nao e efeito colateral escondido: a constraint do banco
    recusaria dois globais ativos, e deixar isso estourar como IntegrityError
    obrigaria o administrador a desativar o antigo primeiro, adivinhando qual.
    """
    if template.esta_arquivado:
        raise DomainError(
            "Este modelo esta arquivado. Duplique-o para voltar a usa-lo."
        )

    pendencias = template.pendencias_para_ativar()
    if pendencias:
        raise DomainError(pendencias)

    with transaction.atomic():
        travado = CertificateTemplate.objects.select_for_update().get(pk=template.pk)

        substituido = None
        if travado.is_global:
            substituido = (
                CertificateTemplate.objects.select_for_update()
                .filter(status=TemplateStatus.ACTIVE, is_global=True)
                .exclude(pk=travado.pk)
                .first()
            )
            if substituido is not None:
                substituido.status = TemplateStatus.ARCHIVED
                substituido.save(update_fields=["status", "updated_at"])

        travado.status = TemplateStatus.ACTIVE
        travado.save(update_fields=["status", "updated_at"])

        record(
            AuditEvent.CERTIFICATE_TEMPLATE_ACTIVATED,
            request=request,
            actor=actor,
            entity_type="CertificateTemplate",
            entity_id=travado.pk,
            metadata={
                "name": travado.name,
                "version": travado.version,
                "is_global": travado.is_global,
                "replaced_id": substituido.pk if substituido else None,
            },
        )
    return travado, substituido


def archive_template(template, *, actor=None, request=None):
    """
    Aposenta o modelo sem apagar nada.

    Nao existe exclusao fisica de modelo na interface, e a ausencia e
    deliberada: um modelo que ja emitiu certificado e parte do historico
    daquele documento, e a arte dele continua sendo lida do disco toda vez
    que o PDF e gerado.

    Os modulos que apontavam para ele voltam ao padrao global, por SET_NULL.
    A emissao avisa se nao houver padrao — recusar com mensagem clara e melhor
    do que emitir com um layout que ninguem escolheu.
    """
    with transaction.atomic():
        travado = CertificateTemplate.objects.select_for_update().get(pk=template.pk)

        # Conferido DEPOIS do lock, e sobre a linha travada. Dois cliques
        # simultaneos passariam os dois por uma checagem feita antes.
        if travado.status == TemplateStatus.ARCHIVED:
            raise ModeloJaArquivado("Este modelo ja esta arquivado.")

        travado.status = TemplateStatus.ARCHIVED
        travado.save(update_fields=["status", "updated_at"])

        # SET_NULL nao dispara sozinho num UPDATE de status: quem aponta
        # precisa ser soltado explicitamente, senao o modulo continuaria
        # amarrado a um modelo aposentado.
        modulos = list(travado.modules.values_list("code", flat=True))
        travado.modules.update(certificate_template=None)

        record(
            AuditEvent.CERTIFICATE_TEMPLATE_ARCHIVED,
            request=request,
            actor=actor,
            entity_type="CertificateTemplate",
            entity_id=travado.pk,
            metadata={"name": travado.name, "modules_released": modulos},
        )
    return travado


def duplicate_template(template, *, actor=None, request=None):
    """
    Cria a proxima versao a partir desta.

    A copia nasce DRAFT e nunca global: ativar e uma decisao a parte, e um
    duplicado que ja chegasse como padrao trocaria o certificado de todo mundo
    por um clique de "Duplicar".

    A arte NAO e copiada como arquivo novo — as duas versoes apontam para o
    mesmo caminho. Trocar a arte da copia grava um arquivo novo (ver
    set_background), entao a arte da v1 nunca e sobrescrita pelo conteudo da
    v2. Era exatamente isso que precisava ser garantido.
    """
    with transaction.atomic():
        maior = (
            CertificateTemplate.objects.filter(
                name=template.name
            ).aggregate(maior=Max("version"))["maior"]
            or template.version
        )

        copia = CertificateTemplate.objects.create(
            name=template.name,
            description=template.description,
            status=TemplateStatus.DRAFT,
            background=template.background,
            background_checksum=template.background_checksum,
            background_width=template.background_width,
            background_height=template.background_height,
            page_orientation=template.page_orientation,
            page_width_mm=template.page_width_mm,
            page_height_mm=template.page_height_mm,
            version=maior + 1,
            parent_template=template,
            is_global=False,
            created_by=actor if getattr(actor, "pk", None) else None,
        )

        for campo in template.fields.all():
            CertificateTemplateField.objects.create(
                template=copia,
                field_type=campo.field_type,
                x=campo.x,
                y=campo.y,
                width=campo.width,
                height=campo.height,
                font_family=campo.font_family,
                font_size=campo.font_size,
                min_font_size=campo.min_font_size,
                auto_fit=campo.auto_fit,
                line_height=campo.line_height,
                text_align=campo.text_align,
                text_color=campo.text_color,
                rotation=campo.rotation,
                image=campo.image,
                is_visible=campo.is_visible,
                z_index=campo.z_index,
            )

        record(
            AuditEvent.CERTIFICATE_TEMPLATE_DUPLICATED,
            request=request,
            actor=actor,
            entity_type="CertificateTemplate",
            entity_id=copia.pk,
            metadata={
                "name": copia.name,
                "version": copia.version,
                "source_id": template.pk,
            },
        )
    return copia


# ---------------------------------------------------------------------------
# Resolucao para a emissao
# ---------------------------------------------------------------------------

MENSAGEM_SEM_MODELO = (
    "Nao foi possivel emitir o certificado.\n\n"
    "Nenhum modelo de certificado esta configurado para este modulo."
)


def resolver_template(modulo):
    """
    O modelo que deve ser usado para este modulo, ou None.

    A ordem e explicita:

        1  o modelo configurado no modulo, se estiver ativo
        2  o modelo padrao global ativo
        3  nada

    O passo 1 confere o status de novo em vez de confiar na FK: um modelo
    arquivado solta os modulos por SET_NULL, mas um caminho futuro poderia
    arquivar de outro jeito, e emitir com modelo aposentado seria pior do que
    recusar.

    O terceiro caso NAO cai num layout embutido. Voltar a desenhar em codigo
    quando falta configuracao produziria, justamente no dia em que ninguem
    esta olhando, um documento oficial com estetica que ninguem aprovou.
    """
    escolhido = modulo.certificate_template
    if escolhido is not None and escolhido.status == TemplateStatus.ACTIVE:
        return escolhido
    return (
        CertificateTemplate.objects.filter(
            status=TemplateStatus.ACTIVE, is_global=True
        )
        .order_by("-version", "-pk")
        .first()
    )


def exigir_template(modulo):
    template = resolver_template(modulo)
    if template is None:
        raise DomainError(MENSAGEM_SEM_MODELO)
    return template
