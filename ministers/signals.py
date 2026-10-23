"""
A sessao do portal e a do CPO nao convivem no mesmo navegador.

O login do ministro ja encerra a sessao do CPO (ministers.auth.entrar). Este
receiver cobre o caminho inverso: quando alguem entra no CPO — pela tela de
login, pelo /django-admin/ ou por qualquer outro caminho que dispare
user_logged_in —, a identidade de ministro que estivesse na sessao e
descartada.

Sem isto, o login do Django rotaciona a chave mas PRESERVA os dados da
sessao anonima anterior, e um aluno que entrasse num computador onde um
ministro esqueceu o portal aberto herdaria o acesso dele.
"""

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from ministers.auth import esquecer_ministro


@receiver(user_logged_in)
def encerrar_sessao_do_portal(sender, request, user, **kwargs):
    if request is not None and hasattr(request, "session"):
        esquecer_ministro(request.session)
        if hasattr(request, "_minister_account"):
            request._minister_account = None
