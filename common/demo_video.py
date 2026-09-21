"""
O cenario de demonstracao usado para gravar os videos de treinamento.

Fonte unica
-----------
Tudo que descreve a demonstracao mora aqui: os dois alunos, o modulo, a prova,
as questoes e — para cada cenario — quais alternativas o script de gravacao
deve clicar.

O motivo e o mesmo de certificates.fonts: o comando de gestao CRIA esses
registros e o script do Playwright CLICA neles. Se as duas pontas guardassem a
propria copia do texto das alternativas, bastaria alguem reescrever um
enunciado para a gravacao passar a clicar no lugar errado — e o video sairia
com a resposta trocada sem ninguem perceber.

Sobre o "gabarito" deste arquivo
--------------------------------
As respostas corretas da prova DEMO estao aqui em texto puro, e isso e seguro:
a prova DEMO nao tem conteudo do CPO. Sao quatro questoes ficticias escritas
para ensinar a MECANICA da avaliacao — onde clicar, como saber que salvou,
como finalizar. O gabarito das provas reais continua onde sempre esteve, em
QuestionOption.is_correct, lido apenas no servidor.

Identificadores reservados
--------------------------
O isolamento nao depende de flag nova no banco: depende destes identificadores
serem impossiveis de colidir com dado real.

    DEMO-VIDEO              codigo de modulo que nenhuma turma usa
    *@example.com           dominio reservado pela RFC 2606, nao entrega e-mail

Quem garante que nenhum aluno real ve o modulo e a matricula, e nao o nome:
so os dois usuarios DEMO sao matriculados em DEMO-VIDEO.
"""

from decimal import Decimal

# --- identificadores ------------------------------------------------------

CODIGO_DO_MODULO = "DEMO-VIDEO"
NOME_DO_MODULO = "Demonstração — Como realizar a avaliação"
TITULO_DA_PROVA = "Avaliação de Demonstração"

EMAIL_APROVADO = "demo.aprovado@example.com"
EMAIL_REPROVADO = "demo.reprovado@example.com"

NOME_APROVADO = "Aluno Demonstração Aprovado"
NOME_REPROVADO = "Aluno Demonstração Reprovado"

EMAILS = (EMAIL_APROVADO, EMAIL_REPROVADO)

# Lida pelo comando e pelo script de gravacao. Nunca tem valor padrao no
# codigo: uma senha default vira senha de producao no dia em que alguem
# esquece de definir a variavel.
VARIAVEL_DA_SENHA = "DEMO_VIDEO_PASSWORD"

# --- configuracao da prova ------------------------------------------------

DURACAO_EM_MINUTOS = 10
NOTA_MINIMA = Decimal("8.00")
MAXIMO_DE_TENTATIVAS = 1
PONTOS_POR_QUESTAO = Decimal("1.00")

MENSAGEM_DE_REPROVACAO = (
    "Você não atingiu a nota mínima necessária nesta avaliação.\n\n"
    "Procure a coordenação do CPO para orientações."
)

INSTRUCOES = (
    "Esta é uma avaliação de demonstração, criada apenas para treinamento.\n\n"
    "Nenhuma questão abaixo faz parte do conteúdo do CPO e o resultado não "
    "vale nota.\n\n"
    "Você terá 10 minutos para responder 4 questões objetivas. Suas respostas "
    "são salvas automaticamente: ao marcar uma alternativa, observe o aviso "
    "Salvo no alto da tela.\n\n"
    "Depois de finalizar, não será possível alterar as respostas."
)

DESCRICAO_DO_MODULO = (
    "Módulo de demonstração usado nos vídeos de treinamento. "
    "Não faz parte da grade do CPO."
)

# --- o que sai impresso no certificado DEMO -------------------------------
#
# O modulo precisa destes quatro campos preenchidos, senao a emissao e
# recusada e o video do aprovado para antes do certificado.
#
# Os textos dizem DEMONSTRACAO em letra maiuscula de proposito. Se um frame
# do video for recortado e compartilhado sozinho, o documento se identifica
# como treinamento sem depender de quem esta olhando saber disso.

NOME_NO_CERTIFICADO = "DEMONSTRAÇÃO — Treinamento do sistema"
DATAS_NO_CERTIFICADO = "material de demonstração"
LOCAL_NO_CERTIFICADO = "DEMONSTRAÇÃO"
CARGA_HORARIA = 1

