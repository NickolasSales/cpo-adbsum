"""Importacao da relacao de ministros: leitura, preview, confirmacao."""

import datetime

import pytest

from audit.models import AuditEvent, AuditLog
from common.exceptions import DomainError
from ministers import services
from ministers.importers import RowStatus, analisar, confirmar, ler_arquivo
from ministers.models import (
    Minister,
    MinisterAccount,
    MinisterialTitle,
    MinisterSex,
    MinisterSource,
)
from ministers.tests.conftest import SENHA_PADRAO_MINISTRO, URL_LOGIN, URL_TROCAR_SENHA, dados_de_ministro
from ministers.tests.planilhas import (
    CSV_EXPORTADO_DO_EXCEL,
    LINHAS_EXEMPLO,
    csv_relacao,
    planilha_relacao,
)

pytestmark = pytest.mark.django_db

URL_UPLOAD = "/admin-panel/ministros/importar/"
URL_PREVIEW = "/admin-panel/ministros/importar/preview/"
URL_CONFIRMAR = "/admin-panel/ministros/importar/confirmar/"
URL_CANCELAR = "/admin-panel/ministros/importar/cancelar/"


def contagens():
    return Minister.objects.count(), MinisterAccount.objects.count(), AuditLog.objects.count()


def por_inscricao(analise):
    return {linha.conamad: linha for linha in analise.linhas}


# ---------------------------------------------------------------------------
# Leitura do formato real
# ---------------------------------------------------------------------------


def test_le_a_relacao_no_formato_real():
    lote = ler_arquivo(planilha_relacao())

    assert lote["aba"] == "Ministros Campo Sumaré"
    assert lote["linha_cabecalho"] == 6
    assert [c["campo"] for c in lote["colunas"]] == [
        "sequencia",
        "nome",
        "conamad",
        "titulo",
        "consagracao",
        "nascimento",
        "sexo",
    ]
    assert lote["ignoradas"] == [
        {"cabecalho": "IDADE", "motivo": "calculada a partir do ano de nascimento"}
    ]
    assert len(lote["linhas"]) == len(LINHAS_EXEMPLO)
    primeira = lote["linhas"][0]
    assert primeira["numero"] == 8
    assert primeira["sequencia"] == "1"
    assert primeira["conamad"] == "132901"
    assert primeira["nascimento"] == "9/4/2000"


def test_interpreta_cada_campo_da_relacao():
    linhas = por_inscricao(analisar(ler_arquivo(planilha_relacao())["linhas"]))

    abel = linhas["132901"].dados
    assert abel["full_name"] == "ABEL FICTÍCIO DOS SANTOS"
    assert abel["ministerial_title"] == MinisterialTitle.EVANGELISTA
    assert abel["consecration_year"] == 2025
    assert abel["birth_date"] == datetime.date(2000, 4, 9)

    assert linhas["129102"].dados["ministerial_title"] == MinisterialTitle.PASTOR
    assert linhas["15504"].dados["ministerial_title"] == MinisterialTitle.PASTORA
    assert linhas["15504"].dados["consecration_year"] == 2009  # veio como texto
    assert linhas["129105"].dados["ministerial_title"] == MinisterialTitle.EVANGELISTA  # "Ev"
    assert linhas["132906"].dados["ministerial_title"] == MinisterialTitle.MISSIONARIO


def test_sexo_m_e_ambiguo_e_fica_em_branco_com_aviso():
    linhas = por_inscricao(analisar(ler_arquivo(planilha_relacao())["linhas"]))

    assert linhas["129102"].dados["sex"] == MinisterSex.MASCULINO  # H
    assert linhas["132903"].dados["sex"] == MinisterSex.FEMININO  # F
    assert linhas["132901"].dados["sex"] == ""  # M
    assert "ambíguo" in linhas["132901"].avisos[0]
    assert linhas["132901"].status == RowStatus.NOVO


def test_numero_sequencial_nao_vira_matricula():
    linhas = analisar(ler_arquivo(planilha_relacao())["linhas"]).linhas
    assert all(linha.dados["field_registration"] == "" for linha in linhas)
    assert linhas[0].import_sequence == 1


def test_csv_exportado_pelo_excel():
    lote = ler_arquivo(csv_relacao(CSV_EXPORTADO_DO_EXCEL))
    analise = analisar(lote["linhas"])

    assert lote["linha_cabecalho"] == 5
    assert analise.resumo()["novos"] == 3
    assert por_inscricao(analise)["129102"].dados["birth_date"] == datetime.date(1975, 9, 21)


