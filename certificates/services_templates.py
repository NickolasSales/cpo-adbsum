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
from certificates.fonts import NEGRITO, PESOS, pesos_suportados, tem_italico
from certificates.models.template import (
    CORES_ACEITAS,
    FAMILIAS_PERMITIDAS,
    FONTES_PERMITIDAS,
    LIMITE_DA_FONTE,
    LIMITE_DA_ROTACAO,
    TIPOS_COM_REPETICAO,
    TIPOS_DE_IMAGEM,
    CertificateTemplate,
    CertificateTemplateField,
    FieldType,
    PageOrientation,
    TemplateStatus,
    TextAlign,
    decompor_fonte,
)
from certificates.placeholders import PlaceholderInvalido, validar_texto
from certificates.uploads import proporcao_compativel, validar_imagem_enviada
from common.exceptions import DomainError

# Faixas dos percentuais. O banco tambem impoe; a validacao aqui existe para
# devolver mensagem legivel em vez de IntegrityError.
LIMITE_PERCENTUAL = (0, 100)
LIMITE_ENTRELINHA = (0.8, 3.0)

# Teto de elementos por modelo. Um certificado tem meia duzia; quarenta e
# folga larga. O teto existe porque o editor manda JSON, e um JSON montado a
# mao com dez mil elementos viraria dez mil linhas no banco e um PDF que nao
# termina de desenhar.
MAXIMO_DE_ELEMENTOS = 40

# Propriedades que so fazem sentido em texto. Um QR nao tem fonte nem
# alinhamento; preenche-las com o padrao evita recusar um elemento de imagem
# por falta de um dado que nada usa.
PADRAO_DE_IMAGEM = {
    "font_family": "Helvetica",
    "font_size": 12,
    "min_font_size": 8,
    "line_height": 1.2,
    "text_align": TextAlign.CENTER,
    "text_color": "#000000",
}


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
    # A tela manda a FAMILIA ("Times") mais os dois marcadores. O nome
    # composto ("Times-BoldItalic") tambem e aceito e decomposto: e o que os
    # campos gravados ate agora guardavam, e recusa-lo transformaria uma
    # melhoria de tela em perda de configuracao.
    #
    # A conferencia acontece ANTES de decompor. `decompor_fonte` cai na
    # familia padrao para o que nao reconhece — comportamento certo para
    # renderizar um snapshot antigo, e errado aqui: "Arial" viraria
    # Helvetica em silencio em vez de dizer que nao existe.
    # str() em toda leitura de texto: o corpo do editor e JSON, e JSON tem
    # numeros, listas e nulos. Sem a coercao, `{"font_family": 5}` chamaria
    # .strip() num inteiro e devolveria 500 no lugar de uma recusa legivel.
    nome_da_fonte = str(dados.get("font_family") or "").strip()
    if nome_da_fonte not in FAMILIAS_PERMITIDAS and nome_da_fonte not in FONTES_PERMITIDAS:
        raise DomainError(
            "Fonte nao permitida: {}.".format(nome_da_fonte or "vazia")
        )

    familia, peso_do_nome, italico_do_nome = decompor_fonte(nome_da_fonte)

    # O peso pedido pela tela. Ausente vale o do nome — que e o caminho por
    # onde um campo antigo, gravado como "Times-Bold", conserva o peso ao
    # passar por aqui.
    #
    # `bold` ainda e aceito porque a tela anterior mandava um booleano, e um
    # POST de uma aba aberta antes do deploy nao deve virar erro. True vale
    # 700; o que fica gravado e so o peso.
    peso = peso_do_nome
    if dados.get("bold"):
        peso = max(peso, NEGRITO)

    pedido = dados.get("font_weight")
    if pedido not in (None, ""):
        try:
            pedido = int(pedido)
        except (TypeError, ValueError):
            raise DomainError("O peso da fonte precisa ser um numero.")
        if pedido not in PESOS:
            # Recusa, e nao arredondamento. Um 12345 que virasse 700 em
            # silencio esconderia um erro de quem chamou; a lista de pesos e
            # curta e a tela so oferece o que existe.
            raise DomainError(
                "Peso de fonte nao permitido: {}. Use um de {}.".format(
                    pedido, ", ".join(str(valor) for valor in PESOS)
                )
            )
        peso = max(peso, pedido)

    # O peso e a inclinacao caem no que a familia REALMENTE tem. Great Vibes
    # com semibold pedido sai em regular, porque o arquivo semibold dela nao
    # existe — e inventa-lo engordando os tracos destruiria justamente o
    # desenho que faz ela servir para assinatura. A tela tambem nao oferece a
    # combinacao; isto e a rede embaixo.
    italico = (bool(dados.get("italic")) or italico_do_nome) and tem_italico(familia)
    disponiveis = pesos_suportados(familia)
    if peso not in disponiveis:
        peso = min(disponiveis, key=lambda existente: (abs(existente - peso), existente))

    alinhamento = str(dados.get("text_align") or "").strip().upper()
    if alinhamento not in TextAlign.values:
        raise DomainError("Alinhamento invalido.")

    cor = str(dados.get("text_color") or "").strip()
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
        "font_family": familia,
        "font_weight": peso,
        "italic": italico,
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
        "wrap": bool(dados.get("wrap", True)),
        "is_visible": bool(dados.get("is_visible")),
        "z_index": _numero(dados.get("z_index"), "A ordem de desenho", 0, 999, inteiro=True),
    }


