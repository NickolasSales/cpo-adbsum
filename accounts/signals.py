"""
Auditoria de autenticacao via signals.

Usar os signals do Django, em vez de gravar dentro da view de login, garante
que qualquer caminho de autenticacao seja auditado: a tela propria, o
/django-admin/ e qualquer comando de gerenciamento que autentique.
"""

from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from audit.models import AuditEvent
from audit.services import record


@receiver(user_logged_in)
def registrar_login_com_sucesso(sender, request, user, **kwargs):
    record(
        AuditEvent.LOGIN_SUCCESS,
        request=request,
        actor=user,
        student=user if getattr(user, "is_student", False) else None,
        entity_type="User",
        entity_id=user.pk,
        metadata={"email": user.email, "role": user.role},
    )


@receiver(user_login_failed)
def registrar_falha_de_login(sender, credentials, request=None, **kwargs):
    """
    Registra a tentativa frustrada.

    Apenas o e-mail tentado e gravado. O dicionario de credenciais inteiro
    nunca e persistido, e a senha nunca chega ao banco nem ao log: o servico
    de auditoria ainda sanitiza a metadata como segunda barreira.
    """
    email_tentado = (credentials or {}).get("username") or ""

    record(
        AuditEvent.LOGIN_FAILED,
        request=request,
        metadata={"email": str(email_tentado)[:254]},
    )