# --- questoes -------------------------------------------------------------
#
# Quatro objetivas, um ponto cada. Objetivas de proposito: SHORT_TEXT e ESSAY
# exigiriam correcao manual por um administrador, e o video precisa mostrar o
# resultado saindo na hora, que e o que o aluno vai viver.
#
# O texto das alternativas e neutro ("Alternativa A — exemplo") e nao anuncia
# qual e a correta. Quem assiste aprende a mecanica sem decorar resposta, e o
# video do reprovado nao fica com o aluno clicando em algo escrito "errado".

VERDADEIRO = "Verdadeiro"
FALSO = "Falso"

QUESTOES = (
    {
        "ordem": 1,
        "tipo": "TRUE_FALSE",
        "enunciado": (
            "Este é um exemplo de questão do tipo Verdadeiro ou Falso. "
            "As respostas desta avaliação são salvas automaticamente."
        ),
        "alternativas": (
            {"texto": VERDADEIRO, "correta": True},
            {"texto": FALSO, "correta": False},
        ),
    },
    {
        "ordem": 2,
        "tipo": "SINGLE_CHOICE",
        "enunciado": "Qual opção demonstra que uma resposta foi selecionada?",
        "alternativas": (
            {"texto": "Alternativa A — exemplo", "correta": True},
            {"texto": "Alternativa B — exemplo", "correta": False},
            {"texto": "Alternativa C — exemplo", "correta": False},
            {"texto": "Alternativa D — exemplo", "correta": False},
        ),
    },
    {
        "ordem": 3,
        "tipo": "MULTIPLE_CHOICE",
        "enunciado": (
            "Selecione as duas opções indicadas como exemplo. "
            "Nesta questão é possível marcar mais de uma alternativa."
        ),
        "alternativas": (
            {"texto": "Primeira alternativa do exemplo", "correta": True},
            {"texto": "Segunda alternativa do exemplo", "correta": True},
            {"texto": "Terceira alternativa do exemplo", "correta": False},
            {"texto": "Quarta alternativa do exemplo", "correta": False},
        ),
    },
    {
        "ordem": 4,
        "tipo": "SINGLE_CHOICE",
        "enunciado": (
            "Esta é a última questão do exemplo. "
            "Depois de respondê-la, você poderá finalizar a avaliação."
        ),
        "alternativas": (
            {"texto": "Alternativa A — exemplo", "correta": True},
            {"texto": "Alternativa B — exemplo", "correta": False},
            {"texto": "Alternativa C — exemplo", "correta": False},
        ),
    },
)

TOTAL_DE_PONTOS = PONTOS_POR_QUESTAO * len(QUESTOES)


def _corretas(questao):
    return tuple(
        alt["texto"] for alt in questao["alternativas"] if alt["correta"]
    )


def _uma_errada(questao):
    """A primeira alternativa incorreta, para o cenario reprovado."""
    for alt in questao["alternativas"]:
        if not alt["correta"]:
            return (alt["texto"],)
    raise AssertionError(
        "A questao {} nao tem alternativa incorreta.".format(questao["ordem"])
    )


def roteiro_de_respostas(aprovado):
    """
    O que o script de gravacao deve clicar, questao a questao.

    Devolve uma lista de (enunciado, textos_a_marcar) na ordem das questoes.
    O script localiza a questao pelo ENUNCIADO, e nao pela posicao: a prova
    pode ter randomize_questions ligado, e nesse caso a ordem na tela nao e a
    ordem daqui.

    aprovado=True   acerta as quatro         -> 4/4 -> 10.00 -> APPROVED
    aprovado=False  acerta so as duas        -> 2/4 ->  5.00 -> FAILED
                    primeiras

    A correcao e tudo-ou-nada (exams.services.grading), entao marcar uma
    alternativa errada na multipla escolha zera a questao inteira — nao ha
    meio ponto que faca a conta do reprovado escorregar para perto da minima.
    """
    roteiro = []
    for questao in QUESTOES:
        acertar = aprovado or questao["ordem"] <= 2
        textos = _corretas(questao) if acertar else _uma_errada(questao)
        roteiro.append((questao["enunciado"], list(textos)))
    return roteiro


def pontos_esperados(aprovado):
    """Pontos brutos que o cenario deve produzir."""
    return PONTOS_POR_QUESTAO * (len(QUESTOES) if aprovado else 2)


def nota_esperada(aprovado):
    """Nota normalizada 0-10 que o cenario deve produzir."""
    return (pontos_esperados(aprovado) / TOTAL_DE_PONTOS) * Decimal("10")
