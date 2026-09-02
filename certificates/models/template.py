"""
Modelo de certificado: a arte oficial mais os campos posicionados sobre ela.

A inversao desta etapa
----------------------
Ate a Etapa 9 o desenho do certificado era codigo Python: moldura, ornamentos
e tipografia estavam em `certificates/pdf.py`, e a fidelidade ao documento
institucional dependia de o programa reproduzir a arte a mao. Isso nunca vai
ficar identico, e cada ajuste custa um deploy.

Agora a estetica vem de fora. O administrador envia a arte oficial, o
renderizador a usa como fundo e escreve por cima apenas os valores que mudam
de aluno para aluno. O sistema deixou de inventar design.

Coordenadas em porcentagem
--------------------------
x, y, width e height sao percentuais de 0 a 100 da pagina, com origem no
CANTO SUPERIOR ESQUERDO — x cresce para a direita, y cresce para BAIXO, e
(x, y) e o canto superior esquerdo da caixa.

Duas razoes. A primeira: trocar a resolucao do fundo (300 dpi por 600 dpi)
nao move nada, porque nada esta preso a pixel. A segunda: quem posiciona
esta olhando uma tela, e em tela o eixo vertical cresce para baixo. O PDF usa
o oposto — origem embaixo — e a conversao acontece num lugar so, no
renderizador.

Imutabilidade
-------------
Um modelo que ja emitiu certificado nao muda mais. Nao e capricho: mesmo com
o snapshot que cada certificado guarda, deixar o layout usado ser reescrito
significaria que a resposta a "como este documento foi produzido?" depende de
quando a pergunta e feita. Para mudar, duplica-se — mesmo caminho das provas.
"""

import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

# ---------------------------------------------------------------------------
# Listas brancas
#
# Tudo que o navegador escolhe e escolhido DENTRO destas listas. Nenhuma
# string vinda do formulario vira nome de fonte, caminho de arquivo ou
# propriedade CSS por conta propria.
# ---------------------------------------------------------------------------

# As Type 1 embutidas no proprio formato PDF. Nenhum arquivo de fonte precisa
# existir no servidor, e por isso mesmo a lista nao pode crescer sozinha: um
# nome fora dela faria o ReportLab procurar um arquivo que nao existe.
#
# Esta tupla e o que o RENDERIZADOR aceita. Nao e o que a tela oferece: ali o
# administrador escolhe familia + negrito + italico, e a combinacao e
# resolvida para um destes nomes por resolver_fonte(). Ver FAMILIAS_DE_FONTE.
FONTES_PERMITIDAS = (
    "Helvetica",
    "Helvetica-Bold",
    "Helvetica-Oblique",
    "Helvetica-BoldOblique",
    "Times-Roman",
    "Times-Bold",
    "Times-Italic",
    "Times-BoldItalic",
    "Courier",
    "Courier-Bold",
    "Courier-Oblique",
    "Courier-BoldOblique",
)

# Familia + negrito + italico -> nome PostScript.
#
# O campo guarda a FAMILIA e dois booleanos, e nao o nome composto. A
# alternativa — guardar "Times-BoldItalic" e mais duas caixas de selecao —
# criaria dois lugares para o mesmo fato, e dois lugares divergem: bastaria
# gravar "Times-Bold" com italico marcado para ninguem saber qual dos dois
# manda.
#
# Repare que a regular do Times chama "Times-Roman" e a inclinada da
# Helvetica chama "Oblique". Sao os nomes reais do formato, nao ha padrao
# entre as familias, e e exatamente por isso que a composicao precisa de uma
# tabela em vez de concatenacao de strings.
FAMILIAS_DE_FONTE = {
    "Helvetica": {
        (False, False): "Helvetica",
        (True, False): "Helvetica-Bold",
        (False, True): "Helvetica-Oblique",
        (True, True): "Helvetica-BoldOblique",
    },
    "Times": {
        (False, False): "Times-Roman",
        (True, False): "Times-Bold",
        (False, True): "Times-Italic",
        (True, True): "Times-BoldItalic",
    },
    "Courier": {
        (False, False): "Courier",
        (True, False): "Courier-Bold",
        (False, True): "Courier-Oblique",
        (True, True): "Courier-BoldOblique",
    },
}

