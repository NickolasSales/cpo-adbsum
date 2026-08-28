"""Testes do modelo Module e dos servicos de modulo."""

import pytest
from django.db import IntegrityError, transaction

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from courses.models import Enrollment, EnrollmentStatus, Module
from courses.services import (
    create_module,
    disable_module,
    enable_module,
    update_module,
)

pytestmark = pytest.mark.django_db


def logs(evento, modulo=None):
    consulta = AuditLog.objects.filter(event=evento, entity_type="Module")
    if modulo is not None:
        consulta = consulta.filter(entity_id=str(modulo.pk))
    return consulta


# ---------------------------------------------------------------------------
# Criacao
# ---------------------------------------------------------------------------


def test_create_module_grava_todos_os_campos(admin_user):
    modulo = create_module(
        name="Fundamentos",
        code="FUND",
        description="Modulo introdutorio",
        order=3,
        actor=admin_user,
    )

    modulo.refresh_from_db()
    assert modulo.name == "Fundamentos"
    assert modulo.code == "FUND"
    assert modulo.description == "Modulo introdutorio"
    assert modulo.order == 3
    assert modulo.is_active is True
    assert str(modulo) == "FUND - Fundamentos"


def test_create_module_gera_evento_de_auditoria(admin_user):
    modulo = create_module(name="Fundamentos", code="FUND", actor=admin_user)

    log = logs(AuditEvent.MODULE_CREATED, modulo).get()
    assert log.actor_id == admin_user.pk
    assert log.entity_id == str(modulo.pk)
    assert log.metadata["code"] == "FUND"


def test_create_module_normaliza_o_codigo():
    modulo = create_module(name="Modulo 5", code=" mod5 ")

    assert modulo.code == "MOD5"
    assert Module.objects.filter(code="MOD5").exists()


def test_save_do_modelo_tambem_normaliza_o_codigo():
    # A normalizacao vive no save(), nao apenas no servico: quem cria pelo ORM
    # ou pelo Django Admin nao pode acabar gravando o codigo em caixa baixa.
    modulo = Module.objects.create(name="  Modulo 7  ", code=" mod7 ", order=7)

    modulo.refresh_from_db()
    assert modulo.code == "MOD7"
    assert modulo.name == "Modulo 7"


def test_create_module_exige_codigo():
    with pytest.raises(DomainError):
        create_module(name="Sem Codigo", code="   ")

    assert Module.objects.count() == 0


def test_create_module_exige_nome():
    with pytest.raises(DomainError):
        create_module(name="   ", code="MOD3")

    assert Module.objects.count() == 0


def test_create_module_recusa_codigo_duplicado(modulo):
    with pytest.raises(DomainError):
        create_module(name="Outro nome", code="MOD1")

    assert Module.objects.count() == 1


def test_create_module_recusa_codigo_duplicado_em_outra_caixa(modulo):
    with pytest.raises(DomainError):
        create_module(name="Outro nome", code="mod1")

    assert Module.objects.count() == 1


def test_banco_recusa_codigo_duplicado_ignorando_caixa(modulo):
    # bulk_create nao passa por save() e portanto nao normaliza nada. Este e o
    # caminho que o indice funcional Upper(code) existe para cobrir.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Module.objects.bulk_create(
                [Module(name="Clone do modulo 1", code="mod1", order=4)]
            )

    assert Module.objects.count() == 1


def test_create_module_recusa_ordem_negativa():
    with pytest.raises(DomainError):
        create_module(name="Modulo negativo", code="NEG", order=-1)

    assert Module.objects.count() == 0


def test_create_module_nao_gera_auditoria_quando_falha():
    with pytest.raises(DomainError):
        create_module(name="Modulo negativo", code="NEG", order=-1)

    assert logs(AuditEvent.MODULE_CREATED).count() == 0


# ---------------------------------------------------------------------------
# Edicao
# ---------------------------------------------------------------------------


def test_update_module_altera_os_campos(modulo, admin_user):
    update_module(
        modulo,
        name="Modulo Um Revisado",
        code="MOD1A",
        description="Nova descricao",
        order=5,
        is_active=True,
        actor=admin_user,
    )

    modulo.refresh_from_db()
    assert modulo.name == "Modulo Um Revisado"
    assert modulo.code == "MOD1A"
    assert modulo.description == "Nova descricao"
    assert modulo.order == 5


def test_update_module_registra_os_campos_alterados(modulo, admin_user):
    update_module(
        modulo,
        name="Modulo Um Revisado",
        code="MOD1",
        description=modulo.description,
        order=modulo.order,
        is_active=modulo.is_active,
        actor=admin_user,
    )

    log = logs(AuditEvent.MODULE_UPDATED, modulo).get()
    assert log.actor_id == admin_user.pk
    assert log.metadata["changed_fields"] == ["name"]
    assert log.metadata["code"] == "MOD1"


def test_update_module_sem_alteracao_nao_gera_auditoria(modulo, admin_user):
    # Reenviar o formulario sem mudar nada nao e um evento auditavel; poluir a
    # trilha com nao-eventos dificulta encontrar as edicoes de verdade.
    update_module(
        modulo,
        name=modulo.name,
        code=modulo.code,
        description=modulo.description,
        order=modulo.order,
        is_active=modulo.is_active,
        actor=admin_user,
    )

    assert logs(AuditEvent.MODULE_UPDATED, modulo).count() == 0


