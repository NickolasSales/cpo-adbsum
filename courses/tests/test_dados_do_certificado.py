"""
Dados de certificado no modulo.

Sao cinco campos administrativos que existem por um motivo so: sair impressos
no documento. O sistema nao tem como deduzi-los — nao sabe em que dia a turma
se reuniu nem quantas horas durou —, e por isso eles precisam ser pedidos, ter
validacao propria e bloquear a emissao enquanto faltarem.

O tema deste arquivo e a fronteira entre "vazio porque ninguem preencheu" e
"invalido". Vazio e um estado legitimo: todo modulo criado antes da Etapa 8
esta assim, e a migration nao inventou valores. Invalido — ano 202, carga
horaria zero — e o que nunca pode chegar ao banco.
"""

import pytest
from django.db.utils import IntegrityError
from django.urls import reverse

from common.exceptions import DomainError
from courses.forms import ModuleForm
from courses.models import Module
from courses.services import create_module, update_module

pytestmark = pytest.mark.django_db


DADOS_BASE = {
    "name": "Modulo Novo",
    "code": "MODX",
    "description": "",
    "order": 1,
    "is_active": True,
}

CERTIFICADO_COMPLETO = {
    "certificate_display_name": "Modulo X - Auxiliares",
    "certificate_course_dates_text": "5 e 12 de marco de 2027",
    "certificate_location": "Igreja Sede",
    "certificate_workload_hours": 12,
    "certificate_year": 2027,
}


# ---------------------------------------------------------------------------
# O que falta, e o que falta dizer
# ---------------------------------------------------------------------------


def test_modulo_novo_nasce_sem_dados_de_certificado():
    modulo = create_module(**DADOS_BASE)

    assert modulo.certificate_course_dates_text == ""
    assert modulo.certificate_location == ""
    assert modulo.certificate_workload_hours is None
    assert modulo.certificate_year is None
    assert modulo.pronto_para_certificar is False


def test_a_lista_de_ausentes_nomeia_os_quatro_obrigatorios(
    modulo_sem_dados_de_certificado,
):
    faltando = modulo_sem_dados_de_certificado.dados_do_certificado_ausentes()

    assert set(faltando) == {"data(s) do curso", "local", "carga horaria", "ano"}


def test_o_nome_exibido_nao_entra_na_lista_de_ausentes():
    """
    Dos cinco campos, so o nome exibido tem substituto natural.

    Certificado com nome curto e melhor do que nenhum certificado; certificado
    sem data nao e.
    """
    modulo = create_module(**DADOS_BASE, dados_do_certificado=CERTIFICADO_COMPLETO)
    modulo.certificate_display_name = ""
    modulo.save()

    assert modulo.dados_do_certificado_ausentes() == []
    assert modulo.pronto_para_certificar is True
    assert modulo.nome_no_certificado == modulo.name


def test_modulo_completo_esta_pronto():
    modulo = create_module(**DADOS_BASE, dados_do_certificado=CERTIFICADO_COMPLETO)

    assert modulo.pronto_para_certificar is True
    assert modulo.nome_no_certificado == "Modulo X - Auxiliares"


def test_espaco_em_branco_nao_conta_como_preenchido():
    modulo = create_module(
        **DADOS_BASE,
        dados_do_certificado={**CERTIFICADO_COMPLETO, "certificate_location": "   "},
    )

    assert modulo.certificate_location == ""
    assert "local" in modulo.dados_do_certificado_ausentes()


# ---------------------------------------------------------------------------
# Validacao no servico
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("horas", [0, -1])
def test_carga_horaria_precisa_ser_maior_que_zero(horas):
    with pytest.raises(DomainError):
        create_module(
            **DADOS_BASE,
            dados_do_certificado={
                **CERTIFICADO_COMPLETO,
                "certificate_workload_hours": horas,
            },
        )


