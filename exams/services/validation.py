"""
Validacao estrutural de provas e questoes.

Funcoes puras: recebem objetos, devolvem lista de problemas em portugues e
nao escrevem nada. Ficam separadas do resto dos servicos porque sao usadas em
tres momentos diferentes — ao salvar uma questao, ao mostrar o estado da
prova na tela de detalhe e ao publicar — e em nenhum deles pode haver
divergencia sobre o que conta como valido.

Lista vazia significa estrutura valida. A escolha por lista, em vez de
levantar excecao no primeiro erro, e deliberada: o administrador precisa ver
de uma vez tudo que falta, e nao descobrir um problema por tentativa de
publicacao.
"""

from decimal import Decimal

from common.exceptions import DomainError
from exams.models import (
    ExamStatus,
    QuestionType,
    TEXTO_FALSO,
    TEXTO_VERDADEIRO,
    TIPOS_COM_ALTERNATIVAS,
    NOTA_MAXIMA,
)


def exigir_estrutura_editavel(exam, acao="alterar a estrutura"):
    """
    Barra qualquer mudanca estrutural fora de rascunho.

    Chamada no inicio de todo servico que mexe em questao, alternativa,
    gabarito, pontuacao ou modulo. Concentrar a checagem aqui e o que torna a
    regra verificavel: nao ha caminho de escrita que dependa de o template
    ter escondido um botao.
    """
    if exam.status == ExamStatus.DRAFT:
        return exam

    if exam.status == ExamStatus.PUBLISHED:
        raise DomainError(
            "Nao e possivel {} de uma prova publicada. A estrutura fica "
            "congelada na publicacao. Para mudar, duplique a prova e edite a "
            "nova versao.".format(acao)
        )

    raise DomainError(
        "Nao e possivel {} de uma prova fechada. Uma prova fechada e somente "
        "leitura; duplique-a para criar uma versao nova.".format(acao)
    )


def _rotulo(question):
    """Como a questao e citada nas mensagens de erro."""
    return "Questao {}".format(question.order if question.order else question.pk or "nova")


def erros_da_questao(question, opcoes=None):
    """
    Problemas estruturais de uma questao. Lista vazia significa valida.

    O parametro opcoes existe para evitar N+1: quem ja carregou as
    alternativas por prefetch passa a lista pronta, em vez de provocar uma
    consulta por questao.
    """
    erros = []
    rotulo = _rotulo(question)

    if not (question.text or "").strip():
        erros.append("{}: o enunciado esta vazio.".format(rotulo))

    if question.points is None or Decimal(question.points) <= 0:
        erros.append("{}: o valor precisa ser maior que zero.".format(rotulo))

    if opcoes is None:
        opcoes = list(question.options.all())
    else:
        opcoes = list(opcoes)

    corretas = [opcao for opcao in opcoes if opcao.is_correct]

    if question.type == QuestionType.SINGLE_CHOICE:
        if len(opcoes) < 2:
            erros.append(
                "{}: escolha unica precisa de pelo menos 2 alternativas.".format(rotulo)
            )
        if len(corretas) == 0:
            erros.append("{}: nenhuma alternativa foi marcada como correta.".format(rotulo))
        elif len(corretas) > 1:
            erros.append(
                "{}: e de escolha unica, mas possui {} alternativas corretas.".format(
                    rotulo, len(corretas)
                )
            )

    elif question.type == QuestionType.MULTIPLE_CHOICE:
        if len(opcoes) < 2:
            erros.append(
                "{}: multiplas respostas precisa de pelo menos 2 alternativas.".format(
                    rotulo
                )
            )
        if len(corretas) == 0:
            erros.append("{}: nenhuma alternativa foi marcada como correta.".format(rotulo))
        elif opcoes and len(corretas) == len(opcoes):
            # Uma questao em que tudo esta certo nao mede nada e, na correcao,
            # transforma qualquer marcacao em acerto parcial.
            erros.append(
                "{}: todas as alternativas estao corretas. Deixe ao menos uma "
                "incorreta.".format(rotulo)
            )

    elif question.type == QuestionType.TRUE_FALSE:
        if len(opcoes) != 2:
            erros.append(
                "{}: verdadeiro ou falso precisa de exatamente 2 alternativas.".format(
                    rotulo
                )
            )
        else:
            textos = {(opcao.text or "").strip() for opcao in opcoes}
            if textos != {TEXTO_VERDADEIRO, TEXTO_FALSO}:
                erros.append(
                    '{}: as alternativas precisam ser "{}" e "{}".'.format(
                        rotulo, TEXTO_VERDADEIRO, TEXTO_FALSO
                    )
                )
        if len(corretas) != 1:
            erros.append(
                "{}: marque exatamente uma alternativa como correta.".format(rotulo)
            )

    elif question.type not in TIPOS_COM_ALTERNATIVAS:
        # SHORT_TEXT e ESSAY. Correcao manual nesta versao, entao alternativa
        # cadastrada aqui e sinal de estrutura inconsistente e nao de
        # gabarito automatico.
        if opcoes:
            erros.append(
                "{}: e de correcao manual e nao pode ter alternativas "
                "cadastradas ({} encontrada(s)).".format(rotulo, len(opcoes))
            )

    return erros


def erros_para_publicacao(exam):
    """
    Tudo que impede a publicacao. Lista vazia significa que pode publicar.

    Carrega questoes e alternativas por prefetch: sem isso, uma prova de
    trinta questoes com cinco alternativas cada faria mais de trinta
    consultas so para validar.
    """
    erros = []

    if not exam.module_id:
        erros.append("A prova precisa estar vinculada a um modulo.")
    elif not exam.module.is_active:
        erros.append(
            "O modulo {} esta inativo. Ative o modulo antes de publicar.".format(
                exam.module.code
            )
        )

    if not (exam.title or "").strip():
        erros.append("O titulo da prova e obrigatorio.")

    if exam.open_at is None:
        erros.append("A data de abertura e obrigatoria.")
    if exam.close_at is None:
        erros.append("A data de encerramento e obrigatoria.")
    if exam.open_at and exam.close_at and exam.open_at >= exam.close_at:
        erros.append("A data de encerramento deve ser posterior a abertura.")

    if exam.duration_minutes is None:
        erros.append("A duracao em minutos e obrigatoria.")
    elif exam.duration_minutes <= 0:
        erros.append("A duracao precisa ser maior que zero minutos.")

    if exam.passing_score is None:
        erros.append("A nota minima e obrigatoria.")
    elif not (Decimal("0") <= Decimal(exam.passing_score) <= NOTA_MAXIMA):
        erros.append("A nota minima precisa estar entre 0 e 10.")

    if exam.max_attempts is None or exam.max_attempts < 1:
        erros.append("A prova precisa permitir ao menos uma tentativa.")

    questoes = list(
        exam.questions.filter(active=True).prefetch_related("options").order_by("order", "id")
    )

    if not questoes:
        erros.append("A prova nao possui nenhuma questao ativa.")
    else:
        total = sum((Decimal(q.points) for q in questoes), Decimal("0.00"))
        if total <= 0:
            erros.append("A soma dos pontos das questoes precisa ser maior que zero.")

        for questao in questoes:
            erros.extend(erros_da_questao(questao, opcoes=list(questao.options.all())))

    return erros
