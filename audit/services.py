"""
Servico de registro de auditoria.

Toda escrita na trilha passa por aqui. Concentrar a gravacao em um unico
ponto permite garantir, por construcao, que nenhum segredo seja persistido:
a sanitizacao da metadata acontece neste modulo e nao depende da disciplina
de quem chama.
"""

import logging

from common.http import get_client_ip, get_user_agent

logger = logging.getLogger("cpo.audit")

# Chaves cujo valor jamais pode ser gravado. A comparacao e por substring em
# minusculas, entao "password", "new_password1" e "USER_PASSWORD" sao todas
# capturadas pelo mesmo termo.
CHAVES_PROIBIDAS = (
    "password",
    "senha",
    "passwd",
    "token",
    "secret",
    "authorization",
    "csrf",
    "session",
    "cookie",
    "api_key",
    "apikey",
    "credential",
)

VALOR_OCULTADO = "[REMOVIDO]"


def sanitizar_metadata(metadata):
    """
    Remove de forma recursiva qualquer chave sensivel da metadata.

    O valor nao e substituido por uma versao mascarada do original: e
    descartado e trocado por um marcador. Mascarar preservaria o comprimento
    da senha, que ja e informacao demais.
    """
    if metadata is None:
        return {}

    if isinstance(metadata, dict):
        limpo = {}
        for chave, valor in metadata.items():
            texto_chave = str(chave).lower()
            if any(proibida in texto_chave for proibida in CHAVES_PROIBIDAS):
                limpo[chave] = VALOR_OCULTADO
            else:
                limpo[chave] = sanitizar_metadata(valor)
        return limpo

    if isinstance(metadata, (list, tuple)):
        return [sanitizar_metadata(item) for item in metadata]

    return metadata


def record(
    event,
    *,
    request=None,
    actor=None,
    student=None,
    entity_type="",
    entity_id="",
    metadata=None,
):
    """
    Grava um evento na trilha de auditoria.

    Nunca levanta excecao para quem chama: uma falha ao auditar nao pode
    derrubar a operacao de negocio que estava sendo executada. A falha e
    registrada no log da aplicacao para investigacao.
    """
    from audit.models import AuditLog

    try:
        return AuditLog.objects.create(
            event=event,
            actor=actor if getattr(actor, "pk", None) else None,
            student=student if getattr(student, "pk", None) else None,
            entity_type=entity_type or "",
            entity_id=str(entity_id) if entity_id else "",
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
            metadata=sanitizar_metadata(metadata),
        )
    except Exception:
        logger.exception("Falha ao gravar registro de auditoria (evento=%s)", event)
        return None
