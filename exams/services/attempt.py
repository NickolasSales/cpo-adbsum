"""
Realizacao da prova pelo aluno.

Tudo que decide alguma coisa sobre uma tentativa esta neste modulo. As views
so validam a forma da requisicao, chamam uma funcao daqui e apresentam o
resultado; nenhuma regra vive em template, formulario ou JavaScript.

O principio que organiza o arquivo
----------------------------------
O navegador e ambiente hostil. Nada que venha dele e fonte da verdade:

    quem e o aluno        vem da sessao, nunca do corpo do POST
    qual e a tentativa    vem do public_id da URL, cruzado com o aluno
    qual questao          vem do token, resolvido dentro da tentativa
    que horas sao         vem de timezone.now(), nunca do relogio do cliente
    quanto tempo resta    vem de expires_at, gravado no start

O cronometro da tela e enfeite. Quem decide se ainda da tempo de salvar e o
servidor, em cada request, comparando com o prazo que ele mesmo gravou.

Travas
------
Duas operacoes concorrentes precisam de serializacao real:

    start     dois cliques, dois aparelhos, duas abas — nunca duas tentativas.
              Trava o User do aluno, e a constraint parcial
              uniq_tentativa_em_andamento e a rede embaixo disso.

    autosave  um autosave em voo enquanto o aluno clica em finalizar.
    x submit   Ambos travam a propria ExamAttempt, entao um dos dois chega
              primeiro e o outro ve o estado ja mudado. Nenhuma resposta e
              gravada depois de submitted_at.
"""

import secrets
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError
from common.http import get_client_ip, get_user_agent
from courses.models import Enrollment
from exams.models import (
    ESTADOS_CORRIGIVEIS,
    LIMITE_ESSAY,
    LIMITE_SHORT_TEXT,
    Answer,
    AnswerOption,
    AttemptOption,
    AttemptQuestion,
    AttemptStatus,
    ExamAttempt,
    ExamStatus,
    Question,
    QuestionType,
    TIPOS_COM_ALTERNATIVAS,
)

MENSAGEM_SENHA_INVALIDA = "Senha da prova invalida."

# Quanto texto cada tipo aceita. O limite e do servidor; o maxlength do HTML
# e conforto de digitacao e nao vale como validacao.
LIMITES_DE_TEXTO = {
    QuestionType.SHORT_TEXT: LIMITE_SHORT_TEXT,
    QuestionType.ESSAY: LIMITE_ESSAY,
}


# ---------------------------------------------------------------------------
# Erros de dominio especificos
# ---------------------------------------------------------------------------


class SemAcessoAProva(DomainError):
    """
    O aluno nao pode nem saber que esta prova existe.

    Separado dos demais porque a view responde 404, e nao uma mensagem: sem
    matricula liberada, dizer "voce nao tem permissao" ja confirmaria que a
    prova existe e em qual modulo esta.
    """


class TentativaNaoEditavel(DomainError):
    """
    A tentativa existe, mas nao aceita mais escrita.

    Carrega o status resultante para que a resposta HTTP possa dizer ao
    navegador o que aconteceu — expirou ou foi enviada — sem que a view
    precise consultar o banco de novo.
    """

    def __init__(self, mensagem, *, status):
        super().__init__(mensagem)
        self.status_da_tentativa = status


class TokenInvalido(DomainError):
    """
    Token que nao pertence a esta tentativa, a esta questao, ou nao existe.

    Os tres casos devolvem a mesma coisa de proposito. Distinguir "nao existe"
    de "e de outra tentativa" transformaria o endpoint num oraculo para
    descobrir tokens validos.
    """


class ObrigatoriasPendentes(DomainError):
    """Envio voluntario com questao obrigatoria em branco."""

    def __init__(self, numeros):
        self.numeros = list(numeros)
        super().__init__(
            ["Questao {} ainda nao foi respondida.".format(n) for n in self.numeros]
        )


# ---------------------------------------------------------------------------
# Leitura com controle de acesso
# ---------------------------------------------------------------------------


