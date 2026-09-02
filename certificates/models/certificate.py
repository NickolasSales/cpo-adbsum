"""
Certificado de conclusao.

O que este modelo e
-------------------
Um documento, nao uma consulta. A diferenca importa: quando alguem abre a
validacao publica de um certificado emitido ha dois anos, a pagina precisa
mostrar o que o documento dizia naquele dia — nao o que o banco diz hoje.

Por isso os campos *_snapshot. Se o modulo "Modulo 1" for renomeado para
"Formacao Basica", ou se a instituicao mudar de nome, os certificados ja
emitidos continuam com o texto original. Renderizar a partir dos dados vivos
faria o documento mudar sozinho depois de assinado, o que e exatamente o que
um certificado nao pode fazer.

O que ele nao e
---------------
Nao e um PDF guardado. O arquivo e gerado sob demanda a partir destes campos.
Guardar milhares de PDFs no banco custaria espaco e nao acrescentaria nada:
o documento e deterministico, os dados de origem sao imutaveis, e o codigo de
verificacao e o que prova autenticidade.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class CertificateStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Valido"
    REVOKED = "REVOKED", "Revogado"


# Versao do desenho do documento. Quando o layout mudar, os certificados ja
# emitidos continuam apontando para a versao com que foram gerados, e o
# renderizador escolhe o desenho pelo numero — em vez de reimprimir um
# documento antigo com a cara nova.
#
#   1  layout provisorio da Etapa 6. Moldura simples, sem os dados de turma.
#   2  modelo oficial da AD Bras Sumare (Etapa 8). Exige data, local, carga
#      horaria e ano, que a versao 1 nao gravava.
#
# A escolha pelo numero nao e capricho de versionamento: um certificado da
# versao 1 nao TEM os campos que a versao 2 imprime. Renderizar todos com o
# desenho novo produziria um documento oficial com lacunas no lugar da data e
# da carga horaria.
VERSAO_ATUAL_DO_MODELO = 2


class CertificateQuerySet(models.QuerySet):
    def validos(self):
        return self.filter(status=CertificateStatus.ACTIVE)

    def do_aluno(self, user):
        return self.filter(attempt__student=user)


class Certificate(models.Model):
    """Certificado emitido a partir de uma tentativa aprovada."""

    attempt = models.OneToOneField(
        "exams.ExamAttempt",
        verbose_name="tentativa",
        on_delete=models.PROTECT,
        related_name="certificate",
        help_text="Uma tentativa aprovada gera no maximo um certificado.",
    )

    # Identificador publico do documento. UUID4 de proposito: entra em QR Code
    # impresso e em URL sem autenticacao, entao precisa ser impossivel de
    # adivinhar. Um id sequencial, ou qualquer coisa derivada do aluno, deixaria
    # a colecao inteira enumeravel por quem tivesse um unico certificado.
    verification_code = models.UUIDField(
        "codigo de verificacao", default=uuid.uuid4, unique=True, editable=False
    )

    status = models.CharField(
        "situacao",
        max_length=8,
        choices=CertificateStatus.choices,
        default=CertificateStatus.ACTIVE,
        db_index=True,
    )

    # --- o documento, congelado ------------------------------------------
    student_name_snapshot = models.CharField("nome do aluno", max_length=150)
    module_name_snapshot = models.CharField("nome do modulo", max_length=150)
    exam_title_snapshot = models.CharField("titulo da prova", max_length=200)
    institution_name_snapshot = models.CharField("instituicao", max_length=150)

    # --- o modelo oficial (Etapa 8) --------------------------------------
    #
    # Todos aceitam vazio/nulo porque os certificados da versao 1 nao os tem e
    # nao ha de onde tira-los: inventar "carga horaria: 8" para um documento
    # emitido antes da existencia do campo seria assinar um dado que ninguem
    # informou. Quem decide o que fazer com a ausencia e o renderizador, pela
    # template_version.
    #
    # Para certificados novos o servico de emissao exige os quatro do meio, e
    # recusa emitir sem eles.
    course_name_snapshot = models.CharField(
        "nome do curso", max_length=200, blank=True
    )
    module_display_name_snapshot = models.CharField(
        "modulo no certificado", max_length=150, blank=True
    )
    course_dates_snapshot = models.CharField(
        "data(s) do curso", max_length=120, blank=True
    )
    course_location_snapshot = models.CharField("local", max_length=120, blank=True)
    workload_hours_snapshot = models.PositiveSmallIntegerField(
        "carga horaria", null=True, blank=True
    )
    certificate_year_snapshot = models.PositiveSmallIntegerField(
        "ano", null=True, blank=True
    )
    signatory_name_snapshot = models.CharField(
        "signatario", max_length=150, blank=True
    )
    signatory_title_snapshot = models.CharField(
        "cargo do signatario", max_length=150, blank=True
    )

    # --- quando a conclusao aconteceu ------------------------------------
    #
    # Tres datas convivem neste modelo, e elas respondem perguntas
    # diferentes:
    #
    #   completed_at_snapshot   quando o aluno CONCLUIU
    #   issued_at               quando o documento foi emitido
    #   created_at              quando a linha entrou na tabela
    #
    # A que sai impressa como "data de conclusao" e a primeira, copiada de
    # ExamAttempt.graded_at no momento da emissao. Nao e detalhe: usar a data
    # de hoje faria o mesmo certificado imprimir uma data diferente a cada
    # download, e usar issued_at faria a conclusao "acontecer" no dia em que
    # alguem clicou em emitir — que pode ser semanas depois da prova.
    #
    # E copia, e nao leitura de attempt.graded_at na hora de desenhar: uma
    # correcao administrativa posterior na tentativa nao pode reescrever a
    # data de um documento ja assinado.
    #
    # Aceita nulo pelos certificados anteriores a esta etapa, que nao tinham
    # o campo. Para eles o renderizador cai em issued_at, que e a data mais
    # proxima da verdade que existe — e nao um valor inventado.
    completed_at_snapshot = models.DateTimeField(
        "concluido em", null=True, blank=True
    )

    # issued_at e o ato academico; created_at e a linha da tabela. Coincidem
    # hoje e vao continuar coincidindo, mas sao perguntas diferentes e uma
    # correcao de dado nao deveria mexer na data impressa no documento.
    issued_at = models.DateTimeField("emitido em", default=timezone.now)

    revoked_at = models.DateTimeField("revogado em", null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="revogado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="certificates_revoked",
    )
    revocation_reason = models.TextField("motivo da revogacao", blank=True)

    template_version = models.PositiveSmallIntegerField(
        "versao do modelo", default=VERSAO_ATUAL_DO_MODELO
    )

    # --- o modelo usado, e a copia congelada dele (Etapa 10) --------------
    #
    # A FK responde "qual modelo produziu este documento" e e o que impede o
    # modelo de ser apagado enquanto houver certificado emitido por ele.
    #
    # O snapshot responde "com QUE configuracao". Sao perguntas diferentes: o
    # modelo continua vivo e pode ganhar versoes novas, e nenhuma delas pode
    # reescrever um documento ja assinado. O renderizador le o snapshot, e
    # nao o modelo.
    #
    # Os dois aceitam vazio porque os certificados emitidos ate a Etapa 9 nao
    # tem nem um nem outro: eles foram desenhados por codigo, e continuam
    # sendo, pela template_version. Inventar um snapshot para eles seria
    # afirmar uma configuracao que nunca existiu.
    certificate_template = models.ForeignKey(
        "certificates.CertificateTemplate",
        verbose_name="modelo utilizado",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="certificates",
    )
    template_snapshot = models.JSONField(
        "configuracao congelada", default=dict, blank=True
    )

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    objects = CertificateQuerySet.as_manager()

    class Meta:
        verbose_name = "certificado"
        verbose_name_plural = "certificados"
        ordering = ["-issued_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        CertificateStatus.ACTIVE,
                        CertificateStatus.REVOKED,
                    ]
                ),
                name="certificado_situacao_conhecida",
            ),
            # Revogado sem data, ou data de revogacao num certificado valido,
            # sao estados que a interface nunca produz — e que um UPDATE
            # manual produziria em silencio. A pagina publica decide "valido"
            # ou "revogado" por este campo; ele nao pode mentir.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=CertificateStatus.REVOKED,
                        revoked_at__isnull=False,
                    )
                    | models.Q(
                        status=CertificateStatus.ACTIVE,
                        revoked_at__isnull=True,
                    )
                ),
                name="certificado_revogacao_coerente",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "-issued_at"], name="certificado_situacao_idx"
            ),
        ]

    def __str__(self):
        return "Certificado de {} em {}".format(
            self.student_name_snapshot, self.module_name_snapshot
        )

    @property
    def esta_valido(self):
        return self.status == CertificateStatus.ACTIVE

    @property
    def modulo_impresso(self):
        """
        Nome do modulo como sai no documento.

        Cai no snapshot antigo quando o novo nao existe: um certificado da
        versao 1 nunca teve module_display_name_snapshot, e module_name era o
        unico nome que ele carregava.
        """
        return (
            self.module_display_name_snapshot or ""
        ).strip() or self.module_name_snapshot

    @property
    def data_de_conclusao(self):
        """
        A data que sai impressa como conclusao.

        Cai em issued_at quando o snapshot nao existe — certificados
        emitidos antes desta etapa. Nunca devolve a data de hoje: um
        documento que muda de data a cada download nao e um documento.
        """
        return self.completed_at_snapshot or self.issued_at

    @property
    def codigo_resumido(self):
        """
        Codigo abreviado para o cartao da lista.

        UUID tem 36 caracteres e, exibido inteiro, e o elemento mais longo do
        cartao no celular — mais longo que o nome do modulo, e sem nenhum
        valor para quem so quer baixar o PDF. O codigo completo continua na
        pagina de validacao, no PDF e no botao de copiar.
        """
        texto = str(self.verification_code)
        return "{}…{}".format(texto[:8], texto[-5:])

    @property
    def nome_do_arquivo(self):
        """
        Nome sugerido para o PDF baixado.

        Construido a partir de uma lista branca de caracteres. O nome do aluno
        entra num cabecalho HTTP, e cabecalho aceita CR e LF: um nome com
        quebra de linha permitiria injetar cabecalhos na resposta.
        """
        from certificates.pdf import nome_de_arquivo_seguro

        return nome_de_arquivo_seguro(
            "certificado", self.module_name_snapshot, self.student_name_snapshot
        )
