"""Modulos do curso e matriculas."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Upper


def normalizar_codigo(codigo):
    """
    Forma canonica do codigo de um modulo.

    " mod1 " vira "MOD1". Normalizar na escrita e o que torna MOD1, mod1 e
    Mod1 o mesmo modulo, sem depender de comparacao case-insensitive em toda
    consulta.
    """
    if not codigo:
        return codigo
    return codigo.strip().upper()


class ModuleQuerySet(models.QuerySet):
    def ativos(self):
        return self.filter(is_active=True)


class Module(models.Model):
    """Modulo do curso. Uma prova pertencera a um modulo (Etapa 3)."""

    name = models.CharField("nome", max_length=150)
    code = models.CharField(
        "codigo",
        max_length=30,
        unique=True,
        help_text="Identificador curto, como MOD1. Gravado sempre em maiusculas.",
        error_messages={"unique": "Ja existe um modulo com este codigo."},
    )
    description = models.TextField("descricao", blank=True)
    is_active = models.BooleanField(
        "ativo",
        default=True,
        db_index=True,
        help_text="Modulo inativo nao aparece para o aluno.",
    )
    order = models.PositiveIntegerField(
        "ordem",
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Define a ordem de exibicao. Menor aparece primeiro.",
    )

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    objects = ModuleQuerySet.as_manager()

    class Meta:
        verbose_name = "modulo"
        verbose_name_plural = "modulos"
        ordering = ["order", "name"]
        constraints = [
            # PositiveIntegerField ja recusa negativos no PostgreSQL, mas a
            # constraint explicita documenta a regra e sobrevive a uma
            # eventual troca do tipo do campo.
            models.CheckConstraint(
                condition=models.Q(order__gte=0),
                name="module_ordem_nao_negativa",
            ),
            # unique=True no campo ja garante a unicidade porque save()
            # sempre grava em maiusculas. Este indice funcional cobre os
            # caminhos que nao passam por save(), como bulk_create e SQL
            # direto, e e a garantia real de que MOD1 e mod1 nao coexistem.
            models.UniqueConstraint(
                Upper("code"),
                name="module_codigo_unico_ignorando_caixa",
            ),
        ]

    def __str__(self):
        return "{} - {}".format(self.code, self.name)

    def save(self, *args, **kwargs):
        self.code = normalizar_codigo(self.code)
        self.name = (self.name or "").strip()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.code = normalizar_codigo(self.code)
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "O nome do modulo e obrigatorio."})
        if not self.code:
            raise ValidationError({"code": "O codigo do modulo e obrigatorio."})


class EnrollmentStatus(models.TextChoices):
    """
    Situacao academica da matricula.

    Nao confundir com access_enabled, que e uma chave operacional
    independente. Ver a docstring de Enrollment.
    """

    ACTIVE = "ACTIVE", "Ativa"
    INACTIVE = "INACTIVE", "Inativa"
    COMPLETED = "COMPLETED", "Concluida"


class EnrollmentQuerySet(models.QuerySet):
    def liberadas(self):
        """
        Matriculas que efetivamente dao acesso ao modulo.

        As tres condicoes precisam valer ao mesmo tempo: situacao academica
        ativa, acesso operacional liberado e modulo ativo. Este e o unico
        criterio que a area do aluno usa.
        """
        return self.filter(
            status=EnrollmentStatus.ACTIVE,
            access_enabled=True,
            module__is_active=True,
        )

    def do_aluno(self, user):
        return self.filter(student=user)


class Enrollment(models.Model):
    """
    Vinculo entre um aluno e um modulo.

    Dois conceitos deliberadamente separados:

        status          situacao academica (ativa, inativa, concluida)
        access_enabled  chave operacional de acesso

    A combinacao status=ACTIVE com access_enabled=False significa "continua
    matriculado, mas o acesso esta suspenso agora". Bloquear um aluno as
    vesperas da prova nao pode alterar a situacao academica dele.
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="aluno",
        on_delete=models.CASCADE,
        related_name="enrollments",
        limit_choices_to={"role": "STUDENT"},
    )
    module = models.ForeignKey(
        Module,
        verbose_name="modulo",
        on_delete=models.PROTECT,
        related_name="enrollments",
    )

    status = models.CharField(
        "situacao",
        max_length=12,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ACTIVE,
        db_index=True,
    )
    access_enabled = models.BooleanField(
        "acesso liberado",
        default=True,
        help_text="Bloqueio operacional. Nao altera a situacao academica.",
    )

    enrolled_at = models.DateTimeField("matriculado em", auto_now_add=True)
    notes = models.TextField("observacoes", blank=True)

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    objects = EnrollmentQuerySet.as_manager()

    class Meta:
        verbose_name = "matricula"
        verbose_name_plural = "matriculas"
        ordering = ["module__order", "module__name", "student__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "module"],
                name="matricula_unica_por_aluno_e_modulo",
            ),
        ]
        indexes = [
            models.Index(
                fields=["student", "status"], name="matricula_aluno_situacao_idx"
            ),
        ]

    def __str__(self):
        return "{} em {}".format(self.student.full_name, self.module.code)

    def clean(self):
        """
        Impede que um ADMIN seja matriculado como aluno.

        O PostgreSQL nao consegue expressar esta regra em constraint, porque
        ela depende de uma coluna de outra tabela. A garantia real fica na
        camada de servico; esta validacao cobre formularios e Django Admin.
        """
        super().clean()
        if self.student_id and getattr(self.student, "role", None) != "STUDENT":
            raise ValidationError(
                {"student": "Somente usuarios com papel ALUNO podem ser matriculados."}
            )

    @property
    def libera_acesso(self):
        """Se esta matricula, hoje, da acesso ao modulo."""
        return (
            self.status == EnrollmentStatus.ACTIVE
            and self.access_enabled
            and self.module.is_active
        )