def matricula_liberada(student, exam):
    """Matricula que da acesso a prova, ou None."""
    return (
        Enrollment.objects.liberadas()
        .filter(student=student, module_id=exam.module_id)
        .first()
    )


def prova_visivel_ou_none(student, exam_id):
    """
    Prova publicada que este aluno pode ver, ou None.

    Une as duas condicoes numa consulta so: a prova precisa estar publicada e
    o aluno precisa ter matricula liberada no modulo dela. A view devolve 404
    quando isto retorna None — nao 403 — porque um 403 confirmaria que a
    prova existe.

    Uma prova fechada continua visivel: o aluno que ja fez precisa poder abrir
    a tela e ver a situacao da tentativa dele. O que a prova fechada nao
    permite e iniciar, e esse portao esta em start_attempt.

    Uma prova ARQUIVADA, ao contrario da fechada, some por completo. Arquivar
    e dizer "isto saiu da operacao"; deixar a prova ainda abrivel pela URL
    contradiria a unica coisa que o arquivamento promete.
    """
    from exams.models import Exam

    return (
        Exam.objects.select_related("module")
        .filter(
            pk=exam_id,
            status__in=(ExamStatus.PUBLISHED, ExamStatus.CLOSED),
            is_archived=False,
            module__enrollments__student=student,
            module__enrollments__status="ACTIVE",
            module__enrollments__access_enabled=True,
            module__is_active=True,
        )
        .first()
    )


def tentativa_do_aluno_ou_none(student, public_id):
    """
    Tentativa deste aluno, pelo identificador publico.

    As duas condicoes andam juntas por construcao — public_id e dono na mesma
    consulta —, entao nao existe caminho em que alguem esqueca de conferir o
    dono depois de achar a tentativa.

    A matricula tambem e reconferida: se o acesso do aluno for bloqueado no
    meio da prova, o proximo request dele ja nao encontra a tentativa. O
    bloqueio administrativo tem efeito imediato, sem precisar derrubar sessao.
    """
    try:
        uuid.UUID(str(public_id))
    except (ValueError, AttributeError, TypeError):
        return None

    return (
        ExamAttempt.objects.select_related("exam", "exam__module")
        .filter(
            public_id=public_id,
            student=student,
            exam__module__enrollments__student=student,
            exam__module__enrollments__status="ACTIVE",
            exam__module__enrollments__access_enabled=True,
            exam__module__is_active=True,
        )
        .first()
    )


def tentativas_do_aluno(student, exam):
    """Todas as tentativas do aluno naquela prova, da mais recente primeiro."""
    return ExamAttempt.objects.filter(student=student, exam=exam).order_by(
        "-attempt_number"
    )


# ---------------------------------------------------------------------------
# Inicio da tentativa
# ---------------------------------------------------------------------------


