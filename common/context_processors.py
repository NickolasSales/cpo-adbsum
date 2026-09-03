"""Contexto disponivel em todos os templates."""

from django.conf import settings


def institution(request):
    """
    Expoe a identidade da aplicacao para o layout.

    Um unico ponto de leitura. A identidade anterior estava escrita a mao em
    dezenas de templates, no formato "{{ INSTITUTION_NAME }} Provas" — trocar
    o nome exigia editar arquivo por arquivo, e bastava esquecer um para a
    aplicacao ficar com dois nomes ao mesmo tempo.

    APP_NAME e o que aparece na interface. INSTITUTION_NAME e o que vai
    impresso no certificado. Coincidem hoje, mas sao perguntas diferentes.

    APP_LOGO e o caminho DENTRO do static, e nao uma URL pronta. Quem resolve
    e o {% static %} do template, porque e ele que conhece o hash que o
    collectstatic poe no nome do arquivo. Montar a URL aqui congelaria o nome
    sem hash e serviria a logo antiga depois de uma troca.
    """
    return {
        "APP_NAME": settings.APP_NAME,
        "APP_SUBTITLE": settings.APP_SUBTITLE,
        "INSTITUTION_NAME": settings.INSTITUTION_NAME,
        "APP_LOGO": settings.APP_LOGO,
        "APP_LOGO_ALT": settings.APP_LOGO_ALT,
        "SITE_URL": settings.SITE_URL,
    }
