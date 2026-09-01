"""
Servicos de dominio de modulos e matriculas.

As views nao criam nem alteram Module ou Enrollment diretamente. Toda regra
— normalizacao de codigo, recusa de ADMIN como aluno, tratamento de matricula
existente — vive aqui, em um unico ponto de execucao.
"""

from django.db import transaction

from accounts.models import UserRole
from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError, campos_alterados
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
        raise DomainError(
            "{} ja possui uma matricula {} em {}. Use a acao de reativar.".format(
                student.full_name,
                existente.get_status_display().lower(),
                module.code,
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
    """Devolve a matricula ao estado ativo e com acesso liberado."""
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