def start_attempt(student, exam, *, supplied_password=None, request=None):
    """
    Inicia uma tentativa, ou devolve a que ja estava aberta.

    Ordem dos portoes, e por que ela importa:

        1  matricula liberada          -> 404, nem confirma que a prova existe
        2  prova publicada             -> fechada ou rascunho nao inicia
        3  janela aberta               -> open_at <= agora < close_at
        4  tentativa aberta            -> retoma, sem senha e sem evento novo
        5  limite de tentativas
        6  senha da prova

    A retomada vem antes do limite e da senha de proposito. Quem ja esta com
    a prova aberta nao esta comecando nada: cobrar senha de novo ou barrar por
    limite deixaria o aluno trancado do lado de fora da propria tentativa se
    ele apenas fechasse a aba sem querer.

    Concorrencia: a linha do aluno e travada antes de qualquer contagem, entao
    dois cliques simultaneos entram em fila e o segundo enxerga a tentativa
    que o primeiro acabou de criar.
    """
    from exams.models import Exam

    agora = timezone.now()

    # Antes de tudo, e em transacao propria: se havia uma tentativa aberta com
    # o prazo ja vencido, ela e encerrada agora.
    #
    # Fora da transacao do start de proposito. O prazo venceu de fato, e esse
    # fato nao pode depender do desfecho do start — se o start for recusado
    # logo abaixo por limite de tentativas, o rollback desfaria a expiracao e
    # a tentativa ficaria presa em IN_PROGRESS, bloqueada pela constraint
    # parcial ate o comando de gestao passar.
    _expirar_tentativa_aberta_vencida(student, exam, agora, request=request)

    with transaction.atomic():
        # A trava e sobre o aluno, e nao sobre a prova: e o aluno que nao pode
        # ter duas tentativas, e travar a prova poria em fila a turma inteira.
        get_user_model().objects.select_for_update().get(pk=student.pk)

        if matricula_liberada(student, exam) is None:
            raise SemAcessoAProva("Prova indisponivel.")

        # Arquivamento vem antes do status porque e independente dele: uma
        # prova arquivada continua PUBLISHED, e sem este portao ela passaria
        # direto pelas duas linhas de baixo e aceitaria tentativa nova.
        #
        # Relido do banco, e nao do objeto recebido: entre a montagem da tela
        # e este POST alguem pode ter arquivado a prova, e a instancia que a
        # view carregou ainda diria is_archived=False.
        if Exam.objects.filter(pk=exam.pk, is_archived=True).exists():
            raise DomainError(
                "Esta prova foi arquivada e nao aceita novas tentativas."
            )

        if exam.status == ExamStatus.CLOSED:
            raise DomainError("Esta prova foi encerrada e nao aceita novas tentativas.")
        if exam.status != ExamStatus.PUBLISHED:
            raise DomainError("Esta prova ainda nao esta disponivel.")

        if exam.open_at is None or agora < exam.open_at:
            raise DomainError("Esta prova ainda nao abriu.")
        # Comparacao estrita: exatamente em close_at a janela ja fechou. Uma
        # prova que abre as 19h e fecha as 21h vale [19h, 21h).
        if exam.close_at is None or agora >= exam.close_at:
            raise DomainError("O periodo desta prova foi encerrado.")

        aberta = ExamAttempt.objects.filter(
            student=student, exam=exam, status=AttemptStatus.IN_PROGRESS
        ).first()
        if aberta is not None:
            if not aberta.prazo_vencido(agora):
                return aberta
            # Chegar aqui e raro: a expiracao preventiva acima ja teria
            # fechado esta tentativa. Sobra o caso em que o prazo venceu entre
            # as duas etapas, e ai vale a mesma regra.
            _expirar(aberta, agora=agora, request=request)

        usadas = (
            ExamAttempt.objects.filter(student=student, exam=exam)
            .que_contam_para_o_limite()
            .count()
        )
        if usadas >= exam.max_attempts:
            raise DomainError(
                "Voce ja utilizou todas as tentativas permitidas para esta prova."
            )

        if exam.tem_senha:
            # A senha fornecida nao e gravada, logada nem auditada em lugar
            # nenhum: existe apenas como argumento desta comparacao.
            if not supplied_password or not check_password(
                supplied_password, exam.access_password_hash
            ):
                raise DomainError(MENSAGEM_SENHA_INVALIDA)

        maior = ExamAttempt.objects.filter(student=student, exam=exam).aggregate(
            maior=Max("attempt_number")
        )["maior"]
        # Conta sobre todas as tentativas, inclusive as anuladas: o numero
        # identifica a tentativa no historico e nunca e reaproveitado.
        numero = (maior or 0) + 1

        tentativa = ExamAttempt.objects.create(
            student=student,
            exam=exam,
            attempt_number=numero,
            status=AttemptStatus.IN_PROGRESS,
            started_at=agora,
            expires_at=_calcular_prazo(exam, agora),
            total_points_snapshot=exam.total_points,
            passing_score_snapshot=exam.passing_score,
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
        )

        total_questoes, total_opcoes = _montar_tentativa(tentativa, exam)

        record(
            AuditEvent.ATTEMPT_STARTED,
            request=request,
            actor=student,
            student=student,
            entity_type="ExamAttempt",
            entity_id=tentativa.pk,
            metadata={
                "exam_id": exam.pk,
                "exam_version": exam.version,
                "attempt_number": numero,
                "expires_at": tentativa.expires_at.isoformat(),
                "questions": total_questoes,
                "options": total_opcoes,
            },
        )

    return tentativa


