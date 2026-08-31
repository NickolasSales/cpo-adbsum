"""Middleware que impoe a troca da senha inicial."""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class MustChangePasswordMiddleware:
    """
    Bloqueia o usuario com must_change_password ate que ele troque a senha.

    Foi implementado como middleware, e nao como decorator de view, de
    proposito: um decorator precisa ser lembrado em cada view nova, e uma
    view esquecida vira um furo na regra. O middleware cobre por construcao
    toda rota presente e futura.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._rotas_liberadas = None

    def rotas_liberadas(self):
        """
        URLs acessiveis mesmo com a troca de senha pendente.

        Resolvido com preguica, e nao no __init__, porque a URLconf ainda nao
        esta carregada quando o middleware e instanciado.
        """
        if self._rotas_liberadas is None:
            self._rotas_liberadas = {
                reverse("accounts:change_password"),
                reverse("accounts:logout"),
                reverse("common:health"),
            }
        return self._rotas_liberadas

    def __call__(self, request):
        user = getattr(request, "user", None)

        # A partir da Etapa 5 o aluno nao troca a propria senha: quem a define
        # e o administrador. A flag continua no modelo, mas deixa de comandar
        # o fluxo do STUDENT — se continuasse, um aluno antigo criado com
        # must_change_password=True ficaria preso num redirect infinito para
        # uma tela que a TrocarSenhaView agora recusa com 403.
        #
        # A flag segue valendo para ADMIN, que continua trocando a propria
        # senha normalmente.
        pendente = (
            user is not None
            and user.is_authenticated
            and user.must_change_password
            and not user.is_student
        )

        if pendente:
            caminho = request.path_info

            liberado = (
                caminho in self.rotas_liberadas()
                or caminho.startswith(settings.STATIC_URL)
            )

            if not liberado:
                return redirect("accounts:change_password")

        return self.get_response(request)
