"""
Servicos de dominio de provas.

Dividido em tres modulos por tamanho, nao por estetica: validacao estrutural,
ciclo de vida da prova e manutencao de questoes somariam bem mais de
setecentas linhas num arquivo unico.

Este pacote reexporta tudo, de modo que quem chama continua escrevendo
`from exams import services` e `services.publish_exam(...)`, igual ao padrao
das outras apps.
"""

from exams.services.exam import (  # noqa: F401
    close_exam,
    create_exam,
    duplicate_exam,
    publish_exam,
    remove_exam_password,
    set_exam_password,
    update_exam,
)
from exams.services.question import (  # noqa: F401
    create_question,
    delete_question,
    reorder_questions,
    update_question,
)
from exams.services.validation import (  # noqa: F401
    erros_da_questao,
    erros_para_publicacao,
    exigir_estrutura_editavel,
)

__all__ = [
    "close_exam",
    "create_exam",
    "duplicate_exam",
    "publish_exam",
    "remove_exam_password",
    "set_exam_password",
    "update_exam",
    "create_question",
    "delete_question",
    "reorder_questions",
    "update_question",
    "erros_da_questao",
    "erros_para_publicacao",
    "exigir_estrutura_editavel",
]
