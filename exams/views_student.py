"""
Telas da prova vistas pelo aluno.

As views daqui nao decidem nada de dominio. Elas resolvem quem esta pedindo,
chamam exams.services.attempt e apresentam. Toda regra — quem pode comecar,
se ainda da tempo, se a resposta pode ser gravada — vive no servico, porque
essa e a unica camada que o comando de gestao tambem atravessa.

Politica de erro, e por que ela nao e uniforme
----------------------------------------------
    404   o aluno nao deveria saber que aquilo existe: prova de modulo em que
          ele nao tem matricula liberada, tentativa de outro aluno, tentativa
          com identificador inventado. Um 403 confirmaria a existencia, que e
          exatamente o que o sondador queria descobrir.

    409   a coisa existe e e dele, mas o estado nao permite: autosave numa
          tentativa ja enviada, envio com questao obrigatoria em branco. O
          navegador precisa distinguir isso de erro de rede para nao dizer
          "Salvo" quando nada foi salvo.

    302   navegacao normal recusada por motivo que o aluno resolve: prova
          fora da janela, tentativas esgotadas, senha errada. Volta para a
          tela de instrucoes com a mensagem.

Nenhum GET altera estado. Iniciar, salvar e finalizar sao POST com CSRF.
"""

import json

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from common.exceptions import DomainError
from common.mixins import StudentRequiredMixin, student_required
from exams import selectors
from exams.models import AttemptStatus
from exams.services import attempt as attempt_service


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


def _prova_ou_404(request, exam_id):
    prova = attempt_service.prova_visivel_ou_none(request.user, exam_id)
    if prova is None:
        raise Http404("Prova indisponivel.")
    return prova


def _tentativa_ou_404(request, public_id):
    tentativa = attempt_service.tentativa_do_aluno_ou_none(request.user, public_id)
    if tentativa is None:
        raise Http404("Tentativa indisponivel.")
    return tentativa


def _json_de_conflito(mensagem, *, status_da_tentativa="", extras=None):
    """
    Resposta 409 do autosave.

    Sempre informa saved=false. O navegador usa esse campo para nao exibir
    "Salvo" quando nada foi gravado — dizer que salvou uma resposta que se
    perdeu e o pior erro que esta tela poderia cometer.
    """
    corpo = {"saved": False, "error": mensagem}
    if status_da_tentativa:
        corpo["status"] = status_da_tentativa
    if extras:
        corpo.update(extras)
    return JsonResponse(corpo, status=409)


# ---------------------------------------------------------------------------
# Instrucoes
# ---------------------------------------------------------------------------


class ExamInstructionsView(StudentRequiredMixin, TemplateView):
    """
    A tela anterior a prova. GET puro: nao cria tentativa, nunca.

    E a unica tela que o aluno recarrega varias vezes antes de comecar, com
    ansiedade e as vezes com dois aparelhos abertos. Se um GET criasse
    tentativa, cada recarregamento gastaria uma das chances dele.
    """

    template_name = "student/exams/instructions.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        prova = _prova_ou_404(self.request, self.kwargs["exam_id"])
        agora = timezone.now()

        cartoes = selectors.provas_do_modulo_para_aluno(
            prova.module, self.request.user, agora=agora
        )
        cartao = next((item for item in cartoes if item.id == prova.pk), None)

        contexto["prova"] = prova
        contexto["cartao"] = cartao
        contexto["agora"] = agora
        contexto["tentativas"] = list(
            attempt_service.tentativas_do_aluno(self.request.user, prova)
        )
        contexto["erros"] = self.request.session.pop("erros_do_inicio", [])
        return contexto


@require_POST
@student_required
def attempt_start(request, exam_id):
    """
    O unico ponto do sistema que cria uma tentativa.

    POST com CSRF. A senha, quando existe, chega aqui e morre aqui: e passada
    ao servico como argumento e nao e gravada, logada nem auditada.
    """
    prova = _prova_ou_404(request, exam_id)

    try:
        tentativa = attempt_service.start_attempt(
            request.user,
            prova,
            supplied_password=request.POST.get("access_password") or "",
            request=request,
        )
    except attempt_service.SemAcessoAProva:
        # Deixou de ter acesso entre abrir a tela e clicar em iniciar.
        raise Http404("Prova indisponivel.")
    except DomainError as erro:
        # A mensagem volta pela sessao, e nao por querystring: motivo de
        # recusa nao precisa ficar no historico do navegador nem em log de
        # acesso, e mensagem de senha invalida menos ainda.
        request.session["erros_do_inicio"] = list(erro.mensagens)
        return redirect("student:exam_instructions", exam_id=prova.pk)

    return redirect("student:attempt", public_id=tentativa.public_id)


