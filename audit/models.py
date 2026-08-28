"""Registro de auditoria."""

from django.conf import settings
from django.db import models


class AuditEvent(models.TextChoices):
    """
    Eventos auditaveis.

    A lista cresce a cada etapa; eventos existentes nunca sao removidos nem
    renomeados, porque isso invalidaria a trilha ja gravada.
    """

    # Autenticacao (Etapa 1)
    LOGIN_SUCCESS = "LOGIN_SUCCESS", "Login realizado"
    LOGIN_FAILED = "LOGIN_FAILED", "Falha de login"
    PASSWORD_CHANGED = "PASSWORD_CHANGED", "Senha alterada"

    # Alunos (Etapa 2)
    STUDENT_CREATED = "STUDENT_CREATED", "Aluno criado"
    STUDENT_UPDATED = "STUDENT_UPDATED", "Aluno atualizado"
    STUDENT_BLOCKED = "STUDENT_BLOCKED", "Aluno bloqueado"
    STUDENT_UNBLOCKED = "STUDENT_UNBLOCKED", "Aluno desbloqueado"
    STUDENT_IMPORT_COMPLETED = "STUDENT_IMPORT_COMPLETED", "Importacao concluida"

    # Modulos (Etapa 2)
    MODULE_CREATED = "MODULE_CREATED", "Modulo criado"
    MODULE_UPDATED = "MODULE_UPDATED", "Modulo atualizado"
    MODULE_ENABLED = "MODULE_ENABLED", "Modulo ativado"
    MODULE_DISABLED = "MODULE_DISABLED", "Modulo desativado"

    # Matriculas (Etapa 2)
    ENROLLMENT_CREATED = "ENROLLMENT_CREATED", "Matricula criada"
    ENROLLMENT_REMOVED = "ENROLLMENT_REMOVED", "Matricula desativada"
    ENROLLMENT_REACTIVATED = "ENROLLMENT_REACTIVATED", "Matricula reativada"
    ENROLLMENT_BLOCKED = "ENROLLMENT_BLOCKED", "Acesso da matricula bloqueado"
    ENROLLMENT_UNBLOCKED = "ENROLLMENT_UNBLOCKED", "Acesso da matricula liberado"
    ENROLLMENT_COMPLETED = "ENROLLMENT_COMPLETED", "Matricula concluida"

    # Provas (Etapa 3)
    EXAM_CREATED = "EXAM_CREATED", "Prova criada"
    EXAM_UPDATED = "EXAM_UPDATED", "Prova atualizada"
    EXAM_PUBLISHED = "EXAM_PUBLISHED", "Prova publicada"
    EXAM_CLOSED = "EXAM_CLOSED", "Prova fechada"
    EXAM_DUPLICATED = "EXAM_DUPLICATED", "Prova duplicada"
    # Registram apenas o fato de a senha ter mudado. Nem a senha, nem o hash,
    # nem o comprimento entram na metadata.
    EXAM_PASSWORD_CHANGED = "EXAM_PASSWORD_CHANGED", "Senha da prova alterada"
    EXAM_PASSWORD_REMOVED = "EXAM_PASSWORD_REMOVED", "Senha da prova removida"

    QUESTION_CREATED = "QUESTION_CREATED", "Questao criada"
    QUESTION_UPDATED = "QUESTION_UPDATED", "Questao atualizada"
    QUESTION_DELETED = "QUESTION_DELETED", "Questao excluida"


class AuditLog(models.Model):
    """
    Trilha de auditoria, somente insercao.

    O modelo bloqueia atualizacao e exclusao na camada de aplicacao. Uma
    remocao por politica de retencao continua possivel no banco, de forma
    deliberada e fora do alcance da interface.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="autor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs_como_autor",
        help_text="Quem executou a acao. Nulo em falha de login.",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="aluno",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs_como_aluno",
        help_text="Aluno afetado, quando a acao recai sobre um aluno.",
    )

    event = models.CharField(
        "evento", max_length=64, choices=AuditEvent.choices, db_index=True
    )
    entity_type = models.CharField("tipo da entidade", max_length=64, blank=True)
    entity_id = models.CharField("id da entidade", max_length=64, blank=True)

    timestamp = models.DateTimeField("data e hora", auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField("endereco IP", null=True, blank=True)
    user_agent = models.TextField("user-agent", blank=True)
    metadata = models.JSONField("metadados", default=dict, blank=True)

    class Meta:
        verbose_name = "registro de auditoria"
        verbose_name_plural = "registros de auditoria"
        ordering = ["-timestamp", "-id"]
        indexes = [
            models.Index(fields=["event", "-timestamp"], name="audit_evento_data_idx"),
            models.Index(fields=["student", "-timestamp"], name="audit_aluno_data_idx"),
            models.Index(
                fields=["entity_type", "entity_id"], name="audit_entidade_idx"
            ),
        ]

    def __str__(self):
        return "{} em {:%d/%m/%Y %H:%M:%S}".format(self.event, self.timestamp)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError(
                "AuditLog e somente insercao: um registro existente nao pode "
                "ser alterado."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "AuditLog e somente insercao: um registro nao pode ser excluido "
            "pela aplicacao."
        )