def test_csv_simples_com_virgula_e_data_em_uma_coluna():
    texto = (
        "Nome,Inscrição Conamad,Matrícula Campo,Titulo,Congregação,Data de Nascimento\n"
        "Ana Exemplo,000123,0042,Pastora,Sede,05/03/1961\n"
    )
    linha = analisar(ler_arquivo(csv_relacao(texto, bom=False))["linhas"]).linhas[0]

    assert linha.status == RowStatus.NOVO
    assert linha.dados["conamad_registration"] == "000123"
    assert linha.dados["field_registration"] == "0042"
    assert linha.dados["congregation"] == "Sede"
    assert linha.dados["birth_date"] == datetime.date(1961, 3, 5)


def test_csv_que_nao_e_utf8_e_recusado():
    from django.core.files.uploadedfile import SimpleUploadedFile

    arquivo = SimpleUploadedFile("r.csv", "Nome;Inscrição Conamad\nJoão;1\n".encode("cp1252"))
    with pytest.raises(DomainError):
        ler_arquivo(arquivo)


def test_arquivo_sem_cabecalho_reconhecivel():
    with pytest.raises(DomainError) as erro:
        ler_arquivo(csv_relacao("a;b;c\n1;2;3\n"))
    assert "Inscrição Conamad" in str(erro.value)


def test_xlsx_corrompido_e_recusado():
    from django.core.files.uploadedfile import SimpleUploadedFile

    with pytest.raises(DomainError):
        ler_arquivo(SimpleUploadedFile("r.xlsx", b"nao e um zip"))


def test_matricula_sozinha_nao_e_adivinhada():
    texto = "Nome;Inscrição Conamad;Titulo;Matrícula\nAna;1;EV;99\n"
    lote = ler_arquivo(csv_relacao(texto))
    assert {"cabecalho": "Matrícula", "motivo": "coluna não reconhecida"} in lote["ignoradas"]
    assert analisar(lote["linhas"]).linhas[0].dados["field_registration"] == ""


# ---------------------------------------------------------------------------
# Classificacao
# ---------------------------------------------------------------------------


def test_duplicata_dentro_do_arquivo():
    linhas = list(LINHAS_EXEMPLO) + [("OUTRA PESSOA", 132901, "EV", 2020, (1, 1, 1990), "H")]
    analise = analisar(ler_arquivo(planilha_relacao(linhas))["linhas"])
    duplicadas = [linha for linha in analise.linhas if linha.status == RowStatus.DUPLICADO]

    assert len(duplicadas) == 2
    assert analise.resumo()["duplicados"] == 2


def test_ministro_ja_existente():
    services.create_minister(
        dados=dados_de_ministro(
            full_name="Abel Fictício dos Santos",
            conamad_registration="132901",
            field_registration="",
            ministerial_title=MinisterialTitle.EVANGELISTA,
            position="",
            congregation="",
            consecration_year=2025,
            birth_date=datetime.date(2000, 4, 9),
            sex="",
        )
    )
    linha = por_inscricao(analisar(ler_arquivo(planilha_relacao())["linhas"]))["132901"]

    # So a caixa do nome muda: nao e divergencia.
    assert linha.status == RowStatus.EXISTENTE


def test_ministro_existente_com_dados_divergentes_nao_e_alterado():
    ministro = services.create_minister(
        dados=dados_de_ministro(
            full_name="Nome Diferente",
            conamad_registration="132901",
            field_registration="",
            ministerial_title=MinisterialTitle.PASTOR,
        )
    )
    linha = por_inscricao(analisar(ler_arquivo(planilha_relacao())["linhas"]))["132901"]
    assert linha.status == RowStatus.DIVERGENTE
    assert "nome" in linha.divergencias
    assert "título" in linha.divergencias

    lote = ler_arquivo(planilha_relacao())
    confirmar(lote["linhas"], criar_acesso=False)

    ministro.refresh_from_db()
    assert ministro.full_name == "Nome Diferente"
    assert ministro.ministerial_title == MinisterialTitle.PASTOR


def test_matricula_do_campo_conflitante():
    services.create_minister(
        dados=dados_de_ministro(conamad_registration="555", field_registration="0042")
    )
    texto = "Nome;Inscrição Conamad;Matrícula Campo;Titulo\nAna;000123;0042;EV\n"
    linha = analisar(ler_arquivo(csv_relacao(texto))["linhas"]).linhas[0]

    assert linha.status == RowStatus.CONFLITO
    assert "0042" in linha.problemas[0]