# ---------------------------------------------------------------------------
# A prova
# ---------------------------------------------------------------------------


class AttemptView(StudentRequiredMixin, TemplateView):
    """
    A tela da prova, ou a pagina final quando a tentativa ja acabou.

    Uma rota so para os dois casos de proposito. Se a pagina final fosse outra
    URL, voltar pelo historico do navegador devolveria o formulario editavel
    da tela antiga; aqui qualquer request novo passa por esta checagem, e uma
    tentativa encerrada nunca mais renderiza campo de resposta.

    O prazo tambem e verificado a cada carregamento: quem volta depois do
    tempo encontra a tentativa ja encerrada, sem precisar do comando de
    expiracao ter rodado.
    """

    def get_template_names(self):
        if self.tentativa.encerrada:
            return ["student/exams/finished.html"]
        return ["student/exams/attempt.html"]

    def get(self, request, *args, **kwargs):
        self.tentativa = _tentativa_ou_404(request, kwargs["public_id"])

        agora = timezone.now()
        if self.tentativa.em_andamento and self.tentativa.prazo_vencido(agora):
            self.tentativa = attempt_service.expire_attempt(
                self.tentativa, agora=agora, request=request
            )

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(contexto_da_tentativa(self.tentativa))
        return contexto


def contexto_da_tentativa(tentativa, *, agora=None):
    """
    O que a tela da prova precisa, num lugar so.

    Existe porque duas rotas renderizam a mesma tela: o GET normal e o envio
    recusado por questao obrigatoria em branco, que responde 409 com a prova
    inteira de volta. Montar o contexto duas vezes acabaria com as duas telas
    divergindo na primeira mudanca.
    """
    agora = agora or timezone.now()

    contexto = {
        "tentativa": tentativa,
        "prova": tentativa.exam,
    }

    if tentativa.encerrada:
        contexto["enviada"] = tentativa.status == AttemptStatus.SUBMITTED
        return contexto

    contexto["questoes"] = selectors.questoes_da_tentativa(tentativa)
    contexto["segundos_restantes"] = tentativa.segundos_restantes(agora)
    return contexto


@require_POST
@student_required
def attempt_autosave(request, public_id):
    """
    Grava a resposta de uma questao. Chamado pelo JavaScript da tela.

    Aceita JSON ou formulario. CSRF continua obrigatorio nos dois casos: o
    endpoint nao e csrf_exempt, e o JavaScript envia o token no cabecalho.

    Nada do corpo da requisicao identifica aluno ou tentativa. O aluno vem da
    sessao, a tentativa vem da URL, e a questao vem de um token que so vale
    dentro dessa combinacao.
    """
    tentativa = _tentativa_ou_404(request, public_id)

    dados = _corpo_do_autosave(request)
    if dados is None:
        return JsonResponse(
            {"saved": False, "error": "Requisicao invalida."}, status=400
        )

    try:
        resultado = attempt_service.autosave_answer(
            tentativa,
            question_token=dados["question_token"],
            option_tokens=dados["option_tokens"],
            text=dados["text"],
            request=request,
        )
    except attempt_service.TentativaNaoEditavel as erro:
        return _json_de_conflito(
            str(erro), status_da_tentativa=erro.status_da_tentativa
        )
    except attempt_service.TokenInvalido as erro:
        return _json_de_conflito(str(erro))
    except DomainError as erro:
        return _json_de_conflito(str(erro))

    return JsonResponse(resultado)