def test_update_module_aceita_o_proprio_codigo_atual(modulo, admin_user):
    # A checagem de unicidade precisa ignorar o proprio registro, senao nenhum
    # modulo poderia ser editado sem tambem trocar de codigo.
    update_module(
        modulo,
        name="Modulo Um",
        code="MOD1",
        description=modulo.description,
        order=modulo.order,
        is_active=modulo.is_active,
        actor=admin_user,
    )

    modulo.refresh_from_db()
    assert modulo.code == "MOD1"
    assert modulo.name == "Modulo Um"


def test_update_module_aceita_o_proprio_codigo_em_outra_caixa(modulo):
    update_module(
        modulo,
        name=modulo.name,
        code="mod1",
        description=modulo.description,
        order=modulo.order,
        is_active=modulo.is_active,
    )

    modulo.refresh_from_db()
    assert modulo.code == "MOD1"


def test_update_module_recusa_codigo_de_outro_modulo(modulo, outro_modulo):
    with pytest.raises(DomainError):
        update_module(
            modulo,
            name=modulo.name,
            code="mod2",
            description=modulo.description,
            order=modulo.order,
            is_active=modulo.is_active,
        )

    modulo.refresh_from_db()
    assert modulo.code == "MOD1"


def test_update_module_exige_nome(modulo):
    with pytest.raises(DomainError):
        update_module(modulo, name="  ", code="MOD1")

    modulo.refresh_from_db()
    assert modulo.name == "Modulo 1"


def test_update_module_recusa_ordem_negativa(modulo):
    with pytest.raises(DomainError):
        update_module(modulo, name="Modulo 1", code="MOD1", order=-1)

    modulo.refresh_from_db()
    assert modulo.order == 1


# ---------------------------------------------------------------------------
# Ativacao e desativacao
# ---------------------------------------------------------------------------


def test_disable_module_desativa_e_audita(modulo, admin_user):
    disable_module(modulo, actor=admin_user)

    modulo.refresh_from_db()
    assert modulo.is_active is False

    log = logs(AuditEvent.MODULE_DISABLED, modulo).get()
    assert log.actor_id == admin_user.pk
    assert log.metadata["code"] == "MOD1"


def test_disable_module_preserva_as_matriculas(modulo, matricula, admin_user):
    # Desativar modulo nunca e exclusao: a matricula permanece intacta e apenas
    # deixa de dar acesso, porque a liberacao tambem exige module.is_active.
    antes = Enrollment.objects.filter(module=modulo).count()

    disable_module(modulo, actor=admin_user)

    depois = Enrollment.objects.filter(module=modulo).count()
    assert antes == 1
    assert depois == 1

    matricula.refresh_from_db()
    assert matricula.status == EnrollmentStatus.ACTIVE
    assert matricula.access_enabled is True
    assert matricula.libera_acesso is False


def test_disable_module_em_modulo_ja_inativo_nao_audita(modulo_inativo, admin_user):
    disable_module(modulo_inativo, actor=admin_user)

    modulo_inativo.refresh_from_db()
    assert modulo_inativo.is_active is False
    assert logs(AuditEvent.MODULE_DISABLED, modulo_inativo).count() == 0


def test_disable_module_chamado_duas_vezes_gera_um_unico_log(modulo, admin_user):
    disable_module(modulo, actor=admin_user)
    disable_module(modulo, actor=admin_user)

    modulo.refresh_from_db()
    assert modulo.is_active is False
    assert logs(AuditEvent.MODULE_DISABLED, modulo).count() == 1


def test_enable_module_ativa_e_audita(modulo_inativo, admin_user):
    enable_module(modulo_inativo, actor=admin_user)

    modulo_inativo.refresh_from_db()
    assert modulo_inativo.is_active is True

    log = logs(AuditEvent.MODULE_ENABLED, modulo_inativo).get()
    assert log.actor_id == admin_user.pk
    assert log.metadata["code"] == "MOD9"


def test_enable_module_chamado_duas_vezes_gera_um_unico_log(modulo_inativo, admin_user):
    enable_module(modulo_inativo, actor=admin_user)
    enable_module(modulo_inativo, actor=admin_user)

    modulo_inativo.refresh_from_db()
    assert modulo_inativo.is_active is True
    assert logs(AuditEvent.MODULE_ENABLED, modulo_inativo).count() == 1


def test_enable_module_em_modulo_ja_ativo_nao_audita(modulo, admin_user):
    enable_module(modulo, actor=admin_user)

    modulo.refresh_from_db()
    assert modulo.is_active is True
    assert logs(AuditEvent.MODULE_ENABLED, modulo).count() == 0


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def test_ordenacao_padrao_e_por_ordem_depois_nome():
    create_module(name="Zebra", code="Z1", order=1)
    create_module(name="Alfa", code="A1", order=1)
    create_module(name="Beta", code="B1", order=0)

    assert [m.name for m in Module.objects.all()] == ["Beta", "Alfa", "Zebra"]


def test_ativos_traz_somente_modulos_ativos(modulo, outro_modulo, modulo_inativo):
    ativos = list(Module.objects.ativos())

    assert ativos == [modulo, outro_modulo]
    assert modulo_inativo not in ativos


def test_ativos_reflete_a_desativacao(modulo, outro_modulo):
    disable_module(modulo)

    assert list(Module.objects.ativos()) == [outro_modulo]
