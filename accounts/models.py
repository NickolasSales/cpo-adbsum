"""Modelo de usuario da aplicacao."""

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from accounts.managers import UserManager, normalizar_email


class UserRole(models.TextChoices):
    """Papeis do sistema."""

    ADMIN = "ADMIN", "Administrador"
    STUDENT = "STUDENT", "Aluno"


class User(AbstractBaseUser, PermissionsMixin):
    """
    Usuario autenticado por e-mail, sem username.

    Um unico modelo atende aos dois papeis. Dados academicos do aluno
    (matriculas, observacoes) ficarao em modelos proprios nas etapas
    seguintes, mantendo este modelo restrito a identidade e autenticacao.
    """

    email = models.EmailField(
        "e-mail",
        unique=True,
        max_length=254,
        error_messages={"unique": "Ja existe um usuario com este e-mail."},
    )
    full_name = models.CharField("nome completo", max_length=150)
    role = models.CharField(
        "papel",
        max_length=10,
        choices=UserRole.choices,
        default=UserRole.STUDENT,
        db_index=True,
    )

    is_active = models.BooleanField(
        "ativo",
        default=True,
        help_text="Usuario inativo nao consegue autenticar.",
    )
    is_staff = models.BooleanField(
        "acesso ao Django Admin",
        default=False,
        help_text="Permite entrar em /django-admin/. Nao confundir com o papel ADMIN.",
    )
    must_change_password = models.BooleanField(
        "precisa trocar a senha",
        default=False,
        help_text="Enquanto marcado, o usuario so acessa a tela de troca de senha.",
    )
    date_joined = models.DateTimeField("cadastrado em", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        ordering = ["full_name"]

    def __str__(self):
        return "{} <{}>".format(self.full_name, self.email)

    def save(self, *args, **kwargs):
        # Normaliza tambem quando o objeto e criado sem passar pelo manager
        # (shell, Django Admin, fixtures). Garante que a unicidade do campo
        # email seja, na pratica, insensivel a maiusculas.
        self.email = normalizar_email(self.email)
        self.full_name = (self.full_name or "").strip()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.email = normalizar_email(self.email)

    @property
    def is_admin(self):
        return self.role == UserRole.ADMIN

    @property
    def is_student(self):
        return self.role == UserRole.STUDENT

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        """Primeiro nome, usado nas saudacoes da interface."""
        return self.full_name.split(" ")[0] if self.full_name else self.email