def _expirar_tentativa_aberta_vencida(student, exam, agora, *, request=None):
    """
    Encerra, em transacao propria, a tentativa aberta deste aluno cujo prazo
    ja passou.

    Chamada antes do start justamente para nao participar da transacao dele. A
    passagem do tempo nao e uma consequencia do start dar certo: se o start
    for recusado por limite de tentativas, o rollback desfaria a expiracao e a
    tentativa ficaria eternamente IN_PROGRESS — bloqueada pela constraint
    parcial, impedindo qualquer nova tentativa ate o comando de gestao rodar.

    expire_attempt trava a linha e e idempotente, entao dois starts
    simultaneos do mesmo aluno nao produzem dois eventos.
    """
    aberta = ExamAttempt.objects.filter(
        student=student, exam=exam, status=AttemptStatus.IN_PROGRESS
    ).first()
    if aberta is not None and aberta.prazo_vencido(agora):
        expire_attempt(aberta, agora=agora, request=request)


def _calcular_prazo(exam, started_at):
    """
    Quando esta tentativa vence.

    O menor entre "a duracao acabou" e "a prova fechou". Um aluno que comeca
    faltando dez minutos para o encerramento tem dez minutos, e nao a duracao
    inteira: a janela da prova vale para todos.

    Calculado uma unica vez. Depois de gravado, expires_at nao e recalculado
    por nada — nem se o administrador fechar a prova, nem se a duracao mudar.
    """
    fim_por_duracao = started_at + timedelta(minutes=exam.duration_minutes)
    return min(fim_por_duracao, exam.close_at)


def _montar_tentativa(tentativa, exam):
    """
    Cria as questoes e alternativas desta tentativa, na ordem que este aluno
    vera, com os tokens que so ele conhece.

    Roda uma vez, dentro da transacao do start. A ordem e os tokens ficam
    gravados: e isso que faz o F5 devolver exatamente a mesma tela.
    """
    questoes = list(
        Question.objects.filter(exam=exam, active=True)
        .prefetch_related("options")
        .order_by("order", "id")
    )

    if exam.randomize_questions:
        # SystemRandom, e nao random: o embaralhamento de uma prova nao deve
        # ser reproduzivel por quem observe outras tentativas.
        secrets.SystemRandom().shuffle(questoes)

    AttemptQuestion.objects.bulk_create(
        [
            AttemptQuestion(
                attempt=tentativa,
                question=questao,
                display_order=posicao,
                # Copia o valor da questao agora. Hoje a prova publicada e
                # imutavel e o snapshot coincidiria com question.points, mas a
                # nota de um aluno e registro historico: "sobre quantos pontos
                # esta questao foi avaliada" nao pode depender de nada que
                # aconteca depois. E e este campo que a constraint de teto
                # compara, porque uma check enxerga apenas a propria linha.
                points_snapshot=questao.points,
            )
            for posicao, questao in enumerate(questoes)
        ]
    )

    # Recarrega para ter as PKs junto com a Question de cada linha.
    criadas = list(
        AttemptQuestion.objects.filter(attempt=tentativa).select_related("question")
    )
    por_questao = {linha.question_id: linha for linha in criadas}

    opcoes = []
    for questao in questoes:
        if questao.type not in TIPOS_COM_ALTERNATIVAS:
            continue

        alternativas = list(questao.options.all())
        # Verdadeiro/Falso mantem a ordem canonica mesmo com sorteio ligado.
        # Sao duas alternativas de significado fixo: inverter a posicao delas
        # nao esconde nada de ninguem, e so torna a leitura mais lenta para
        # quem esta fazendo a prova no celular.
        if exam.randomize_options and questao.type != QuestionType.TRUE_FALSE:
            secrets.SystemRandom().shuffle(alternativas)

        linha = por_questao[questao.pk]
        for posicao, alternativa in enumerate(alternativas):
            opcoes.append(
                AttemptOption(
                    attempt_question=linha,
                    option=alternativa,
                    display_order=posicao,
                )
            )

    AttemptOption.objects.bulk_create(opcoes)
    return len(criadas), len(opcoes)


