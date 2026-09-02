"""
Emissao e revogacao de certificados.

Regra unica de quem pode receber
--------------------------------
Somente uma tentativa corrigida e aprovada. Nao existe caminho alternativo:
nem a tela do aluno, nem a do administrador, nem o Django Admin criam
Certificate sem passar por aqui.

Idempotencia e concorrencia
---------------------------
Emitir e uma acao de um clique numa pagina que o aluno pode recarregar. Dois
cliques, dois toques no celular ou duas abas nao podem produzir dois
documentos com codigos diferentes para a mesma conclusao — cada codigo extra
seria um certificado autentico e verificavel a mais circulando por engano.

A garantia vem em duas camadas:

    OneToOneField        o banco recusa a segunda linha
    select_for_update    a segunda requisicao espera a primeira terminar,
                         encontra o certificado pronto e o devolve

Sem o lock, duas requisicoes simultaneas leriam "nao existe" ao mesmo tempo e
a segunda morreria com IntegrityError na cara do aluno. Com ele, a segunda
simplesmente recebe o mesmo documento.
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from certificates.models import (
    VERSAO_ATUAL_DO_MODELO,
    Certificate,
    CertificateStatus,
)
from certificates.services_templates import exigir_template
from certificates.snapshot import montar_snapshot
from common.exceptions import DomainError
from courses.models import Enrollment
from courses.services import complete_enrollment
from exams.models import AttemptResult, AttemptStatus, ExamAttempt, GradingStatus


class TentativaNaoAprovada(DomainError):
    """A tentativa nao satisfaz as condicoes para gerar certificado."""


class CertificadoRevogado(DomainError):
    """Operacao invalida sobre um certificado ja revogado."""


class DadosDoCertificadoIncompletos(DomainError):
    """Faltam no modulo dados que precisam sair impressos no documento."""


# Canais de compartilhamento reconhecidos. Lista fechada de proposito: o valor
# vai para a trilha de auditoria, e um canal que o navegador pudesse inventar
# encheria a metadata de texto arbitrario.
CANAL_WHATSAPP = "whatsapp"
CANAL_NATIVO = "native"
CANAIS = (CANAL_WHATSAPP, CANAL_NATIVO)


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def pode_emitir(attempt):
    """
    Se esta tentativa, como esta agora, geraria um certificado.

    Usada pela interface para decidir se o botao aparece. A validacao real
    acontece no servico: um botao escondido nao e controle de acesso.
    """
    return (
        attempt.status != AttemptStatus.RESET
        and attempt.grading_status == GradingStatus.GRADED
        and attempt.result == AttemptResult.APPROVED
    )


def certificado_da_tentativa(attempt):
    return Certificate.objects.filter(attempt=attempt).first()


def certificados_do_aluno(user):
    return (
        Certificate.objects.do_aluno(user)
        .select_related("attempt", "attempt__exam", "attempt__exam__module")
        .order_by("-issued_at")
    )


def _validar_emissivel(attempt):
    if attempt.status == AttemptStatus.RESET:
        raise TentativaNaoAprovada(
            "Esta tentativa foi anulada e nao gera certificado."
        )
    if attempt.grading_status != GradingStatus.GRADED:
        raise TentativaNaoAprovada(
            "A correcao desta tentativa ainda nao foi finalizada."
        )
    if attempt.result != AttemptResult.APPROVED:
        raise TentativaNaoAprovada(
            "Somente tentativas aprovadas geram certificado."
        )


def _validar_dados_do_modulo(modulo):
    """
    Recusa a emissao enquanto o modulo nao tiver o que sai impresso.

    A alternativa seria gerar o documento com lacunas — "realizado em , em ,
    com carga horaria de horas" — e essa versao quebrada nao seria notada
    antes de o aluno baixar e imprimir. Melhor recusar aqui, dizendo
    exatamente o que preencher.
    """
    faltando = modulo.dados_do_certificado_ausentes()
    if not faltando:
        return
    raise DadosDoCertificadoIncompletos(
        "Nao foi possivel emitir o certificado. Configure os dados do "
        "certificado no modulo {}: {}.".format(modulo.code, ", ".join(faltando))
    )


# ---------------------------------------------------------------------------
# Emissao
# ---------------------------------------------------------------------------


def issue_certificate(attempt, *, actor=None, request=None):
    """
    Emite o certificado de uma tentativa aprovada.

    Devolve (certificado, emitido_agora). O segundo valor e False quando o
    certificado ja existia: quem chama usa isso para escolher a mensagem, sem
    precisar comparar datas.

    Efeitos, todos na mesma transacao:

        cria o Certificate com os textos congelados
        conclui a matricula do aluno naquele modulo e encerra o acesso
        registra CERTIFICATE_ISSUED
    """
    with transaction.atomic():
        travada = (
            ExamAttempt.objects.select_for_update()
            .select_related("student", "exam", "exam__module")
            .get(pk=attempt.pk)
        )

        existente = Certificate.objects.filter(attempt=travada).first()
        if existente is not None:
            # Segundo clique, segunda aba, ou a requisicao que perdeu a
            # corrida. Nao e erro: e o mesmo documento.
            return existente, False

        _validar_emissivel(travada)

        modulo = travada.exam.module
        _validar_dados_do_modulo(modulo)

        # O modelo e resolvido ANTES de criar qualquer linha. Sem modelo nao
        # existe documento: a Etapa 10 tirou o desenho do codigo, e emitir
        # sem configuracao produziria uma folha em branco com texto solto.
        template = exigir_template(modulo)
        snapshot = montar_snapshot(template)

        certificado = Certificate.objects.create(
            attempt=travada,
            status=CertificateStatus.ACTIVE,
            student_name_snapshot=travada.student.full_name,
            module_name_snapshot=modulo.name,
            exam_title_snapshot=travada.exam.title,
            institution_name_snapshot=settings.INSTITUTION_NAME,
            # Tudo que sai impresso vira copia agora. Corrigir a data do
            # modulo no ano que vem nao pode reescrever um documento assinado.
            course_name_snapshot=settings.CERTIFICATE_COURSE_NAME,
            module_display_name_snapshot=modulo.nome_no_certificado,
            course_dates_snapshot=modulo.certificate_course_dates_text.strip(),
            course_location_snapshot=modulo.certificate_location.strip(),
            workload_hours_snapshot=modulo.certificate_workload_hours,
            certificate_year_snapshot=modulo.certificate_year,
            signatory_name_snapshot=settings.CERTIFICATE_SIGNATORY_NAME,
            signatory_title_snapshot=settings.CERTIFICATE_SIGNATORY_TITLE,
            template_version=VERSAO_ATUAL_DO_MODELO,
            certificate_template=template,
            # A copia congelada. A partir daqui o modelo pode ganhar versoes
            # novas, trocar de arte ou ser arquivado — este documento continua
            # sendo desenhado exatamente como foi emitido.
            template_snapshot=snapshot,
        )

        _encerrar_matricula(travada, actor=actor, request=request)

        record(
            AuditEvent.CERTIFICATE_ISSUED,
            request=request,
            actor=actor,
            student=travada.student,
            entity_type="Certificate",
            entity_id=certificado.pk,
            metadata={
                "module_id": modulo.pk,
                "module_code": modulo.code,
                "attempt_number": travada.attempt_number,
                "certificate_status": certificado.status,
                "template_id": template.pk,
                "template_name": template.name,
                "template_version": template.version,
            },
        )

    return certificado, True


def _encerrar_matricula(attempt, *, actor, request):
    """
    Conclui a matricula do aluno no modulo da prova.

    O lock e o mesmo padrao da tentativa: sem ele, duas conclusoes
    concorrentes poderiam gravar em cima uma da outra.
    """
    matricula = (
        Enrollment.objects.select_for_update()
        .select_related("module", "student")
        .filter(student=attempt.student, module=attempt.exam.module)
        .first()
    )
    if matricula is None:
        # Matricula removida entre a prova e a emissao. O certificado ainda
        # vale: ele atesta o que aconteceu, e nao o vinculo de hoje.
        return None
    return complete_enrollment(
        matricula, encerrar_acesso=True, actor=actor, request=request
    )


# ---------------------------------------------------------------------------
# Revogacao
# ---------------------------------------------------------------------------


def revoke_certificate(certificado, *, actor=None, request=None, motivo=""):
    """
    Revoga um certificado, preservando-o.

    Nunca apaga: o codigo antigo continua consultavel e passa a responder
    "revogado". Quem recebeu o documento em papel precisa conseguir descobrir
    que ele deixou de valer — e isso e impossivel se o codigo simplesmente
    desaparecer.

    Revogar NAO reativa a matricula automaticamente. Uma revogacao pode vir
    de erro administrativo, fraude ou correcao documental, e cada uma dessas
    pede uma decisao academica diferente.

    Devolve (certificado, revogado_agora).
    """
    with transaction.atomic():
        travado = (
            Certificate.objects.select_for_update()
            .select_related("attempt", "attempt__student")
            .get(pk=certificado.pk)
        )

        if travado.status == CertificateStatus.REVOKED:
            return travado, False

        travado.status = CertificateStatus.REVOKED
        travado.revoked_at = timezone.now()
        travado.revoked_by = actor if getattr(actor, "pk", None) else None
        travado.revocation_reason = (motivo or "").strip()
        travado.save(
            update_fields=[
                "status",
                "revoked_at",
                "revoked_by",
                "revocation_reason",
                "updated_at",
            ]
        )

        record(
            AuditEvent.CERTIFICATE_REVOKED,
            request=request,
            actor=actor,
            student=travado.attempt.student,
            entity_type="Certificate",
            entity_id=travado.pk,
            metadata={"certificate_status": travado.status},
        )

    return travado, True


# ---------------------------------------------------------------------------
# Compartilhamento
# ---------------------------------------------------------------------------


def mensagem_de_compartilhamento(certificado):
    """
    Texto que acompanha o link, montado inteiramente no servidor.

    O navegador nao envia nem uma palavra dele. Aceitar o texto do frontend
    transformaria um endereco da instituicao num gerador de mensagens de
    terceiros: qualquer pessoa poderia produzir um link de WhatsApp com
    conteudo proprio saindo de um dominio confiavel.

    O que entra: instituicao, curso, modulo e o endereco publico de validacao.
    O que nunca entra: e-mail, nota, numero da tentativa, respostas, ids
    internos. Nada aqui e mais do que o proprio certificado impresso ja mostra.
    """
    from certificates.pdf import url_de_validacao

    curso = certificado.course_name_snapshot or certificado.exam_title_snapshot
    return (
        "{instituicao}\n\n"
        "Concluí o {curso} — {modulo}.\n\n"
        "Valide meu certificado:\n{url}"
    ).format(
        instituicao=certificado.institution_name_snapshot,
        curso=curso,
        modulo=certificado.modulo_impresso,
        url=url_de_validacao(certificado),
    )


def url_do_whatsapp(certificado):
    """
    Deep link oficial do WhatsApp, com a mensagem codificada.

    wa.me sem numero abre o seletor de contato do proprio aplicativo: o
    sistema nao precisa saber, nem guardar, para quem o aluno vai mandar.
    """
    from urllib.parse import quote

    return "https://wa.me/?text={}".format(
        quote(mensagem_de_compartilhamento(certificado), safe="")
    )


def registrar_compartilhamento(certificado, *, canal, actor, request=None):
    """
    Registra que um compartilhamento FOI INICIADO. Nada alem disso.

    Vale repetir, porque a diferenca importa em qualquer leitura futura da
    trilha: este evento significa que alguem apertou o botao. Nao significa
    mensagem enviada, entregue nem lida — depois do clique quem conduz e o
    WhatsApp ou a folha de compartilhamento do celular, e nenhum dos dois
    devolve confirmacao para ca.

    Certificado revogado nao compartilha: divulgar o link de um documento sem
    validade seria ajudar a apresentar como valido algo que nao e.
    """
    if canal not in CANAIS:
        raise DomainError("Canal de compartilhamento desconhecido.")
    if certificado.status == CertificateStatus.REVOKED:
        raise CertificadoRevogado(
            "Este certificado foi revogado e nao pode ser compartilhado."
        )

    record(
        AuditEvent.CERTIFICATE_SHARE_INITIATED,
        request=request,
        actor=actor,
        student=certificado.attempt.student,
        entity_type="Certificate",
        entity_id=certificado.pk,
        # So o canal. A mensagem, a URL e o nome do aluno ja existem em outros
        # lugares, e repeti-los aqui espalharia dado pessoal por uma tabela
        # que so cresce.
        metadata={"channel": canal},
    )
    return certificado
