"""Perfil academico do aluno."""

from django.conf import settings
from django.db import models


class StudentSource(models.TextChoices):
    """
    Origem do cadastro do aluno.

    Serve para rastrear como cada conta entrou no sistema. Novas origens
    (portal de inscricao, integracao) entram aqui quando existirem, sem
    alterar a estrutura.
    """

    MANUAL = "MANUAL", "Cadastro manual"
    IMPORT = "IMPORT", "Importacao de planilha"


class StudentProfile(models.Model):
    """
    Dados academicos e administrativos do aluno.

    Identidade e autenticacao ficam em accounts.User; nada e duplicado aqui.
    O perfil existe para que informacao administrativa (observacoes, origem e,
    no futuro, telefone ou documento) nao inche o modelo de autenticacao.

    A criacao e sempre explicita, feita por students.services.create_student.
    Nao existe signal criando perfil por baixo dos panos: um signal tornaria
    importacoes e testes imprevisiveis, escondendo em que momento o registro
    aparece.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="usuario",
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    notes = models.TextField(
        "observacoes",
        blank=True,
        help_text="Uso administrativo. O aluno nao ve este campo.",
    )
    source = models.CharField(
        "origem",
        max_length=20,
        choices=StudentSource.choices,
        default=StudentSource.MANUAL,
        db_index=True,
    )

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "perfil de aluno"
        verbose_name_plural = "perfis de aluno"
        ordering = ["user__full_name"]

    def __str__(self):
        return "Perfil de {}".format(self.user.full_name)