# ---------------------------------------------------------------------------
# Autosave
# ---------------------------------------------------------------------------


def autosave_answer(
    tentativa, *, question_token, option_tokens=None, text=None, request=None
):
    """
    Grava a resposta de uma questao e devolve o tempo restante.

    Idempotente: salvar duas vezes a mesma coisa nao cria duas respostas nem
    deixa selecao antiga para tras. A cada gravacao a selecao anterior daquela
    questao e substituida pela nova, entao nao existe residuo.

    Trava a tentativa antes de olhar o status. E isso que impede a corrida com
    o envio: se o submit chegou primeiro, este autosave acorda com a tentativa
    ja encerrada e recusa; se este chegou primeiro, o submit espera e finaliza
    depois com a resposta ja gravada.
    """
    agora = timezone.now()
    vencida = False

    with transaction.atomic():
        travada = (
            ExamAttempt.objects.select_for_update()
            .select_related("exam")
            .get(pk=tentativa.pk)
        )

        if travada.status != AttemptStatus.IN_PROGRESS:
            raise TentativaNaoEditavel(
                _mensagem_de_encerramento(travada.status), status=travada.status
            )

        if travada.prazo_vencido(agora):
            # O tempo acabou entre a ultima tela e este request.
            #
            # A expiracao NAO acontece aqui dentro. Este bloco termina
            # levantando TentativaNaoEditavel, e a excecao faria rollback de
            # tudo que ele tivesse gravado — inclusive da propria expiracao e
            # do evento de auditoria dela. A tentativa voltaria a IN_PROGRESS
            # e o aluno continuaria batendo numa porta que nunca fecha.
            #
            # Entao apenas anota, deixa a transacao fechar em paz, e encerra
            # logo abaixo com transacao propria.
            vencida = True
        else:
            linha = _resolver_questao(travada, question_token)
            tipo = linha.question.type

            if tipo in TIPOS_COM_ALTERNATIVAS:
                escolhidas = _resolver_alternativas(linha, tipo, option_tokens or [])
                resposta = _gravar_resposta(linha, texto="", agora=agora)
                _gravar_selecao(resposta, escolhidas)
            else:
                _gravar_resposta(linha, texto=_texto_valido(tipo, text), agora=agora)
                # Uma questao textual nao tem alternativa marcada. Se o tipo
                # da questao mudar entre etapas, a selecao velha nao pode
                # sobreviver.
                AnswerOption.objects.filter(answer__attempt_question=linha).delete()

            # Informacao operacional. Nao mexe no prazo: renovar o tempo a
            # cada gravacao transformaria a duracao da prova em tempo de
            # inatividade.
            travada.last_activity_at = agora
            travada.save(update_fields=["last_activity_at", "updated_at"])

            restantes = travada.segundos_restantes(agora)

    if vencida:
        # Fora da transacao acima, e por isso sobrevive a excecao seguinte.
        expire_attempt(tentativa, agora=agora, request=request)
        raise TentativaNaoEditavel(
            _mensagem_de_encerramento(AttemptStatus.EXPIRED),
            status=AttemptStatus.EXPIRED,
        )

    return {"saved": True, "remaining_seconds": restantes}


def _resolver_questao(tentativa, token):
    """
    Traduz um token publico na questao daquela tentativa.

    A tentativa entra no filtro junto com o token. Um token de outra tentativa
    existe no banco e e valido para o dono dele, mas nao casa com este filtro
    — entao a resposta e a mesma de um token inventado.
    """
    if not _uuid_valido(token):
        raise TokenInvalido("Questao invalida.")

    linha = (
        AttemptQuestion.objects.select_related("question")
        .filter(public_token=token, attempt=tentativa)
        .first()
    )
    if linha is None:
        raise TokenInvalido("Questao invalida.")
    return linha


