"""
Servicos de dominio de modulos e matriculas.

As views nao criam nem alteram Module ou Enrollment diretamente. Toda regra
— normalizacao de codigo, recusa de ADMIN como aluno, tratamento de matricula
existente — vive aqui, em um unico ponto de execucao.
"""

from django.db import transaction
from django.utils import timezone

from accounts.models import UserRole
from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError, campos_alterados
from common.texto import validar_motivo
from courses.models import (
    ANO_MAXIMO_DO_CERTIFICADO,
    ANO_MINIMO_DO_CERTIFICADO,
    Enrollment,
    EnrollmentStatus,
    Module,
    normalizar_codigo,
)

# Os unicos nomes que create_module e update_module aceitam no dicionario de
# dados do certificado. Lista branca: um dicionario que viesse com "is_active"
# ou "code" dentro nao encontraria eco aqui.
CAMPOS_DO_CERTIFICADO = (
    "certificate_display_name",
    "certificate_course_dates_text",
    "certificate_location",
    "certificate_workload_hours",
    "certificate_year",
)


# ---------------------------------------------------------------------------
# Modulos
# ---------------------------------------------------------------------------


def verificar_codigo_disponivel(code, *, ignorando=None):
    """
    Garante unicidade do codigo ignorando maiusculas e minusculas.

    O banco tambem protege, por indice funcional. Esta checagem existe para
    devolver uma mensagem legivel em vez de um IntegrityError.
    """
    code = normalizar_codigo(code)
    if not code:
        raise DomainError("O codigo do modulo e obrigatorio.")

    consulta = Module.objects.filter(code=code)
    if ignorando is not None:
        consulta = consulta.exclude(pk=ignorando.pk)

    if consulta.exists():
        raise DomainError("Ja existe um modulo com o codigo {}.".format(code))
    return code


def normalizar_dados_do_certificado(dados):
    """
    Filtra e valida o dicionario de dados do certificado.

    Aceita somente os nomes da lista branca; qualquer outra chave e ignorada
    em silencio, porque nao existe caso legitimo em que ela apareceria.

    A validacao de carga horaria e ano se repete aqui, no formulario, nos
    validators do modelo e em CheckConstraint. Nao e redundancia por
    desconfianca: cada camada cobre um caminho diferente de escrita, e o valor
    errado so aparece depois de o certificado estar impresso.
    """
    dados = dados or {}
    limpos = {}

    for campo in CAMPOS_DO_CERTIFICADO:
        if campo not in dados:
            continue
        valor = dados[campo]
        if campo in ("certificate_workload_hours", "certificate_year"):
            limpos[campo] = valor if valor not in ("", None) else None
        else:
            limpos[campo] = (valor or "").strip()

    horas = limpos.get("certificate_workload_hours")
    if horas is not None and int(horas) < 1:
        raise DomainError("A carga horaria do certificado precisa ser maior que zero.")

    ano = limpos.get("certificate_year")
    if ano is not None and not (
        ANO_MINIMO_DO_CERTIFICADO <= int(ano) <= ANO_MAXIMO_DO_CERTIFICADO
    ):
        raise DomainError(
            "O ano do certificado precisa estar entre {} e {}.".format(
                ANO_MINIMO_DO_CERTIFICADO, ANO_MAXIMO_DO_CERTIFICADO
            )
        )
    return limpos


@transaction.atomic
def create_module(
    *,
    name,
    code,
    description="",
    order=0,
    is_active=True,
    dados_do_certificado=None,
    certificate_template=None,
    actor=None,
    request=None,
):
    code = verificar_codigo_disponivel(code)
    name = (name or "").strip()
    if not name:
        raise DomainError("O nome do modulo e obrigatorio.")
    if order is None or order < 0:
        raise DomainError("A ordem precisa ser um numero maior ou igual a zero.")

    certificado = normalizar_dados_do_certificado(dados_do_certificado)

    modulo = Module.objects.create(
        name=name,
        code=code,
        description=description or "",
        order=order,
        is_active=is_active,
        # A instancia vem do formulario, que ja limitou o queryset aos ativos.
        # O servico nao aceita id solto: um id vindo do POST poderia apontar
        # para um modelo arquivado ou em rascunho.
        certificate_template=certificate_template,
        **certificado,
    )

    record(
        AuditEvent.MODULE_CREATED,
        request=request,
        actor=actor,
        entity_type="Module",
        entity_id=modulo.pk,
        metadata={"code": modulo.code},
    )
    return modulo