@pytest.mark.parametrize("ano", [202, 1999, 2101, 20260])
def test_ano_fora_da_faixa_e_recusado(ano):
    """
    Nao e regra de negocio profunda: e cerco contra erro de digitacao.

    Um certificado datado de 202 so seria notado depois de impresso e
    entregue, e ai nao ha correcao possivel no papel que ja saiu.
    """
    with pytest.raises(DomainError):
        create_module(
            **DADOS_BASE,
            dados_do_certificado={**CERTIFICADO_COMPLETO, "certificate_year": ano},
        )


@pytest.mark.parametrize("ano", [2000, 2026, 2100])
def test_ano_dentro_da_faixa_e_aceito(ano):
    modulo = create_module(
        **DADOS_BASE,
        dados_do_certificado={**CERTIFICADO_COMPLETO, "certificate_year": ano},
    )

    assert modulo.certificate_year == ano


def test_o_servico_ignora_chave_que_nao_e_do_certificado(modulo):
    """
    Lista branca no dicionario.

    Um dicionario que chegasse com "is_active" ou "code" dentro nao encontra
    eco: o servico so le os cinco nomes que conhece.
    """
    update_module(
        modulo,
        name=modulo.name,
        code=modulo.code,
        order=modulo.order,
        is_active=True,
        dados_do_certificado={
            "certificate_year": 2030,
            "is_active": False,
            "code": "INVADIDO",
            "certificate_display_name": "Novo Nome",
        },
    )
    modulo.refresh_from_db()

    assert modulo.certificate_year == 2030
    assert modulo.certificate_display_name == "Novo Nome"
    assert modulo.is_active is True
    assert modulo.code != "INVADIDO"


def test_dados_ausentes_no_dicionario_nao_apagam_o_que_ja_existe(modulo):
    """
    update_module escreve so o que recebeu.

    Uma tela futura que edite apenas o nome do modulo nao pode zerar a carga
    horaria por omissao.
    """
    antes = modulo.certificate_workload_hours

    update_module(
        modulo,
        name="Outro nome",
        code=modulo.code,
        order=modulo.order,
        is_active=True,
        dados_do_certificado={"certificate_year": 2031},
    )
    modulo.refresh_from_db()

    assert modulo.certificate_year == 2031
    assert modulo.certificate_workload_hours == antes


# ---------------------------------------------------------------------------
# Validacao no banco
# ---------------------------------------------------------------------------


def test_o_banco_recusa_carga_horaria_zero():
    """
    A constraint cobre o caminho que nao passa por servico nem formulario:
    update() em queryset, shell, SQL direto.
    """
    modulo = create_module(**DADOS_BASE, dados_do_certificado=CERTIFICADO_COMPLETO)

    with pytest.raises(IntegrityError):
        Module.objects.filter(pk=modulo.pk).update(certificate_workload_hours=0)


def test_o_banco_recusa_ano_implausivel():
    modulo = create_module(
        **{**DADOS_BASE, "code": "MODY"},
        dados_do_certificado=CERTIFICADO_COMPLETO,
    )

    with pytest.raises(IntegrityError):
        Module.objects.filter(pk=modulo.pk).update(certificate_year=1500)


def test_o_banco_aceita_nulo_nos_dois_campos():
    """
    Nulo e o estado dos modulos anteriores a Etapa 8, e precisa continuar
    valido: a alternativa seria uma data migration inventando valores
    historicos que ninguem informou.
    """
    modulo = create_module(**{**DADOS_BASE, "code": "MODZ"})

    Module.objects.filter(pk=modulo.pk).update(
        certificate_workload_hours=None, certificate_year=None
    )
    modulo.refresh_from_db()

    assert modulo.certificate_workload_hours is None
    assert modulo.certificate_year is None


# ---------------------------------------------------------------------------
# Formulario
# ---------------------------------------------------------------------------


def test_o_formulario_declara_os_cinco_campos():
    form = ModuleForm()

    for campo in (
        "certificate_display_name",
        "certificate_course_dates_text",
        "certificate_location",
        "certificate_workload_hours",
        "certificate_year",
    ):
        assert campo in form.fields