def _resolver_alternativas(linha, tipo, tokens):
    """
    Traduz tokens de alternativa, exigindo que todos sejam daquela questao.

    A checagem de pertencimento e o filtro por attempt_question: uma
    alternativa da questao 3 simplesmente nao aparece ao procurar dentro da
    questao 5, mesmo sendo da mesma tentativa e do mesmo aluno.
    """
    # Normaliza preservando a ordem de chegada. Marcar a mesma alternativa
    # duas vezes e a mesma coisa que marcar uma, e nao um erro do aluno.
    unicos = list(dict.fromkeys(str(token) for token in tokens if token))

    if not unicos:
        return []

    if tipo != QuestionType.MULTIPLE_CHOICE and len(unicos) > 1:
        raise TokenInvalido("Esta questao aceita apenas uma alternativa.")

    for token in unicos:
        if not _uuid_valido(token):
            raise TokenInvalido("Alternativa invalida.")

    encontradas = list(
        AttemptOption.objects.filter(attempt_question=linha, public_token__in=unicos)
    )
    if len(encontradas) != len(unicos):
        raise TokenInvalido("Alternativa invalida.")
    return encontradas


def _texto_valido(tipo, texto):
    """
    Normaliza e confere o tamanho de uma resposta textual.

    Só normaliza quebra de linha: \r\n do Windows vira \n, para que o mesmo
    texto nao ocupe tamanhos diferentes conforme o sistema do aluno. O
    conteudo em si e gravado como foi digitado — sem strip, sem colapsar
    espacos. Numa dissertativa, o recuo de paragrafo e do autor.
    """
    texto = "" if texto is None else str(texto)
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")

    limite = LIMITES_DE_TEXTO.get(tipo, LIMITE_ESSAY)
    if len(texto) > limite:
        raise DomainError(
            "Resposta muito longa: o limite e de {} caracteres.".format(limite)
        )
    return texto


def _gravar_resposta(linha, *, texto, agora):
    resposta, _ = Answer.objects.update_or_create(
        attempt_question=linha,
        defaults={"text_answer": texto, "saved_at": agora},
    )
    return resposta


def _gravar_selecao(resposta, escolhidas):
    """
    Substitui a selecao inteira da questao.

    Apagar e recriar, em vez de calcular a diferenca, e o que garante que
    desmarcar funcione: o conjunto gravado passa a ser exatamente o que
    chegou, sem sobra da gravacao anterior.
    """
    AnswerOption.objects.filter(answer=resposta).delete()
    if escolhidas:
        AnswerOption.objects.bulk_create(
            [
                AnswerOption(answer=resposta, attempt_option=alternativa)
                for alternativa in escolhidas
            ]
        )


# ---------------------------------------------------------------------------
# Envio
# ---------------------------------------------------------------------------


def submit_attempt(tentativa, *, request=None):
    """
    Encerra a tentativa por decisao do aluno.

    Idempotente: um segundo POST numa tentativa ja encerrada nao altera
    submitted_at, nao mexe nas respostas e nao grava um segundo evento. Apenas
    devolve a tentativa como ela esta, e a view leva o aluno a pagina final.
    Tratar isso como erro puniria o duplo clique e o F5 na pagina de envio.

    Se o prazo ja venceu, o envio voluntario nao vence o relogio: a tentativa
    e encerrada como EXPIRED, com as respostas que existirem. Fingir que o
    aluno enviou algo que ele nao enviou dentro do prazo falsificaria o
    registro.
    """
    agora = timezone.now()

    with transaction.atomic():
        travada = (
            ExamAttempt.objects.select_for_update()
            .select_related("exam")
            .get(pk=tentativa.pk)
        )

        if travada.status != AttemptStatus.IN_PROGRESS:
            return travada

        if travada.prazo_vencido(agora):
            return _expirar(travada, agora=agora, request=request)

        pendentes = questoes_obrigatorias_sem_resposta(travada)
        if pendentes:
            # Recusa sem encerrar: enquanto houver tempo, o aluno volta e
            # responde. Encerrar aqui custaria a prova dele por um descuido.
            raise ObrigatoriasPendentes(pendentes)

        travada.status = AttemptStatus.SUBMITTED
        travada.submitted_at = agora
        travada.last_activity_at = agora
        travada.save(
            update_fields=["status", "submitted_at", "last_activity_at", "updated_at"]
        )

        record(
            AuditEvent.ATTEMPT_SUBMITTED,
            request=request,
            actor=travada.student,
            student=travada.student,
            entity_type="ExamAttempt",
            entity_id=travada.pk,
            metadata={
                "exam_id": travada.exam_id,
                "attempt_number": travada.attempt_number,
                "submitted_at": agora.isoformat(),
                "answered": _total_respondidas(travada),
            },
        )

    # Fora da transacao do envio, de proposito.
    #
    # Se a correcao rodasse aqui dentro e falhasse, o rollback levaria junto o
    # envio do aluno — ele teria clicado em finalizar e a prova voltaria a
    # ficar aberta, ou pior, perderia o carimbo de entrega. A entrega e um
    # fato dele; a correcao e trabalho do sistema, e pode ser refeita.
    #
    # grade_objective_questions e idempotente, entao uma falha aqui e
    # recuperavel: a proxima chamada corrige do mesmo jeito.
    return _corrigir_apos_encerramento(travada, request=request)