@transaction.atomic
def update_module(
    modulo,
    *,
    name,
    code,
    description="",
    order=0,
    is_active=True,
    dados_do_certificado=None,
    certificate_template=None,
    actor=None,
    request=None,
):
    code = verificar_codigo_disponivel(code, ignorando=modulo)
    name = (name or "").strip()
    if not name:
        raise DomainError("O nome do modulo e obrigatorio.")
    if order is None or order < 0:
        raise DomainError("A ordem precisa ser um numero maior ou igual a zero.")

    certificado = normalizar_dados_do_certificado(dados_do_certificado)

    novos = {
        "name": name,
        "code": code,
        "description": description or "",
        "order": order,
        "is_active": is_active,
        "certificate_template": certificate_template,
        **certificado,
    }
    alterados = campos_alterados(modulo, novos)

    for campo, valor in novos.items():
        setattr(modulo, campo, valor)
    modulo.save(update_fields=[*novos.keys(), "updated_at"])

    if alterados:
        # Somente a lista de campos alterados. Copiar o objeto inteiro para a
        # trilha nao acrescentaria rastreabilidade util.
        record(
            AuditEvent.MODULE_UPDATED,
            request=request,
            actor=actor,
            entity_type="Module",
            entity_id=modulo.pk,
            metadata={"code": modulo.code, "changed_fields": sorted(alterados)},
        )
    return modulo


def disable_module(modulo, *, actor=None, request=None):
    """
    Desativa o modulo sem excluir nada.

    As matriculas permanecem intactas; apenas deixam de dar acesso, porque o
    criterio de liberacao exige module.is_active. Exclusao fisica de modulo
    nao e operacao de rotina e nao esta exposta na interface.
    """
    if not modulo.is_active:
        return modulo

    modulo.is_active = False
    modulo.save(update_fields=["is_active", "updated_at"])

    record(
        AuditEvent.MODULE_DISABLED,
        request=request,
        actor=actor,
        entity_type="Module",
        entity_id=modulo.pk,
        metadata={"code": modulo.code},
    )
    return modulo


def enable_module(modulo, *, actor=None, request=None):
    if modulo.is_active:
        return modulo

    modulo.is_active = True
    modulo.save(update_fields=["is_active", "updated_at"])

    record(
        AuditEvent.MODULE_ENABLED,
        request=request,
        actor=actor,
        entity_type="Module",
        entity_id=modulo.pk,
        metadata={"code": modulo.code},
    )
    return modulo


# ---------------------------------------------------------------------------
# Matriculas
# ---------------------------------------------------------------------------


def validar_aluno(student):
    """
    Recusa qualquer usuario que nao seja aluno.

    O PostgreSQL nao consegue expressar esta regra em constraint, porque ela
    depende do papel gravado em outra tabela. Este e o ponto onde a garantia
    de fato acontece, e por isso todo caminho de criacao de matricula passa
    obrigatoriamente por aqui.
    """
    if student is None:
        raise DomainError("Selecione um aluno.")
    if student.role != UserRole.STUDENT:
        raise DomainError("Somente usuarios com papel ALUNO podem ser matriculados.")
    return student


