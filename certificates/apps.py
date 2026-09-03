from django.apps import AppConfig
from django.core.checks import Error, register


class CertificatesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "certificates"
    verbose_name = "certificados"

    def ready(self):
        register(verificar_fontes, "certificates")


def verificar_fontes(app_configs, **kwargs):
    """
    As fontes do certificado estao no disco?

    Roda no `manage.py check`, que ja faz parte do deploy — entao um arquivo
    que nao subiu aparece ANTES do restart, e nao no meio da primeira
    emissao. Uma fonte ausente nao e um aviso: sem ela o certificado sairia
    com outra tipografia, ou nao sairia.

    A mensagem nomeia a familia e o arquivo, e nao o caminho absoluto: quem
    le a saida de um deploy nao precisa da arvore de diretorios do servidor.
    """
    from certificates.fonts import RAIZ_DAS_FONTES, arquivos_ausentes, rotulo

    faltando = arquivos_ausentes()
    if not faltando:
        return []

    linhas = ", ".join(
        "{} {}{}".format(rotulo(familia), peso, " italico" if italico else "")
        for familia, peso, italico, _nome in faltando
    )
    return [
        Error(
            "Arquivos de fonte ausentes em static/{}: {}.".format(
                RAIZ_DAS_FONTES, linhas
            ),
            hint=(
                "Os arquivos sao versionados junto com o codigo. Confira se o "
                "git pull trouxe static/{} inteiro."
            ).format(RAIZ_DAS_FONTES),
            id="certificates.E001",
        )
    ]