def questoes_obrigatorias_sem_resposta(tentativa):
    """
    Numeros das questoes obrigatorias ainda em branco, na ordem da tela.

    Duas consultas, independentemente do tamanho da prova. Devolve o numero
    exibido (display_order + 1), que e o unico jeito de o aluno localizar a
    questao — ele nunca viu um id.
    """
    respondidas = _ids_respondidos(tentativa)

    numeros = []
    for linha in (
        AttemptQuestion.objects.filter(attempt=tentativa)
        .select_related("question")
        .order_by("display_order", "id")
    ):
        if linha.question.required and linha.pk not in respondidas:
            numeros.append(linha.display_order + 1)
    return numeros


def _ids_respondidos(tentativa):
    """
    AttemptQuestion que tem resposta com conteudo.

    Uma Answer existir nao basta: o aluno pode ter digitado e apagado, ou
    marcado e desmarcado. Conta como respondida quem tem ao menos uma
    alternativa marcada ou texto com algo alem de espaco.

    O strip serve so para decidir se ha conteudo. O texto gravado nao e
    alterado — numa dissertativa a formatacao e do autor.
    """
    respondidas = set()
    consulta = Answer.objects.filter(attempt_question__attempt=tentativa).annotate(
        marcadas=Count("selected_options")
    )
    for resposta in consulta:
        if resposta.marcadas > 0 or resposta.text_answer.strip():
            respondidas.add(resposta.attempt_question_id)
    return respondidas


def _total_respondidas(tentativa):
    return len(_ids_respondidos(tentativa))


# ---------------------------------------------------------------------------
# Expiracao
# ---------------------------------------------------------------------------


def expire_attempt(tentativa, *, agora=None, request=None):
    """
    Encerra uma tentativa cujo prazo venceu.

    Ponto de entrada publico: adquire a trava e delega a _expirar, que e onde
    a regra mora. O comando de gestao e o acesso web chegam a mesma funcao —
    a diferenca e so quem ja esta segurando a trava quando chega.
    """
    agora = agora or timezone.now()

    with transaction.atomic():
        travada = ExamAttempt.objects.select_for_update().get(pk=tentativa.pk)
        _expirar(travada, agora=agora, request=request)

    # Tambem fora da transacao, pelo mesmo motivo do envio: a expiracao e um
    # fato do relogio e nao pode ser desfeita por uma falha na correcao.
    #
    # Uma prova expirada e corrigida como qualquer outra, e o que ficou em
    # branco vale zero. Tratar expirada como "sem nota" deixaria o aluno num
    # limbo permanente, sem resultado e sem explicacao.
    return _corrigir_apos_encerramento(travada, request=request)


