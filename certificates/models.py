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
VERSAO_ATUAL_DO_MODELO = 1


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