@transaction.atomic
def create_enrollment(*, student, module, notes="", actor=None, request=None, auditar=True):
    """
    Matricula um aluno em um modulo.

    Nunca cria uma segunda linha para o mesmo par aluno/modulo. Se ja existir
    uma matricula inativa, a operacao e recusada com orientacao explicita:
    reativar e uma decisao administrativa consciente, nao um efeito colateral
    de tentar matricular de novo.
    """
    validar_aluno(student)

    if module is None:
        raise DomainError("Selecione um modulo.")
    if not module.is_active:
        raise DomainError(
            "O modulo {} esta inativo e nao aceita novas matriculas.".format(module.code)
        )

    existente = Enrollment.objects.filter(student=student, module=module).first()
    if existente is not None:
        if existente.status == EnrollmentStatus.ACTIVE:
            raise DomainError(
                "{} ja esta matriculado em {}.".format(student.full_name, module.code)
            )
        # A orientacao muda com o status: uma matricula revogada nao volta
        # pelo "Reativar", e mandar o administrador para o botao errado o faria
        # bater numa recusa sem entender o motivo.
        acao = (
            "restaurar matricula"
            if existente.status == EnrollmentStatus.REVOKED
            else "reativar"
        )
        raise DomainError(
            "{} ja possui uma matricula {} em {}. Use a acao de {}.".format(
                student.full_name,
                existente.get_status_display().lower(),
                module.code,
                acao,
            )
        )

    matricula = Enrollment.objects.create(
        student=student,
        module=module,
        status=EnrollmentStatus.ACTIVE,
        access_enabled=True,
        notes=notes or "",
    )

    if auditar:
        record(
            AuditEvent.ENROLLMENT_CREATED,
            request=request,
            actor=actor,
            student=student,
            entity_type="Enrollment",
            entity_id=matricula.pk,
            metadata={"module_code": module.code},
        )
    return matricula


def _registrar(matricula, evento, *, actor, request):
    record(
        evento,
        request=request,
        actor=actor,
        student=matricula.student,
        entity_type="Enrollment",
        entity_id=matricula.pk,
        metadata={"module_code": matricula.module.code},
    )


def disable_enrollment(matricula, *, actor=None, request=None):
    """
    Desativa a matricula preservando o historico.

    "Remover matricula" na interface resulta nisto, e nao em DELETE fisico:
    apagar a linha destruiria o registro de que o aluno esteve matriculado, o
    que sera relevante quando existirem tentativas e notas.
    """
    if (
        matricula.status == EnrollmentStatus.INACTIVE
        and not matricula.access_enabled
    ):
        return matricula

    matricula.status = EnrollmentStatus.INACTIVE
    matricula.access_enabled = False
    matricula.save(update_fields=["status", "access_enabled", "updated_at"])

    _registrar(matricula, AuditEvent.ENROLLMENT_REMOVED, actor=actor, request=request)
    return matricula


def reactivate_enrollment(matricula, *, actor=None, request=None):
    """
    Devolve a matricula ao estado ativo e com acesso liberado.

    NAO serve para matricula revogada, e a recusa e o ponto central desta
    funcao desde a Etapa 9.

    Se ela aceitasse REVOKED, o botao generico "Reativar" desfaria um ato
    administrativo formal sem motivo, sem trilha propria e — o que importa
    mais — sem a checagem de certificado ativo que restore_revoked_enrollment
    faz. Um aluno ja certificado voltaria ao curso por um clique de rotina.
    """
    # Lido do banco, e nao do objeto recebido. Entre a montagem da tela e este
    # POST alguem pode ter revogado a matricula, e a instancia em memoria
    # ainda diria ACTIVE — que e exatamente o caminho pelo qual a revogacao
    # seria desfeita sem passar por restore_revoked_enrollment.
    status_atual = (
        Enrollment.objects.filter(pk=matricula.pk)
        .values_list("status", flat=True)
        .first()
    )
    if status_atual == EnrollmentStatus.REVOKED:
        raise DomainError(
            "Esta matricula foi revogada. Use a acao de restaurar matricula, "
            "que confere se existe certificado ativo antes de devolver o "
            "acesso."
        )

    validar_aluno(matricula.student)

    if not matricula.module.is_active:
        raise DomainError(
            "O modulo {} esta inativo. Ative o modulo antes de reativar a "
            "matricula.".format(matricula.module.code)
        )

    if matricula.status == EnrollmentStatus.ACTIVE and matricula.access_enabled:
        return matricula

    matricula.status = EnrollmentStatus.ACTIVE
    matricula.access_enabled = True
    matricula.save(update_fields=["status", "access_enabled", "updated_at"])

    _registrar(
        matricula, AuditEvent.ENROLLMENT_REACTIVATED, actor=actor, request=request
    )
    return matricula


