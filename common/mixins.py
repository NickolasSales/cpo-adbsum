"""
Autorizacao por papel, reutilizavel.

Este modulo e o unico lugar do projeto autorizado a comparar user.role. Toda
view protegida herda de um mixin daqui ou usa um dos decorators, de forma que
uma eventual mudanca na regra de papeis acontece em um arquivo so.

Comportamento em ambos os casos:

    usuario anonimo   -> redirecionado para o login
    papel incorreto   -> HTTP 403

A distincao e proposital. Redirecionar um usuario ja autenticado para o login
confundiria a interface e daria a impressao de sessao expirada; e devolver 403
para um anonimo esconderia dele a existencia da tela de login.
"""

from functools import wraps

from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from accounts.models import UserRole

MENSAGEM_SEM_PERMISSAO = "Voce nao tem permissao para acessar esta area."


class RoleRequiredMixin(UserPassesTestMixin):
    """
    Exige um papel especifico. Nao usar diretamente.

    UserPassesTestMixin devolve o comportamento desejado sem configuracao
    extra: quando test_func e falso, o AccessMixin levanta PermissionDenied
    se o usuario estiver autenticado e redireciona para o login caso
    contrario. E exatamente a politica descrita no topo do modulo.
    """

    required_role = None
    permission_denied_message = MENSAGEM_SEM_PERMISSAO

    def test_func(self):
        if self.required_role is None:
            raise ValueError(
                "required_role precisa ser definido na view {}.".format(
                    self.__class__.__name__
                )
            )
        user = self.request.user
        # is_active e redundante hoje: o ModelBackend.get_user() do Django ja
        # devolve None para usuario inativo, de modo que uma sessao aberta
        # passa a resolver como anonima assim que o aluno e bloqueado. Fica
        # como defesa em profundidade, caso um backend de autenticacao
        # customizado entre no projeto e nao repita essa checagem.
        return (
            user.is_authenticated
            and user.is_active
            and user.role == self.required_role
        )


class AdminRequiredMixin(RoleRequiredMixin):
    """Restringe a view a usuarios com papel ADMIN."""

    required_role = UserRole.ADMIN


class StudentRequiredMixin(RoleRequiredMixin):
    """Restringe a view a usuarios com papel STUDENT."""

    required_role = UserRole.STUDENT


def role_required(role):
    """Equivalente aos mixins, para views baseadas em funcao."""

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not user.is_active or user.role != role:
                raise PermissionDenied(MENSAGEM_SEM_PERMISSAO)
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


admin_required = role_required(UserRole.ADMIN)
student_required = role_required(UserRole.STUDENT)
