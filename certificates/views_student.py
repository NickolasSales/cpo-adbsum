"""
Certificados na area do aluno.

Rotas:

    /aluno/certificados/                             lista
    /aluno/certificados/emitir/<public_id>/          POST, emite
    /aluno/certificados/<codigo>/baixar/             PDF
    /aluno/certificados/<codigo>/compartilhar/whatsapp/   POST, 302 wa.me
    /aluno/certificados/<codigo>/compartilhar/nativo/     POST, 204

Tudo aqui e do dono e so do dono. A consulta parte sempre de
Certificate.objects.do_aluno(request.user); um certificado de outra pessoa nao
resulta em 403, e sim em 404 — 403 confirmaria que aquele codigo existe.

Emitir e POST. A emissao conclui a matricula e encerra o acesso ao modulo:
mudanca de estado academico nao pode acontecer porque um pre-visualizador de
link, um antivirus corporativo ou o proprio navegador resolveu buscar uma URL.

Compartilhar tambem e POST, pelo mesmo motivo em outra escala: grava uma linha
na trilha de auditoria. Um GET que escreve enche a trilha toda vez que alguem
passa o mouse sobre o link numa conversa.

O que o navegador escolhe, e o que ele nao escolhe
--------------------------------------------------
Escolhe: qual certificado (pelo caminho) e qual canal (pela rota).
Nao escolhe: a mensagem, o endereco de destino, o codigo do certificado por
POST, nem para onde redirecionar. Os quatro sao montados aqui.
"""

from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from certificates import services
from certificates.models import Certificate, CertificateStatus
from certificates.pdf import render_certificate_pdf
from common.exceptions import DomainError
from common.mixins import StudentRequiredMixin, student_required
from exams.services import attempt as attempt_service


def _certificado_do_aluno_ou_404(request, codigo):
    certificado = (
        Certificate.objects.do_aluno(request.user)
        .select_related("attempt", "attempt__exam", "attempt__exam__module")
        .filter(verification_code=codigo)
        .first()
    )
    if certificado is None:
        raise Http404("Certificado indisponivel.")
    return certificado


class StudentCertificateListView(StudentRequiredMixin, TemplateView):
    """
    Lista dos certificados do aluno.

    Continua acessivel depois que a matricula vira COMPLETED: perder o acesso
    ao modulo nao pode significar perder o documento que comprova te-lo
    concluido.
    """

    template_name = "student/certificates/list.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        certificados = list(services.certificados_do_aluno(self.request.user))

        # A mensagem do compartilhamento nativo precisa estar no HTML antes do
        # clique. navigator.share() so funciona dentro do gesto do usuario: se
        # o JavaScript for buscar o texto no servidor primeiro, o navegador
        # (Safari do iOS em especial) ja considerou o gesto encerrado e recusa
        # a chamada. Entao o servidor entrega o texto pronto, e o registro na
        # trilha vai depois, por fetch, sem segurar o gesto.
        #
        # Continua valendo a regra: o texto e do servidor. O navegador so
        # repassa o que recebeu.
        contexto["certificados"] = [
            {
                "certificado": certificado,
                "compartilhavel": certificado.esta_valido,
                "mensagem": (
                    services.mensagem_de_compartilhamento(certificado)
                    if certificado.esta_valido
                    else ""
                ),
            }
            for certificado in certificados
        ]
        return contexto


@require_POST
@student_required
def certificate_issue(request, public_id):
    """
    Emite o certificado da tentativa aprovada do aluno.

    Idempotente por construcao: o servico devolve o certificado existente em
    vez de criar um segundo. O aluno pode clicar duas vezes sem consequencia.
    """
    tentativa = attempt_service.tentativa_do_aluno_ou_none(request.user, public_id)
    if tentativa is None:
        raise Http404("Tentativa indisponivel.")

    try:
        certificado, emitido = services.issue_certificate(
            tentativa, actor=request.user, request=request
        )
    except DomainError as erro:
        messages.error(request, str(erro))
        return redirect("student:attempt_result", public_id=public_id)

    if emitido:
        messages.success(
            request, "Certificado emitido. O modulo foi concluido."
        )
    else:
        messages.info(request, "Este certificado ja havia sido emitido.")
    return redirect("student:certificate_list")


@student_required
def certificate_download(request, verification_code):
    """
    PDF do certificado, gerado na hora.

    Certificado revogado nao vira PDF. Entregar o arquivo de um documento sem
    validade seria entregar algo que parece valido e nao e — a pessoa pode
    imprimir e apresentar sem nunca conferir o QR.
    """
    certificado = _certificado_do_aluno_ou_404(request, verification_code)

    if certificado.status == CertificateStatus.REVOKED:
        return HttpResponse(
            "Este certificado foi revogado e nao pode mais ser baixado.",
            content_type="text/plain; charset=utf-8",
            status=409,
        )

    pdf = render_certificate_pdf(certificado)
    resposta = HttpResponse(pdf, content_type="application/pdf")
    resposta["Content-Disposition"] = 'attachment; filename="{}"'.format(
        certificado.nome_do_arquivo
    )
    resposta["Content-Length"] = str(len(pdf))
    # Documento pessoal: nao pode ficar em cache compartilhado de proxy.
    resposta["Cache-Control"] = "private, no-store"
    return resposta


# ---------------------------------------------------------------------------
# Compartilhamento
# ---------------------------------------------------------------------------


def _registrar_ou_409(request, verification_code, canal):
    """
    Parte comum dos dois canais.

    Devolve (certificado, None) quando pode seguir, ou (None, resposta) com a
    recusa pronta. O certificado vem SEMPRE do caminho da URL cruzado com o
    dono da sessao: nenhum campo do POST escolhe qual documento e este.
    """
    certificado = _certificado_do_aluno_ou_404(request, verification_code)
    try:
        services.registrar_compartilhamento(
            certificado, canal=canal, actor=request.user, request=request
        )
    except DomainError as erro:
        return None, HttpResponse(
            str(erro), content_type="text/plain; charset=utf-8", status=409
        )
    return certificado, None


@require_POST
@student_required
def certificate_share_whatsapp(request, verification_code):
    """
    Registra a intencao e manda o aluno para o WhatsApp.

    O destino e construido aqui, inteiro. Nenhum parametro de URL, next ou
    redirect vindo do navegador participa: caso participasse, esta rota — que
    exige login e por isso parece confiavel — viraria um redirecionador aberto
    para qualquer endereco.
    """
    certificado, recusa = _registrar_ou_409(
        request, verification_code, services.CANAL_WHATSAPP
    )
    if recusa is not None:
        return recusa
    return HttpResponseRedirect(services.url_do_whatsapp(certificado))


@require_POST
@student_required
def certificate_share_native(request, verification_code):
    """
    Registra a intencao do compartilhamento pela folha nativa do sistema.

    Responde 204 porque nao ha nada a devolver: quem abre a folha e o proprio
    navegador, com o texto que ja estava na pagina. Esta rota existe so para
    a trilha de auditoria — e, como toda escrita, e POST com CSRF.
    """
    _, recusa = _registrar_ou_409(
        request, verification_code, services.CANAL_NATIVO
    )
    if recusa is not None:
        return recusa
    return HttpResponse(status=204)