def test_matricula_do_campo_repetida_no_arquivo():
    texto = (
        "Nome;Inscrição Conamad;Matrícula Campo;Titulo\n"
        "Ana;1;0042;EV\n"
        "Bia;2;0042;EV\n"
    )
    analise = analisar(ler_arquivo(csv_relacao(texto))["linhas"])
    assert {linha.status for linha in analise.linhas} == {RowStatus.CONFLITO}


@pytest.mark.parametrize(
    "linha_csv, faltando",
    [
        (";1;EV", "nome"),
        ("Ana;;EV", "inscrição"),
        ("Ana;1;", "título"),
    ],
)
def test_campo_obrigatorio_vazio(linha_csv, faltando):
    texto = "Nome;Inscrição Conamad;Titulo\nCarlos;9;PR\n" + linha_csv + "\n"
    analise = analisar(ler_arquivo(csv_relacao(texto))["linhas"])
    ruim = analise.linhas[1]

    assert ruim.status == RowStatus.OBRIGATORIO
    assert faltando in ruim.problemas[0]
    assert analise.resumo()["obrigatorios_ausentes"] == 1


def test_sem_identificador_e_contado():
    texto = "Nome;Inscrição Conamad;Titulo\nAna;;EV\n"
    assert analisar(ler_arquivo(csv_relacao(texto))["linhas"]).resumo()["sem_identificador"] == 1


@pytest.mark.parametrize(
    "linha_csv",
    [
        "Ana;1;Bispo;2000;01/01/1970",  # titulo desconhecido
        "Ana;1;EV;dois mil;01/01/1970",  # ano invalido
        "Ana;1;EV;2000;31/02/1970",  # data inexistente
        "Ana;1<x>;EV;2000;01/01/1970",  # inscricao com caractere invalido
    ],
)
def test_linha_invalida(linha_csv):
    texto = "Nome;Inscrição Conamad;Titulo;Consagração;Data Nascimento\n" + linha_csv + "\n"
    linha = analisar(ler_arquivo(csv_relacao(texto))["linhas"]).linhas[0]
    assert linha.status == RowStatus.INVALIDA


def test_homonimo_e_aviso_e_nao_bloqueio():
    services.create_minister(
        dados=dados_de_ministro(full_name="ABEL FICTÍCIO DOS SANTOS", conamad_registration="1", field_registration="")
    )
    linha = por_inscricao(analisar(ler_arquivo(planilha_relacao())["linhas"]))["132901"]
    assert linha.status == RowStatus.NOVO
    assert any("Mesmo nome" in aviso for aviso in linha.avisos)


# ---------------------------------------------------------------------------
# Fluxo pelo painel
# ---------------------------------------------------------------------------


def test_upload_e_preview_nao_gravam_nada(admin_client_logado):
    antes = contagens()

    resposta = admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()})
    assert resposta.status_code == 302
    assert resposta.url == URL_PREVIEW

    preview = admin_client_logado.get(URL_PREVIEW)
    html = preview.content.decode()
    assert preview.status_code == 200
    assert "Nada foi alterado ainda" in html
    assert preview.context["resumo"]["novos"] == len(LINHAS_EXEMPLO)
    assert SENHA_PADRAO_MINISTRO not in html

    assert contagens() == antes


def test_confirmacao_cria_ministros_e_acessos(admin_client_logado, admin_user, client):
    admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()})
    token = admin_client_logado.get(URL_PREVIEW).context["lote"]["token"]

    resposta = admin_client_logado.post(URL_CONFIRMAR, {"token": token, "criar_acesso": "1"})
    assert resposta.status_code == 302

    assert Minister.objects.count() == len(LINHAS_EXEMPLO)
    assert set(Minister.objects.values_list("source", flat=True)) == {MinisterSource.IMPORT}
    assert MinisterAccount.objects.count() == len(LINHAS_EXEMPLO)

    acesso = MinisterAccount.objects.get(login_identifier="132901")
    assert acesso.must_change_password is True
    assert SENHA_PADRAO_MINISTRO not in acesso.password_hash
    assert acesso.check_password(SENHA_PADRAO_MINISTRO)
    assert acesso.minister.import_sequence == 1

    evento = AuditLog.objects.get(event=AuditEvent.MINISTER_IMPORT_COMPLETED)
    assert evento.actor == admin_user
    assert evento.metadata["ministers_created"] == len(LINHAS_EXEMPLO)
    assert evento.metadata["accounts_created"] == len(LINHAS_EXEMPLO)
    for registro in AuditLog.objects.all():
        assert SENHA_PADRAO_MINISTRO not in str(registro.metadata)

    # O ministro importado entra com a senha padrao e cai na troca.
    entrada = client.post(URL_LOGIN, {"identificador": "132901", "senha": SENHA_PADRAO_MINISTRO})
    assert entrada.url == URL_TROCAR_SENHA