def test_o_formulario_aceita_os_cinco_vazios():
    """
    Cadastrar o modulo e preencher o certificado sao dois momentos: o modulo
    costuma existir antes de a turma acontecer.
    """
    form = ModuleForm(data={**DADOS_BASE, "code": "MODW"})

    assert form.is_valid(), form.errors


@pytest.mark.parametrize("horas", ["0", "-3"])
def test_o_formulario_recusa_carga_horaria_invalida(horas):
    form = ModuleForm(
        data={**DADOS_BASE, "code": "MODV", "certificate_workload_hours": horas}
    )

    assert not form.is_valid()
    assert "certificate_workload_hours" in form.errors


@pytest.mark.parametrize("ano", ["202", "1999", "2101"])
def test_o_formulario_recusa_ano_fora_da_faixa(ano):
    form = ModuleForm(data={**DADOS_BASE, "code": "MODU", "certificate_year": ano})

    assert not form.is_valid()
    assert "certificate_year" in form.errors


def test_a_validacao_nao_depende_de_javascript(admin_client_logado):
    """
    §18. Os atributos min e max ajudam quem digita; nao sao a defesa.

    Este POST chega direto, sem passar por navegador nenhum.
    """
    resposta = admin_client_logado.post(
        reverse("admin_panel:module_create"),
        {**DADOS_BASE, "code": "MODT", "certificate_year": "3050",
         "certificate_workload_hours": "0"},
    )

    assert resposta.status_code == 200
    assert Module.objects.filter(code="MODT").count() == 0


# ---------------------------------------------------------------------------
# Telas
# ---------------------------------------------------------------------------


def test_a_tela_de_edicao_mostra_a_secao_do_certificado(
    admin_client_logado, modulo
):
    corpo = admin_client_logado.get(
        reverse("admin_panel:module_update", kwargs={"pk": modulo.pk})
    ).content.decode("utf-8")

    assert "Dados do certificado" in corpo
    assert "Carga horaria" in corpo
    assert 'name="certificate_year"' in corpo


def test_o_admin_grava_os_dados_pela_tela(admin_client_logado, modulo):
    admin_client_logado.post(
        reverse("admin_panel:module_update", kwargs={"pk": modulo.pk}),
        {
            "name": modulo.name,
            "code": modulo.code,
            "description": "",
            "order": modulo.order,
            "is_active": "on",
            "certificate_display_name": "Modulo I - Cooperadores",
            "certificate_course_dates_text": "1 de junho de 2027",
            "certificate_location": "Congregacao Central",
            "certificate_workload_hours": "16",
            "certificate_year": "2027",
        },
    )
    modulo.refresh_from_db()

    assert modulo.certificate_display_name == "Modulo I - Cooperadores"
    assert modulo.certificate_course_dates_text == "1 de junho de 2027"
    assert modulo.certificate_location == "Congregacao Central"
    assert modulo.certificate_workload_hours == 16
    assert modulo.certificate_year == 2027


def test_o_detalhe_avisa_quando_falta_dado(
    admin_client_logado, modulo_sem_dados_de_certificado
):
    """
    Quem chega aqui depois de uma emissao recusada precisa entender o motivo
    sem procurar.
    """
    corpo = admin_client_logado.get(
        reverse(
            "admin_panel:module_detail",
            kwargs={"pk": modulo_sem_dados_de_certificado.pk},
        )
    ).content.decode("utf-8")

    assert "Incompleto" in corpo
    assert "carga horaria" in corpo
    assert "ano" in corpo


def test_o_detalhe_de_modulo_completo_nao_avisa(admin_client_logado, modulo):
    corpo = admin_client_logado.get(
        reverse("admin_panel:module_detail", kwargs={"pk": modulo.pk})
    ).content.decode("utf-8")

    assert "Completo" in corpo
    assert "Nenhum certificado deste modulo pode ser emitido" not in corpo