def block_enrollment_access(matricula, *, actor=None, request=None):
    """
    Suspende o acesso sem tocar na situacao academica.

    E o bloqueio operacional do dia da prova: o aluno continua matriculado,
    apenas nao enxerga o modulo agora.
    """
    if not matricula.access_enabled:
        return matricula

    matricula.access_enabled = False
    matricula.save(update_fields=["access_enabled", "updated_at"])

    _registrar(matricula, AuditEvent.ENROLLMENT_BLOCKED, actor=actor, request=request)
    return matricula


def unblock_enrollment_access(matricula, *, actor=None, request=None):
    if matricula.access_enabled:
        return matricula

    matricula.access_enabled = True
    matricula.save(update_fields=["access_enabled", "updated_at"])

    _registrar(matricula, AuditEvent.ENROLLMENT_UNBLOCKED, actor=actor, request=request)
    return matricula


def complete_enrollment(
    matricula, *, encerrar_acesso=False, actor=None, request=None
):
    """
    Marca a matricula como concluida.

    Dois chamadores, com necessidades diferentes:

        administrador marcando "concluida" na ficha
            encerrar_acesso=False — muda so a situacao academica, e o
            bloqueio operacional continua sendo decisao separada

        emissao de certificado (Etapa 6)
            encerrar_acesso=True — concluir o modulo e receber o documento
            encerra tambem o acesso academico aquele modulo

    O padrao preserva o comportamento anterior de proposito: quem ja chamava
    esta funcao nao passa a cortar acesso sem ter pedido.

    Idempotente, e a idempotencia olha os dois campos. Uma matricula ja
    COMPLETED mas com acesso liberado ainda precisa ser fechada quando o
    chamador pede o encerramento.
    """
    concluida = matricula.status == EnrollmentStatus.COMPLETED
    acesso_ja_encerrado = not matricula.access_enabled

    if concluida and (not encerrar_acesso or acesso_ja_encerrado):
        return matricula

    campos = ["updated_at"]
    if not concluida:
        matricula.status = EnrollmentStatus.COMPLETED
        campos.append("status")
    if encerrar_acesso and not acesso_ja_encerrado:
        matricula.access_enabled = False
        campos.append("access_enabled")

    matricula.save(update_fields=campos)

    # O evento marca a conclusao academica. Fechar o acesso de uma matricula
    # que ja estava concluida nao e uma segunda conclusao.
    if not concluida:
        _registrar(
            matricula, AuditEvent.ENROLLMENT_COMPLETED, actor=actor, request=request
        )
    return matricula


# ---------------------------------------------------------------------------
# Revogacao, exclusao e restauracao (Etapa 9)
#
# Tres operacoes que a Etapa 2 nao tinha, e a diferenca entre elas e o que
# este bloco existe para manter clara:
#
#   desativar   pausa operacional. Reversivel com um clique, sem motivo.
#   revogar     ato administrativo. Exige motivo, grava autor e data, e tira
#               a matricula da lista padrao. O historico fica.
#   excluir     apaga a linha. So quando NAO existe historico academico
#               nenhum daquele aluno naquele modulo.
#
# Nenhuma das tres toca em certificado. Um certificado ACTIVE com a matricula
# REVOKED e um estado legitimo: o aluno concluiu e tem o documento, e o
# vinculo foi encerrado depois. Revogar o documento e outro ato, com outro
# fluxo e outro evento.
# ---------------------------------------------------------------------------


class MatriculaJaRevogada(DomainError):
    """A matricula ja esta revogada; nada a fazer."""


class MatriculaNaoRevogada(DomainError):
    """A matricula nao esta revogada; nada a restaurar."""