def test_confirmacao_sem_criar_acesso(admin_client_logado):
    admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()})
    token = admin_client_logado.get(URL_PREVIEW).context["lote"]["token"]
    admin_client_logado.post(URL_CONFIRMAR, {"token": token})

    assert Minister.objects.count() == len(LINHAS_EXEMPLO)
    assert MinisterAccount.objects.count() == 0


def test_criar_acesso_sem_senha_padrao_e_recusado(admin_client_logado, settings):
    settings.DEFAULT_MINISTER_PASSWORD = ""
    admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()})
    token = admin_client_logado.get(URL_PREVIEW).context["lote"]["token"]
    antes = contagens()

    admin_client_logado.post(URL_CONFIRMAR, {"token": token, "criar_acesso": "1"})
    assert contagens() == antes


def test_sem_senha_padrao_ainda_cadastra_sem_acesso(admin_client_logado, settings):
    settings.DEFAULT_MINISTER_PASSWORD = ""
    admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()})
    token = admin_client_logado.get(URL_PREVIEW).context["lote"]["token"]
    admin_client_logado.post(URL_CONFIRMAR, {"token": token})
    assert Minister.objects.count() == len(LINHAS_EXEMPLO)


def test_importacao_e_idempotente(admin_client_logado):
    for _ in range(2):
        admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()})
        token = admin_client_logado.get(URL_PREVIEW).context["lote"]["token"]
        admin_client_logado.post(URL_CONFIRMAR, {"token": token, "criar_acesso": "1"})

    assert Minister.objects.count() == len(LINHAS_EXEMPLO)
    assert MinisterAccount.objects.count() == len(LINHAS_EXEMPLO)
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_IMPORT_COMPLETED).count() == 1

    admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()})
    resumo = admin_client_logado.get(URL_PREVIEW).context["resumo"]
    assert resumo["novos"] == 0
    assert resumo["existentes"] == len(LINHAS_EXEMPLO)


def test_confirmar_direto_no_servico_duas_vezes():
    linhas = ler_arquivo(planilha_relacao())["linhas"]
    confirmar(linhas, criar_acesso=True)
    with pytest.raises(DomainError):
        confirmar(linhas, criar_acesso=True)
    assert Minister.objects.count() == len(LINHAS_EXEMPLO)


def test_token_errado_nao_importa(admin_client_logado):
    admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()})
    admin_client_logado.post(URL_CONFIRMAR, {"token": "forjado", "criar_acesso": "1"})
    assert Minister.objects.count() == 0


def test_campos_enviados_pelo_navegador_nao_decidem_nada(admin_client_logado):
    admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao(LINHAS_EXEMPLO[:1])})
    token = admin_client_logado.get(URL_PREVIEW).context["lote"]["token"]
    admin_client_logado.post(
        URL_CONFIRMAR,
        {
            "token": token,
            "linhas": "999",
            "status": "NOVO",
            "conamad": "000000",
            "nome": "Injetado",
        },
    )
    assert list(Minister.objects.values_list("conamad_registration", flat=True)) == ["132901"]


def test_confirmacao_revalida_contra_o_banco(admin_client_logado):
    """Ministro cadastrado entre o preview e a confirmacao nao e duplicado."""
    admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()})
    token = admin_client_logado.get(URL_PREVIEW).context["lote"]["token"]

    services.create_minister(
        dados=dados_de_ministro(conamad_registration="132901", field_registration="")
    )
    admin_client_logado.post(URL_CONFIRMAR, {"token": token})

    assert Minister.objects.filter(conamad_registration="132901").count() == 1
    assert Minister.objects.count() == len(LINHAS_EXEMPLO)


def test_cancelar_limpa_o_lote(admin_client_logado):
    admin_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()})
    admin_client_logado.post(URL_CANCELAR)
    assert admin_client_logado.get(URL_PREVIEW).status_code == 302


def test_upload_de_formato_errado(admin_client_logado):
    from django.core.files.uploadedfile import SimpleUploadedFile

    resposta = admin_client_logado.post(
        URL_UPLOAD, {"arquivo": SimpleUploadedFile("relacao.pdf", b"%PDF-1.4")}
    )
    assert resposta.status_code == 200
    assert "Formato não suportado" in resposta.content.decode()


def test_aluno_nao_importa(student_client_logado):
    assert student_client_logado.post(URL_UPLOAD, {"arquivo": planilha_relacao()}).status_code == 403
    assert student_client_logado.post(URL_CONFIRMAR, {"token": "x"}).status_code == 403