def _corpo_do_autosave(request):
    """
    Le o payload aceitando JSON ou formulario.

    Le somente tres coisas: qual questao, quais alternativas, qual texto.
    Qualquer outro campo que venha junto — status, expires_at, student_id,
    score — e simplesmente ignorado, porque nao ha nada aqui que o leia.
    """
    if request.content_type and "application/json" in request.content_type:
        try:
            corpo = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(corpo, dict):
            return None
        opcoes = corpo.get("option_tokens") or []
        if not isinstance(opcoes, list):
            return None
        questao = corpo.get("question_token")
        texto = corpo.get("text")
    else:
        questao = request.POST.get("question_token")
        opcoes = request.POST.getlist("option_tokens")
        texto = request.POST.get("text")

    if not questao:
        return None

    return {
        "question_token": str(questao),
        "option_tokens": [str(item) for item in opcoes],
        "text": texto,
    }


@require_POST
@student_required
def attempt_submit(request, public_id):
    """
    Finaliza a tentativa por decisao do aluno.

    Sem nota, sem correcao e sem nada vindo do navegador alem do token CSRF.
    O servico decide se o envio vale, se o prazo ja venceu ou se falta
    questao obrigatoria.
    """
    tentativa = _tentativa_ou_404(request, public_id)

    try:
        attempt_service.submit_attempt(tentativa, request=request)
    except attempt_service.ObrigatoriasPendentes as erro:
        # Nao encerra: enquanto houver tempo, o aluno volta e responde. A
        # prova e devolvida inteira, com as respostas ja salvas no lugar, e
        # com 409 — o pedido era valido, o estado e que nao permite atende-lo.
        # Um 200 aqui faria a tela parecer um envio bem-sucedido.
        contexto = contexto_da_tentativa(tentativa)
        contexto["numeros_pendentes"] = erro.numeros
        contexto["erros_de_envio"] = erro.mensagens
        return render(request, "student/exams/attempt.html", contexto, status=409)

    return redirect("student:attempt", public_id=tentativa.public_id)


class AttemptResultView(StudentRequiredMixin, TemplateView):
    """
    Resultado da tentativa para o aluno. Somente o dono.

    O que aparece depende da correcao:

        AWAITING_REVIEW / PENDING   "aguardando correcao", sem numero nenhum
        GRADED                      resultado, e a nota se a prova permitir

    Nao existe nota provisoria. Mesmo quando as objetivas ja foram corrigidas e
    o sistema sabe que o aluno tem 6 dos 10 pontos, a tela nao diz isso:
    metade de uma nota nao e informacao, e um aluno que ler "6 pontos ate
    agora" vai calcular a propria aprovacao com dados incompletos.

    Gabarito nunca aparece aqui — nem alternativa correta, nem is_correct, nem
    explicacao interna, nem o comentario do avaliador, que e uso
    administrativo. O contexto e montado a partir de campos escalares da
    tentativa, entao nao ha objeto de questao nesta tela para vazar nada.
    """

    template_name = "student/exams/result.html"

    def get(self, request, *args, **kwargs):
        self.tentativa = _tentativa_ou_404(request, kwargs["public_id"])
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from exams.models import AttemptResult, GradingStatus
        from exams.services import grading

        contexto = super().get_context_data(**kwargs)
        tentativa = self.tentativa
        prova = tentativa.exam

        corrigida = tentativa.grading_status == GradingStatus.GRADED
        aprovado = corrigida and tentativa.result == AttemptResult.APPROVED

        contexto["tentativa"] = tentativa
        contexto["prova"] = prova
        contexto["corrigida"] = corrigida
        contexto["aprovado"] = aprovado
        contexto["reprovado"] = corrigida and not aprovado
        contexto["enviada"] = tentativa.status == AttemptStatus.SUBMITTED

        # A prova decide se o aluno ve o numero. O resultado ele ve sempre:
        # esconder "aprovado ou reprovado" tornaria a tela inutil.
        mostrar_nota = corrigida and prova.show_score_after_submission
        contexto["mostrar_nota"] = mostrar_nota
        contexto["nota"] = (
            grading.nota_para_exibicao(tentativa.final_score) if mostrar_nota else ""
        )
        contexto["nota_minima"] = (
            grading.nota_para_exibicao(tentativa.passing_score_snapshot)
            if mostrar_nota
            else ""
        )

        # Mensagem de reprovacao configurada na prova. So aparece a quem
        # reprovou — mostra-la a um aprovado seria cruel e confuso.
        contexto["mensagem_reprovacao"] = (
            prova.failure_message if contexto["reprovado"] else ""
        )
        return contexto