def tem_certificado_ativo(matricula):
    """
    Se resta certificado ACTIVE deste aluno neste modulo.

    Import local: courses nao depende de certificates em tempo de importacao,
    e inverter isso criaria um ciclo — certificates ja importa courses.
    """
    from certificates.models import Certificate, CertificateStatus

    return Certificate.objects.filter(
        attempt__student=matricula.student,
        attempt__exam__module=matricula.module,
        status=CertificateStatus.ACTIVE,
    ).exists()


def revoke_enrollment(matricula, *, actor=None, reason="", request=None):
    """
    Revoga a matricula: encerra o vinculo academico e o acesso.

    Preserva tudo que ja aconteceu. Tentativas, notas e certificados continuam
    exatamente onde estavam — revogar responde "este aluno nao cursa mais este
    modulo", e nao "este aluno nunca cursou".

    O acesso cai junto e nao e opcional: a constraint
    matricula_revogada_sem_acesso recusa a linha se alguem tentar gravar
    REVOKED com acesso liberado.

    A matricula e travada antes de qualquer escrita. Se o aluno estiver
    iniciando uma prova no mesmo instante, um dos dois chega primeiro: ou o
    start le a matricula ainda liberada e cria a tentativa, ou ele espera esta
    transacao e encontra a matricula ja revogada. O que nao pode acontecer e
    acesso ativo DEPOIS que a revogacao foi confirmada, e e isso que o lock
    garante.
    """
    motivo = validar_motivo(reason, vazio="Informe o motivo da revogacao.")
    agora = timezone.now()

    with transaction.atomic():
        travada = (
            Enrollment.objects.select_for_update()
            .select_related("student", "module")
            .get(pk=matricula.pk)
        )

        if travada.status == EnrollmentStatus.REVOKED:
            # Nao e sucesso silencioso: quem clicou duas vezes precisa saber
            # que a segunda nao fez nada. A view transforma isto em 409.
            raise MatriculaJaRevogada("Esta matricula ja foi revogada.")

        travada.status = EnrollmentStatus.REVOKED
        travada.access_enabled = False
        travada.revoked_at = agora
        travada.revoked_by = actor if getattr(actor, "pk", None) else None
        travada.revocation_reason = motivo
        travada.save(
            update_fields=[
                "status",
                "access_enabled",
                "revoked_at",
                "revoked_by",
                "revocation_reason",
                "updated_at",
            ]
        )

        # O motivo NAO entra na metadata: ja esta em revocation_reason, e
        # duplicar texto livre cria duas versoes do mesmo fato.
        _registrar(travada, AuditEvent.ENROLLMENT_REVOKED, actor=actor, request=request)

    return travada


def can_delete_enrollment(matricula):
    """
    Impedimentos para apagar a matricula. Lista vazia significa que pode.

    Historico academico e qualquer tentativa daquele aluno em qualquer prova
    daquele modulo, em qualquer situacao — inclusive RESET. Uma tentativa
    anulada continua sendo o registro de que o aluno esteve ali.

    Certificados sao contados a parte por clareza. Na pratica sao redundantes
    (Certificate.attempt e OneToOne com PROTECT, entao nao ha certificado sem
    tentativa), mas a redundancia sobrevive a uma mudanca futura de desenho.
    """
    from certificates.models import Certificate
    from exams.models import ExamAttempt

    impedimentos = []

    tentativas = ExamAttempt.objects.filter(
        student=matricula.student, exam__module=matricula.module
    ).count()
    if tentativas:
        impedimentos.append(
            "Este aluno possui {} tentativa(s) neste modulo. O historico "
            "academico nao pode ser apagado.".format(tentativas)
        )

    certificados = Certificate.objects.filter(
        attempt__student=matricula.student, attempt__exam__module=matricula.module
    ).count()
    if certificados:
        impedimentos.append(
            "Este aluno possui {} certificado(s) neste modulo.".format(certificados)
        )

    return impedimentos