FAMILIAS_PERMITIDAS = tuple(FAMILIAS_DE_FONTE)
FAMILIA_PADRAO = "Helvetica"

# Nome PostScript -> (familia, negrito, italico). Construido a partir da
# tabela acima para que as duas nunca discordem.
_DECOMPOSICAO = {
    nome: (familia, negrito, italico)
    for familia, variantes in FAMILIAS_DE_FONTE.items()
    for (negrito, italico), nome in variantes.items()
}


def decompor_fonte(valor):
    """
    (familia, negrito, italico) a partir de familia OU de nome composto.

    Aceita os dois porque os dois existem no historico: os campos gravados
    ate agora guardavam "Times-BoldItalic", e os snapshots ja emitidos
    continuam guardando. Um documento antigo precisa continuar saindo com a
    fonte com que foi assinado.
    """
    texto = (valor or "").strip()
    if texto in _DECOMPOSICAO:
        return _DECOMPOSICAO[texto]
    if texto in FAMILIAS_DE_FONTE:
        return texto, False, False
    return FAMILIA_PADRAO, False, False


def resolver_fonte(valor, negrito=False, italico=False):
    """
    Nome PostScript de uma familia com os atributos pedidos.

    Os atributos do nome recebido e os booleanos se SOMAM: pedir negrito
    sobre "Times-Italic" da "Times-BoldItalic". E o que torna a funcao
    idempotente — resolver duas vezes devolve o mesmo nome — e o que permite
    aplica-la sobre um snapshot antigo sem alterar o resultado.
    """
    familia, ja_negrito, ja_italico = decompor_fonte(valor)
    variantes = FAMILIAS_DE_FONTE.get(familia) or FAMILIAS_DE_FONTE[FAMILIA_PADRAO]
    return variantes[(bool(ja_negrito or negrito), bool(ja_italico or italico))]


# Somente #RRGGBB. Nao e "validacao de cor": e a recusa de qualquer coisa que
# pareca CSS. `red; background: url(...)` nao passa por aqui.
CORES_ACEITAS = re.compile(r"^#[0-9A-Fa-f]{6}$")

LIMITE_DA_FONTE = (6, 120)
LIMITE_DA_ROTACAO = (-360, 360)

# 10 MB. Um A4 em 300 dpi comprimido em PNG cabe com folga; o teto existe
# para que um arquivo enorme nao ocupe o disco da instancia nem a memoria do
# processo que o valida.
TAMANHO_MAXIMO_DO_FUNDO = 10 * 1024 * 1024


def _nome_interno(instancia, nome_enviado, pasta):
    """
    Nome de arquivo gerado aqui, nunca o que veio do navegador.

    O nome enviado e dado do usuario: pode conter `../`, pode conter bytes
    que o sistema de arquivos interpreta, pode colidir com um arquivo
    existente e sobrescrever a arte de outro modelo. Nada disso precisa ser
    filtrado se o nome simplesmente nao for usado.

    Da extensao aproveita-se apenas o sufixo, e ainda assim conferido contra
    a lista branca de uploads — o formato real e verificado abrindo o
    arquivo, e nao pelo que o nome promete.
    """
    sufixo = ""
    if "." in (nome_enviado or ""):
        candidata = nome_enviado.rsplit(".", 1)[-1].lower()
        if candidata.isalnum() and len(candidata) <= 5:
            sufixo = ".{}".format(candidata)
    return "{}/{}{}".format(pasta, uuid.uuid4().hex, sufixo)


def caminho_do_fundo(instancia, nome_enviado):
    return _nome_interno(instancia, nome_enviado, "certificate_templates/fundos")


def caminho_do_asset(instancia, nome_enviado):
    return _nome_interno(instancia, nome_enviado, "certificate_templates/assets")


class TemplateStatus(models.TextChoices):
    """
    Ciclo de vida do modelo.

    DRAFT -> ACTIVE -> ARCHIVED. Um booleano `is_active` nao daria conta:
    "ainda sendo montado" e "aposentado depois de ter sido usado" sao estados
    diferentes, e os dois responderiam False.
    """

    DRAFT = "DRAFT", "Rascunho"
    ACTIVE = "ACTIVE", "Ativo"
    ARCHIVED = "ARCHIVED", "Arquivado"


class PageOrientation(models.TextChoices):
    LANDSCAPE = "LANDSCAPE", "Paisagem"
    PORTRAIT = "PORTRAIT", "Retrato"


