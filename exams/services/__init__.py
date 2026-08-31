"""
Servicos de dominio de provas.

Dividido por assunto, e nao por estetica: validacao estrutural, ciclo de vida
da prova, manutencao de questoes e realizacao pelo aluno somariam mais de mil
e quinhentas linhas num arquivo unico.

Este pacote reexporta tudo, de modo que quem chama continua escrevendo
`from exams import services` e `services.publish_exam(...)`, igual ao padrao
das outras apps.
"""

from exams.services.attempt import (  # noqa: F401
    ObrigatoriasPendentes,
    SemAcessoAProva,
    TentativaNaoEditavel,
    TokenInvalido,
    autosave_answer,
    expirar_tentativas_vencidas,
    expire_attempt,
    matricula_liberada,
    prova_visivel_ou_none,
    questoes_obrigatorias_sem_resposta,
    start_attempt,
    submit_attempt,
    tentativa_do_aluno_ou_none,
    tentativas_do_aluno,
)
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
    "ObrigatoriasPendentes",
    "SemAcessoAProva",
    "TentativaNaoEditavel",
    "TokenInvalido",
    "autosave_answer",
    "expirar_tentativas_vencidas",
    "expire_attempt",
    "matricula_liberada",
    "prova_visivel_ou_none",
    "questoes_obrigatorias_sem_resposta",
    "start_attempt",
    "submit_attempt",
    "tentativa_do_aluno_ou_none",
    "tentativas_do_aluno",
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