def delete_enrollment(matricula, *, actor=None, request=None):
    """
    Apaga a matricula fisicamente, se nao houver historico academico.

    O evento entra na trilha ANTES do DELETE, na mesma transacao: se a
    exclusao falhar, o rollback leva o evento junto e a trilha nunca afirma
    uma exclusao que nao aconteceu.

    Isto NAO substitui disable_enrollment. A Etapa 2 decidiu que "remover
    matricula" na interface resulta em desativacao, e continua assim. Esta
    funcao existe para o caso estreito de uma matricula criada por engano,
    que nunca produziu nada.
    """
    with transaction.atomic():
        travada = (
            Enrollment.objects.select_for_update()
            .select_related("student", "module")
            .get(pk=matricula.pk)
        )

        # Reconferido DEPOIS do lock: entre a tela e este POST o aluno pode
        # ter comecado uma prova.
        impedimentos = can_delete_enrollment(travada)
        if impedimentos:
            raise DomainError(impedimentos)

        record(
            AuditEvent.ENROLLMENT_DELETED,
            request=request,
            actor=actor,
            student=travada.student,
            entity_type="Enrollment",
            entity_id=travada.pk,
            metadata={
                "module_code": travada.module.code,
                "previous_status": travada.status,
            },
        )

        travada.delete()

    return True


def restore_revoked_enrollment(matricula, *, actor=None, request=None):
    """
    Devolve uma matricula revogada ao estado ativo.

    Acao administrativa explicita, e nao o "Reativar" generico: reativar serve
    para uma matricula pausada, e usar o mesmo botao para as duas coisas
    esconderia que uma delas esta desfazendo um ato formal.

    Recusa quando existe certificado ACTIVE do aluno naquele modulo. O
    documento afirma que ele concluiu; devolve-lo ao curso contradiria o que a
    instituicao ja assinou. O caminho, se for mesmo o caso, e revogar o
    certificado primeiro — que e uma decisao consciente, com trilha propria.
    """
    with transaction.atomic():
        travada = (
            Enrollment.objects.select_for_update()
            .select_related("student", "module")
            .get(pk=matricula.pk)
        )

        if travada.status != EnrollmentStatus.REVOKED:
            raise MatriculaNaoRevogada("Esta matricula nao esta revogada.")

        validar_aluno(travada.student)

        if not travada.module.is_active:
            raise DomainError(
                "O modulo {} esta inativo. Ative o modulo antes de restaurar "
                "a matricula.".format(travada.module.code)
            )

        if tem_certificado_ativo(travada):
            raise DomainError(
                "Esta matricula possui certificado ativo de conclusao. "
                "Revogue o certificado antes de restaurar acesso academico."
            )

        travada.status = EnrollmentStatus.ACTIVE
        travada.access_enabled = True
        travada.revoked_at = None
        travada.revoked_by = None
        travada.revocation_reason = ""
        travada.save(
            update_fields=[
                "status",
                "access_enabled",
                "revoked_at",
                "revoked_by",
                "revocation_reason",
                "updated_at",
            ]
        )

        _registrar(
            travada, AuditEvent.ENROLLMENT_RESTORED, actor=actor, request=request
        )

    return travada


# ---------------------------------------------------------------------------
# Consultas do aluno
# ---------------------------------------------------------------------------


def modulos_do_aluno(user):
    """
    Modulos que o aluno pode efetivamente acessar agora.

    Unico criterio usado pela area do aluno. Manter isso em um lugar so
    impede que uma tela futura implemente a regra pela metade e passe a
    exibir modulo bloqueado ou inativo.
    """
    return (
        Module.objects.filter(
            enrollments__student=user,
            enrollments__status=EnrollmentStatus.ACTIVE,
            enrollments__access_enabled=True,
            is_active=True,
        )
        .distinct()
        .order_by("order", "name")
    )


def matricula_liberada_ou_none(user, module_id):
    """
    Matricula liberada do aluno para um modulo, ou None.

    Base do controle de IDOR: a view devolve 404 quando isto retorna None, de
    modo que o aluno nao consegue nem confirmar que o modulo existe.
    """
    return (
        Enrollment.objects.select_related("module")
        .liberadas()
        .filter(student=user, module_id=module_id)
        .first()
    )