class TextAlign(models.TextChoices):
    LEFT = "LEFT", "Esquerda"
    CENTER = "CENTER", "Centro"
    RIGHT = "RIGHT", "Direita"


ALINHAMENTOS = tuple(TextAlign.values)


class FieldType(models.TextChoices):
    """
    O que cada campo imprime.

    Lista fechada, e essa e a parte que importa. O administrador escolhe um
    destes; ele nao digita `{{qualquer_coisa}}` para o sistema resolver por
    introspeccao. Um placeholder livre resolvido com getattr transformaria a
    tela de edicao num leitor de atributos arbitrarios do objeto Certificate
    — e dali para `attempt.student.password` e um passo.

    A traducao de cada valor para um dado do certificado vive em
    certificates.render, num dicionario explicito.
    """

    STUDENT_NAME = "STUDENT_NAME", "Nome do aluno"
    # A data em que a avaliacao foi fechada — nao a de hoje, nao a do
    # download. Ver Certificate.completed_at_snapshot.
    COMPLETION_DATE = "COMPLETION_DATE", "Data de conclusao"
    COURSE_NAME = "COURSE_NAME", "Nome do curso"
    MODULE_NAME = "MODULE_NAME", "Modulo"
    COURSE_DATES = "COURSE_DATES", "Data(s) do curso"
    COURSE_LOCATION = "COURSE_LOCATION", "Local"
    WORKLOAD = "WORKLOAD", "Carga horaria"
    YEAR = "YEAR", "Ano"
    ISSUED_AT = "ISSUED_AT", "Data de emissao"
    INSTITUTION = "INSTITUTION", "Instituicao"
    SIGNATORY_NAME = "SIGNATORY_NAME", "Signatario"
    SIGNATORY_TITLE = "SIGNATORY_TITLE", "Cargo do signatario"
    VERIFICATION_CODE = "VERIFICATION_CODE", "Codigo de validacao"
    QR_CODE = "QR_CODE", "QR Code"
    STATIC_IMAGE = "STATIC_IMAGE", "Imagem fixa"


# Campos que nao sao texto. Nao usam fonte, tamanho nem alinhamento, e a tela
# de edicao esconde esses controles em vez de oferecer algo sem efeito.
TIPOS_DE_IMAGEM = frozenset({FieldType.QR_CODE, FieldType.STATIC_IMAGE})


class CertificateTemplateQuerySet(models.QuerySet):
    def ativos(self):
        return self.filter(status=TemplateStatus.ACTIVE)

    def editaveis(self):
        return self.exclude(status=TemplateStatus.ARCHIVED)


