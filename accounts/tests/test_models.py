"""Testes do modelo de usuario."""

import pytest
from django.db import IntegrityError

from accounts.models import User, UserRole

pytestmark = pytest.mark.django_db


def test_cria_aluno_com_papel_padrao():
    user = User.objects.create_user(
        email="novo@exemplo.test", full_name="Novo Aluno", password="Alguma#Senha2026"
    )
    assert user.role == UserRole.STUDENT
    assert user.is_active is True
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.is_student is True
    assert user.is_admin is False


def test_cria_administrador():
    user = User.objects.create_user(
        email="admin@exemplo.test",
        full_name="Admin Teste",
        password="Alguma#Senha2026",
        role=UserRole.ADMIN,
    )
    assert user.is_admin is True
    assert user.is_student is False


def test_createsuperuser_produz_admin_completo():
    user = User.objects.create_superuser(
        email="super@exemplo.test",
        full_name="Super Usuario",
        password="Alguma#Senha2026",
    )
    assert user.role == UserRole.ADMIN
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.must_change_password is False
    assert user.is_active is True


def test_email_e_obrigatorio():
    with pytest.raises(ValueError):
        User.objects.create_user(
            email="", full_name="Sem Email", password="Alguma#Senha2026"
        )


def test_nome_completo_e_obrigatorio():
    with pytest.raises(ValueError):
        User.objects.create_user(
            email="semnome@exemplo.test", full_name="", password="Alguma#Senha2026"
        )


def test_email_e_unico():
    User.objects.create_user(
        email="repetido@exemplo.test", full_name="Primeiro", password="Alguma#Senha2026"
    )
    with pytest.raises(IntegrityError):
        User.objects.create_user(
            email="repetido@exemplo.test",
            full_name="Segundo",
            password="Alguma#Senha2026",
        )


def test_email_e_normalizado_para_minusculas():
    user = User.objects.create_user(
        email="Aluno@Email.Com", full_name="Case Teste", password="Alguma#Senha2026"
    )
    assert user.email == "aluno@email.com"


def test_email_com_case_diferente_nao_cria_segunda_conta():
    """Aluno@Email.com e aluno@email.com precisam ser a mesma conta."""
    User.objects.create_user(
        email="aluno@email.com", full_name="Primeiro", password="Alguma#Senha2026"
    )
    with pytest.raises(IntegrityError):
        User.objects.create_user(
            email="ALUNO@EMAIL.COM", full_name="Segundo", password="Alguma#Senha2026"
        )


def test_normalizacao_tambem_ocorre_fora_do_manager():
    """Criacao direta pelo modelo tambem precisa normalizar o e-mail."""
    user = User(email="  Direto@Exemplo.TEST  ", full_name="Direto")
    user.set_password("Alguma#Senha2026")
    user.save()
    user.refresh_from_db()
    assert user.email == "direto@exemplo.test"


def test_busca_por_chave_natural_ignora_maiusculas():
    original = User.objects.create_user(
        email="busca@exemplo.test", full_name="Busca", password="Alguma#Senha2026"
    )
    encontrado = User.objects.get_by_natural_key("BUSCA@Exemplo.TEST")
    assert encontrado.pk == original.pk


def test_senha_e_armazenada_com_hash():
    senha = "Alguma#Senha2026"
    user = User.objects.create_user(
        email="hash@exemplo.test", full_name="Hash Teste", password=senha
    )
    assert user.password != senha
    assert senha not in user.password
    # O Django prefixa o hash com o identificador do algoritmo.
    assert user.password.startswith("pbkdf2_")
    assert user.check_password(senha) is True
    assert user.check_password("outra-senha") is False


def test_superusuario_com_papel_diferente_de_admin_e_recusado():
    with pytest.raises(ValueError):
        User.objects.create_superuser(
            email="ruim@exemplo.test",
            full_name="Papel Errado",
            password="Alguma#Senha2026",
            role=UserRole.STUDENT,
        )


def test_username_field_e_email_e_nao_ha_username():
    assert User.USERNAME_FIELD == "email"
    assert "full_name" in User.REQUIRED_FIELDS
    campos = {campo.name for campo in User._meta.get_fields()}
    assert "username" not in campos