def _expirar(tentativa, *, agora, request=None):
    """
    A regra de expiracao, em um lugar so.

    Quem chama ja precisa estar segurando a trava da tentativa. Existe para
    que o acesso web — que descobre a expiracao no meio de um autosave ou de
    um envio, ja dentro da transacao travada — nao precise de uma segunda
    versao da mesma regra.

    Nao verifica questao obrigatoria em branco. O tempo acabou: barrar a
    expiracao por falta de resposta deixaria a tentativa presa em andamento
    para sempre, e o aluno nao pode mais responder de qualquer forma.

    Idempotente: uma tentativa que ja saiu de IN_PROGRESS e devolvida como
    esta, sem segundo evento na trilha.
    """
    if tentativa.status != AttemptStatus.IN_PROGRESS:
        return tentativa

    tentativa.status = AttemptStatus.EXPIRED
    tentativa.expired_at = agora
    # submitted_at continua nulo. Os dois campos contam coisas diferentes, e
    # so um deles pode ser verdade.
    tentativa.save(update_fields=["status", "expired_at", "updated_at"])

    record(
        AuditEvent.ATTEMPT_EXPIRED,
        request=request,
        # Sem actor de proposito: expirar e acao do relogio, nao de alguem. O
        # comando de gestao expira sem ninguem logado, e o aluno que dispara a
        # expiracao ao voltar na tela nao decidiu encerrar nada.
        student=tentativa.student,
        entity_type="ExamAttempt",
        entity_id=tentativa.pk,
        metadata={
            "exam_id": tentativa.exam_id,
            "attempt_number": tentativa.attempt_number,
            "expires_at": tentativa.expires_at.isoformat(),
            "expired_at": agora.isoformat(),
            "answered": _total_respondidas(tentativa),
        },
    )
    return tentativa


def expirar_tentativas_vencidas(*, agora=None, lote=100, limite=None):
    """
    Encerra todas as tentativas em andamento cujo prazo ja passou.

    Usada pelo comando de gestao. Trabalha em lotes para nao carregar uma
    turma inteira na memoria, e cada tentativa e travada individualmente pela
    expire_attempt — a fila e curta e nao segura o banco.

    Idempotente por consequencia: a segunda execucao nao encontra nada, porque
    a primeira tirou todas de IN_PROGRESS.

    A expiracao tambem acontece sozinha, no proximo request do aluno. Este
    comando existe para as tentativas orfas — a aba que fechou, o notebook que
    dormiu, o aluno que nunca mais voltou.
    """
    agora = agora or timezone.now()
    total = 0
    vistos = set()

    while True:
        if limite is not None and total >= limite:
            break

        pks = list(
            ExamAttempt.objects.vencidas(agora)
            .exclude(pk__in=vistos)
            .order_by("expires_at", "pk")
            .values_list("pk", flat=True)[:lote]
        )
        if not pks:
            break

        for pk in pks:
            # vistos protege contra laco infinito caso alguma tentativa nao
            # saia de IN_PROGRESS: sem isso a mesma pagina voltaria sempre.
            vistos.add(pk)
            tentativa = ExamAttempt.objects.filter(pk=pk).first()
            if tentativa is None:
                continue
            antes = tentativa.status
            expire_attempt(tentativa, agora=agora)
            if antes == AttemptStatus.IN_PROGRESS:
                total += 1
            if limite is not None and total >= limite:
                break

    return total


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


def _uuid_valido(valor):
    try:
        uuid.UUID(str(valor))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _mensagem_de_encerramento(status):
    if status == AttemptStatus.EXPIRED:
        return "O tempo desta prova terminou."
    if status == AttemptStatus.SUBMITTED:
        return "Esta prova ja foi enviada."
    return "Esta tentativa nao esta mais disponivel."


def _corrigir_apos_encerramento(tentativa, *, request=None):
    """
    Dispara a correcao automatica de uma tentativa recem-encerrada.

    Vive aqui, e nao em grading.py, para evitar import circular: grading.py ja
    importa os modelos de tentativa, e attempt.py precisa apenas desta ponte.
    O import e local pelo mesmo motivo.

    Silencioso quanto a estado: se a tentativa nao estiver num estado
    corrigivel, nada acontece. Quem chama acabou de encerra-la, entao o caso
    normal e corrigir.
    """
    from exams.services.grading import grade_objective_questions

    if tentativa.status not in ESTADOS_CORRIGIVEIS:
        return tentativa

    return grade_objective_questions(tentativa, request=request)