class CertificateTemplate(models.Model):
    """
    A arte oficial e a pagina em que ela sera impressa.

    O fundo e a identidade visual: moldura, ornamentos, logo, tipografia do
    titulo. Nada disso e redesenhado em codigo. O que o renderizador
    acrescenta sao os valores que mudam de aluno para aluno, posicionados
    pelos CertificateTemplateField.
    """

    name = models.CharField("nome", max_length=120)
    description = models.TextField("descricao", blank=True)

    status = models.CharField(
        "situacao",
        max_length=10,
        choices=TemplateStatus.choices,
        default=TemplateStatus.DRAFT,
        db_index=True,
    )

    # Marcador do fallback global. Um modelo global e o usado por qualquer
    # modulo que nao tenha escolhido o seu.
    is_global = models.BooleanField(
        "modelo padrao",
        default=False,
        help_text=(
            "Usado por qualquer modulo que nao tenha um modelo proprio "
            "configurado. So pode existir um ativo."
        ),
    )

    background = models.FileField(
        "arte de fundo",
        upload_to=caminho_do_fundo,
        blank=True,
        help_text="PNG ou JPG da arte oficial, sem os campos variaveis.",
    )
    # Guardado na hora do upload. Serve para duas coisas: identificar a
    # versao exata da arte dentro do snapshot de um certificado, e detectar
    # que o arquivo em disco deixou de ser o que era.
    background_checksum = models.CharField(
        "checksum da arte", max_length=64, blank=True
    )
    background_width = models.PositiveIntegerField(
        "largura da arte em pixels", null=True, blank=True
    )
    background_height = models.PositiveIntegerField(
        "altura da arte em pixels", null=True, blank=True
    )

    page_orientation = models.CharField(
        "orientacao",
        max_length=10,
        choices=PageOrientation.choices,
        default=PageOrientation.LANDSCAPE,
    )
    # Em milimetros, que e a unidade em que o formato do papel e conversado.
    # A4 paisagem = 297 x 210.
    page_width_mm = models.DecimalField(
        "largura da pagina em mm",
        max_digits=6,
        decimal_places=2,
        default=297,
        validators=[MinValueValidator(50), MaxValueValidator(2000)],
    )
    page_height_mm = models.DecimalField(
        "altura da pagina em mm",
        max_digits=6,
        decimal_places=2,
        default=210,
        validators=[MinValueValidator(50), MaxValueValidator(2000)],
    )

    # Numeracao da linhagem, no mesmo desenho das provas: duplicar produz a
    # proxima versao, e a origem fica registrada.
    version = models.PositiveIntegerField("versao", default=1)
    parent_template = models.ForeignKey(
        "self",
        verbose_name="modelo de origem",
        # PROTECT, e nao SET_NULL: a procedencia de uma versao faz parte do
        # historico dela. Um modelo com descendente nao e apagado.
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="derivados",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="criado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificate_templates_criados",
    )
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    objects = CertificateTemplateQuerySet.as_manager()

    class Meta:
        verbose_name = "modelo de certificado"
        verbose_name_plural = "modelos de certificado"
        ordering = ["name", "-version"]
        constraints = [
            models.CheckConstraint(
                condition=Q(page_width_mm__gt=0) & Q(page_height_mm__gt=0),
                name="modelo_pagina_positiva",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="modelo_versao_pelo_menos_um",
            ),
            # Um modelo ATIVO precisa ter arte. Sem fundo ele nao e um modelo
            # de certificado: e uma folha em branco com texto solto, que e
            # exatamente o resultado que esta etapa existe para eliminar.
            models.CheckConstraint(
                condition=(
                    ~Q(status=TemplateStatus.ACTIVE) | ~Q(background="")
                ),
                name="modelo_ativo_tem_arte",
            ),
            # No maximo um modelo global ativo. O global e o fallback de
            # quem nao configurou nada; dois candidatos fariam a emissao
            # depender da ordenacao da consulta.
            #
            # A condicao cobre so o global porque o vinculo por modulo mora
            # em Module.certificate_template: o modulo aponta para o modelo,
            # e nao o contrario. Ver a docstring de resolver_template.
            models.UniqueConstraint(
                fields=["is_global"],
                condition=Q(status=TemplateStatus.ACTIVE, is_global=True),
                name="modelo_global_ativo_unico",
            ),
        ]

    def __str__(self):
        return "{} (v{})".format(self.name, self.version)

    def save(self, *args, **kwargs):
        self.name = (self.name or "").strip()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "O nome do modelo e obrigatorio."})

    # -- Estado -------------------------------------------------------------

    @property
    def e_rascunho(self):
        return self.status == TemplateStatus.DRAFT

    @property
    def esta_ativo(self):
        return self.status == TemplateStatus.ACTIVE

    @property
    def esta_arquivado(self):
        return self.status == TemplateStatus.ARCHIVED

    @property
    def tem_arte(self):
        return bool(self.background)

    def esta_em_uso(self):
        """Se algum certificado ja foi emitido com este modelo."""
        return self.certificates.exists()

    @property
    def editavel(self):
        """
        Se a configuracao ainda pode mudar.

        Arquivado nunca muda. Um modelo que ja emitiu certificado tambem nao:
        ainda que cada certificado carregue o proprio snapshot e nao fosse
        afetado, deixar o layout usado ser reescrito faria a resposta a "como
        este documento foi produzido?" depender de quando a pergunta e feita.

        O caminho para mudar e duplicar, editar a copia e ativa-la — o mesmo
        das provas desde a Etapa 3.
        """
        if self.esta_arquivado:
            return False
        return not self.esta_em_uso()

    @property
    def dimensoes_em_pontos(self):
        """Tamanho da pagina em pontos PDF, que e a unidade do ReportLab."""
        from reportlab.lib.units import mm

        return (float(self.page_width_mm) * mm, float(self.page_height_mm) * mm)

    def campos_visiveis(self):
        return self.fields.filter(is_visible=True).order_by("z_index", "pk")

    def tipos_configurados(self):
        return set(self.fields.values_list("field_type", flat=True))

    def pendencias_para_ativar(self):
        """
        O que impede este modelo de ser ativado. Lista vazia significa que
        pode.

        Devolve motivos legiveis, e nao um booleano: "nao pode ativar" sem
        dizer por que leva o administrador a tentar de novo.
        """
        faltando = []
        if not self.tem_arte:
            faltando.append("Envie a arte de fundo antes de ativar o modelo.")
        if not self.fields.filter(is_visible=True).exists():
            faltando.append(
                "Configure ao menos um campo visivel antes de ativar o modelo."
            )
        return faltando


