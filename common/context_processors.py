"""Contexto disponivel em todos os templates."""

from django.conf import settings


def institution(request):
    """Expoe a identificacao institucional para o layout."""
    return {
        "INSTITUTION_NAME": settings.INSTITUTION_NAME,
        "SITE_URL": settings.SITE_URL,
    }