def normalizar_elemento(dados):
    """
    Valida um elemento vindo do editor visual.

    O editor manda JSON, e JSON e texto que o navegador escreveu. Nada aqui
    confia nele: o tipo e conferido contra a lista fechada, a geometria e o
    estilo passam por normalizar_campo, e o texto personalizado passa pelo
    validador de variaveis. O que sobra e um dicionario com exatamente as
    chaves que o modelo aceita.
    """
    # str() antes de strip(): o corpo e JSON, e JSON tem numeros, listas e
    # nulos. `{"type": 5}` chamaria .strip() num inteiro e derrubaria a
    # requisicao com 500 — um payload invalido merece 400, e nao um erro de
    # servidor.
    tipo = str(dados.get("type") or dados.get("field_type") or "").strip()
    if tipo not in FieldType.values:
        raise DomainError("Elemento desconhecido: {}.".format(tipo or "vazio"))
    if tipo == FieldType.STATIC_IMAGE:
        # Imagem fixa tem arquivo proprio e fluxo de upload proprio; ela nao
        # entra pelo editor de posicoes.
        raise DomainError("A imagem fixa nao e editada por aqui.")

    if tipo in TIPOS_DE_IMAGEM:
        dados = {**PADRAO_DE_IMAGEM, **dados}

    limpo = normalizar_campo(dados)
    limpo["field_type"] = tipo

    if tipo == FieldType.CUSTOM_TEXT:
        bruto = dados.get("content")
        # Recusa em vez de converter. `{"content": 5}` viraria o texto "5" em
        # silencio, e um numero no lugar de uma frase e erro de quem chamou —
        # nao um pedido para imprimir "5" no certificado.
        if bruto is not None and not isinstance(bruto, str):
            raise DomainError(
                "O texto do bloco personalizado precisa ser texto."
            )
        try:
            texto = validar_texto(bruto)
        except PlaceholderInvalido as erro:
            raise DomainError(
                "O texto contem variaveis nao permitidas: {}".format(
                    ", ".join(erro.invalidos)
                )
            )
        except ValueError as erro:
            raise DomainError(str(erro))
        if not texto.strip():
            raise DomainError("Escreva o texto do bloco personalizado.")
        limpo["content"] = texto
    else:
        # Texto proprio so no bloco personalizado. O banco tambem impoe.
        limpo["content"] = ""

    return limpo


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


def save_elements(template, elementos, *, actor=None, request=None):
    """
    Grava a lista inteira de elementos do modelo, de uma vez.

    `elementos` e uma lista de dicionarios crus do editor. Tudo passa por
    normalizar_elemento antes de qualquer escrita, e a transacao e uma so: um
    valor invalido no ultimo elemento nao deixa os primeiros gravados.

    Por que substituir tudo em vez de casar por id
    ----------------------------------------------
    O editor manda o estado final da tela. Casar elemento por id exigiria
    aceitar ids vindos do navegador e conferir, um a um, se cada id pertence
    a ESTE modelo — e o dia em que essa conferencia falhasse, um POST
    montado a mao moveria o campo de outro modelo.

    Substituir o conjunto inteiro dispensa a pergunta: nenhum id atravessa a
    fronteira. O preco e a chave primaria dos elementos mudar a cada
    gravacao, e ela nao e referenciada por nada — o certificado guarda uma
    COPIA da configuracao, e nao um ponteiro para estas linhas.

    A imagem fixa e preservada: ela tem arquivo em disco e nao vem no
    payload, entao apaga-la aqui destruiria um upload que a tela nem
    ofereceu.
    """
    exigir_editavel(template)

    if len(elementos) > MAXIMO_DE_ELEMENTOS:
        raise DomainError(
            "Um modelo aceita no maximo {} elementos.".format(MAXIMO_DE_ELEMENTOS)
        )

    limpos = [normalizar_elemento(dados) for dados in elementos]

    # Um "Nome do aluno" duplicado e sempre engano: o segundo ficaria
    # escondido atras do primeiro, ou visivel em outro canto sem ninguem ter
    # pedido. Texto personalizado e imagem fixa se repetem por natureza.
    vistos = {}
    for elemento in limpos:
        tipo = elemento["field_type"]
        vistos[tipo] = vistos.get(tipo, 0) + 1
        if vistos[tipo] > 1 and tipo not in TIPOS_COM_REPETICAO:
            raise DomainError(
                "O elemento '{}' so pode aparecer uma vez no modelo.".format(
                    FieldType(tipo).label
                )
            )

    with transaction.atomic():
        CertificateTemplateField.objects.select_for_update().filter(
            template=template
        ).exclude(field_type=FieldType.STATIC_IMAGE).delete()

        for elemento in limpos:
            CertificateTemplateField.objects.create(template=template, **elemento)

        # Uma linha na trilha por SAVE, e nao por pixel movido. Arrastar uma
        # caixa nao e um ato administrativo; publicar o resultado e.
        record(
            AuditEvent.CERTIFICATE_TEMPLATE_UPDATED,
            request=request,
            actor=actor,
            entity_type="CertificateTemplate",
            entity_id=template.pk,
            metadata={
                "elements": len(limpos),
                "types": sorted({e["field_type"] for e in limpos}),
            },
        )
    return template


def save_fields(template, campos, *, actor=None, request=None):
    """
    Mesma gravacao, a partir de {field_type: dados}.

    Continua existindo porque o formulario antigo — e os scripts que o
    imitam — falam nesse formato. Um dicionario nao consegue expressar dois
    blocos de texto personalizado; para isso existe save_elements.
    """
    elementos = []
    for tipo, dados in campos.items():
        if tipo == FieldType.STATIC_IMAGE:
            continue
        if tipo not in FieldType.values:
            raise DomainError("Campo desconhecido: {}.".format(tipo))
        elementos.append({**dados, "type": tipo})
    return save_elements(template, elementos, actor=actor, request=request)


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
                font_weight=campo.font_weight,
                italic=campo.italic,
                content=campo.content,
                wrap=campo.wrap,
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