class CertificateTemplateField(models.Model):
    """
    Um valor variavel posicionado sobre a arte.

    Nao guarda texto: guarda QUAL dado imprimir (field_type) e ONDE. O texto
    vem do certificado no momento da renderizacao, ou do snapshot dele.
    """

    template = models.ForeignKey(
        CertificateTemplate,
        verbose_name="modelo",
        on_delete=models.CASCADE,
        related_name="fields",
    )
    field_type = models.CharField(
        "campo", max_length=24, choices=FieldType.choices
    )

    # Percentuais da pagina, origem no canto superior esquerdo. Ver a
    # docstring do modulo.
    x = models.DecimalField(
        "x (%)",
        max_digits=6,
        decimal_places=2,
        default=10,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    y = models.DecimalField(
        "y (%)",
        max_digits=6,
        decimal_places=2,
        default=10,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    width = models.DecimalField(
        "largura (%)",
        max_digits=6,
        decimal_places=2,
        default=30,
        validators=[MinValueValidator(0.1), MaxValueValidator(100)],
    )
    height = models.DecimalField(
        "altura (%)",
        max_digits=6,
        decimal_places=2,
        default=8,
        validators=[MinValueValidator(0.1), MaxValueValidator(100)],
    )

    # A FAMILIA, nao o nome composto: "Times", e nao "Times-BoldItalic". O
    # peso e a inclinacao moram em bold/italic, e resolver_fonte junta os
    # tres num nome PostScript na hora de renderizar.
    font_family = models.CharField(
        "fonte", max_length=32, default=FAMILIA_PADRAO, choices=[
            (familia, familia) for familia in FAMILIAS_PERMITIDAS
        ]
    )
    bold = models.BooleanField("negrito", default=False)
    italic = models.BooleanField("italico", default=False)
    font_size = models.PositiveSmallIntegerField(
        "tamanho",
        default=14,
        validators=[
            MinValueValidator(LIMITE_DA_FONTE[0]),
            MaxValueValidator(LIMITE_DA_FONTE[1]),
        ],
    )
    # Piso do auto-ajuste. Existe para que "caber" nunca signifique
    # "ilegivel": abaixo disto o renderizador prefere quebrar em mais linhas.
    min_font_size = models.PositiveSmallIntegerField(
        "tamanho minimo",
        default=8,
        validators=[
            MinValueValidator(LIMITE_DA_FONTE[0]),
            MaxValueValidator(LIMITE_DA_FONTE[1]),
        ],
    )
    auto_fit = models.BooleanField(
        "ajustar automaticamente",
        default=True,
        help_text="Reduz a fonte ate o texto caber na caixa.",
    )
    line_height = models.DecimalField(
        "entrelinha",
        max_digits=4,
        decimal_places=2,
        default=1.2,
        validators=[MinValueValidator(0.8), MaxValueValidator(3)],
    )

    text_align = models.CharField(
        "alinhamento",
        max_length=8,
        choices=TextAlign.choices,
        default=TextAlign.CENTER,
    )
    text_color = models.CharField("cor", max_length=7, default="#000000")

    rotation = models.SmallIntegerField(
        "rotacao",
        default=0,
        validators=[
            MinValueValidator(LIMITE_DA_ROTACAO[0]),
            MaxValueValidator(LIMITE_DA_ROTACAO[1]),
        ],
    )

    # Imagem propria do campo, usada somente por STATIC_IMAGE: logo,
    # assinatura, selo. O QR nao usa — ele e gerado na hora, a partir do
    # codigo de verificacao do certificado.
    image = models.FileField(
        "imagem", upload_to=caminho_do_asset, blank=True
    )

    is_visible = models.BooleanField("visivel", default=True)
    z_index = models.PositiveSmallIntegerField(
        "ordem de desenho",
        default=0,
        help_text="Maior desenha por cima. O fundo esta sempre atras de tudo.",
    )

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "campo do modelo"
        verbose_name_plural = "campos do modelo"
        ordering = ["z_index", "pk"]
        constraints = [
            # Um campo de cada tipo por modelo. A excecao e a imagem fixa:
            # logo, assinatura e selo sao tres imagens no mesmo documento, e
            # todas sao STATIC_IMAGE.
            models.UniqueConstraint(
                fields=["template", "field_type"],
                condition=~Q(field_type=FieldType.STATIC_IMAGE),
                name="campo_unico_por_modelo",
            ),
            models.CheckConstraint(
                condition=(
                    Q(x__gte=0)
                    & Q(x__lte=100)
                    & Q(y__gte=0)
                    & Q(y__lte=100)
                    & Q(width__gt=0)
                    & Q(width__lte=100)
                    & Q(height__gt=0)
                    & Q(height__lte=100)
                ),
                name="campo_dentro_da_pagina",
            ),
            models.CheckConstraint(
                condition=(
                    Q(font_size__gte=LIMITE_DA_FONTE[0])
                    & Q(font_size__lte=LIMITE_DA_FONTE[1])
                    & Q(min_font_size__gte=LIMITE_DA_FONTE[0])
                    & Q(min_font_size__lte=LIMITE_DA_FONTE[1])
                ),
                name="campo_fonte_em_faixa_util",
            ),
            # O piso do auto-ajuste nao pode ser maior que o tamanho pedido:
            # o renderizador comeca no maior e desce ate o minimo, e a faixa
            # invertida faria o laco nao ter para onde ir.
            models.CheckConstraint(
                condition=Q(min_font_size__lte=F("font_size")),
                name="campo_minimo_nao_passa_do_tamanho",
            ),
            models.CheckConstraint(
                condition=(
                    Q(rotation__gte=LIMITE_DA_ROTACAO[0])
                    & Q(rotation__lte=LIMITE_DA_ROTACAO[1])
                ),
                name="campo_rotacao_em_faixa",
            ),
            # A cor e o unico campo livre que chega perto de virar marcacao.
            # A aplicacao valida por regex; esta constraint e a camada que
            # sobrevive a um UPDATE direto no banco.
            models.CheckConstraint(
                condition=Q(text_color__regex=r"^#[0-9A-Fa-f]{6}$"),
                name="campo_cor_hexadecimal",
            ),
            # A familia tambem: um valor fora da tabela faria o ReportLab
            # procurar um arquivo de fonte que nao existe no servidor, e a
            # falha apareceria no meio de uma emissao.
            models.CheckConstraint(
                condition=Q(font_family__in=FAMILIAS_PERMITIDAS),
                name="campo_familia_de_fonte_conhecida",
            ),
            # Imagem fixa exige arquivo; os demais tipos nao carregam
            # arquivo nenhum. Sem isto, um campo de texto poderia guardar um
            # upload que nada desenha e ninguem apaga.
            models.CheckConstraint(
                condition=(
                    Q(field_type=FieldType.STATIC_IMAGE)
                    | Q(image="")
                ),
                name="campo_imagem_so_em_imagem_fixa",
            ),
        ]

    def __str__(self):
        return "{} em {}".format(self.get_field_type_display(), self.template.name)

    @property
    def e_imagem(self):
        return self.field_type in TIPOS_DE_IMAGEM

    @property
    def fonte_resolvida(self):
        """Nome PostScript que o ReportLab vai receber."""
        return resolver_fonte(self.font_family, self.bold, self.italic)

    def clean(self):
        super().clean()
        if self.font_family not in FAMILIAS_PERMITIDAS:
            raise ValidationError({"font_family": "Fonte nao permitida."})
        if not CORES_ACEITAS.match(self.text_color or ""):
            raise ValidationError(
                {"text_color": "Informe a cor no formato #RRGGBB."}
            )
        if self.min_font_size > self.font_size:
            raise ValidationError(
                {
                    "min_font_size": (
                        "O tamanho minimo nao pode ser maior que o tamanho."
                    )
                }
            )
