"""Provisionamento de acessos com senha temporaria individual (Fase 2F)."""

import csv
import logging
import os
import re
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.hashers import identify_hasher
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client

from audit.models import AuditEvent, AuditLog
from ministers import provisionamento, services
from ministers.models import Minister, MinisterAccount
from ministers.tests.conftest import (
    SENHA_MINISTRO,
    URL_LOGIN,
    URL_PAINEL,
    URL_TROCAR_SENHA,
    dados_de_ministro,
)

pytestmark = pytest.mark.django_db

SENHA_NOVA = "Escolhida#Pelo2027"


def novo(numero, **extra):
    dados = dados_de_ministro(
        full_name="Ministro Provisionado {}".format(numero),
        conamad_registration=str(numero),
        field_registration=extra.pop("field_registration", ""),
        **extra,
    )
    return services.create_minister(dados=dados)


def rodar(*args):
    saida = StringIO()
    call_command("provisionar_acessos_ministros", *args, stdout=saida)
    return saida.getvalue()


def ler(caminho):
    with open(caminho, encoding="utf-8", newline="") as arquivo:
        linhas = list(csv.reader(arquivo, delimiter=";"))
    return {linha[1]: linha[2] for linha in linhas[1:]}


@pytest.fixture
def saida(tmp_path):
    # Fora do projeto: tmp_path nunca fica dentro de BASE_DIR.
    return tmp_path / "credenciais" / "piloto.csv"


@pytest.fixture(autouse=True)
def pasta_da_saida(saida):
    saida.parent.mkdir()


# ---------------------------------------------------------------------------
# Senha temporaria
# ---------------------------------------------------------------------------


def test_senha_temporaria_tem_forma_e_passa_nos_validadores():
    for _ in range(200):
        senha = provisionamento.gerar_senha_temporaria("44303")
        assert len(senha) == provisionamento.TAMANHO_DA_SENHA
        assert re.search(r"[A-Z]", senha) and re.search(r"[a-z]", senha)
        assert re.search(r"[0-9]", senha) and re.search(r"[#@%+=?!]", senha)
        assert not re.search(r"[IOl01]", senha)
        assert "44303" not in senha
        services.validar_senha(senha, identificador="44303")


def test_senhas_nao_se_repetem():
    senhas = {provisionamento.gerar_senha_temporaria() for _ in range(2000)}
    assert len(senhas) == 2000


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_e_o_padrao_e_nao_grava(saida):
    m = novo(44303)
    texto = rodar("--minister-registration", "44303")
    assert "Ministro Provisionado 44303 (CONAMAD 44303) — acesso sera criado" in texto
    assert "Acessos a criar: 1" in texto
    assert "Troca obrigatoria no primeiro login: SIM" in texto
    assert "DRY-RUN: nada foi gravado." in texto
    assert 'Para executar: --execute --saida <arquivo> --confirmar "PROVISIONAR 1"' in texto
    assert not MinisterAccount.objects.filter(minister=m).exists()
    assert not AuditLog.objects.filter(event=AuditEvent.MINISTER_ACCOUNT_CREATED).exists()
    assert not saida.exists()


def test_dry_run_por_matricula_do_campo():
    novo(5001, field_registration="0077")
    assert "Ministro Provisionado 5001 (CONAMAD 5001, campo 0077) — acesso sera criado" in rodar(
        "--minister-registration", "0077"
    )


def test_dry_run_mostra_cada_situacao():
    novo(1)
    services.create_minister(dados=dados_de_ministro(full_name="Com Acesso", conamad_registration="77"), password=SENHA_MINISTRO)
    inativo = novo(3)
    services.deactivate_minister(inativo)
    texto = rodar("--minister-registration", "1", "--minister-registration", "77", "--minister-registration", "3")
    assert "1: Ministro Provisionado 1 (CONAMAD 1) — acesso sera criado" in texto
    assert "77: Com Acesso (CONAMAD 77, campo 7764) — ja tem acesso — ignorado (senha NAO e redefinida)" in texto
    assert "3: Ministro Provisionado 3 (CONAMAD 3) — ministro inativo — ignorado" in texto
    assert "Acessos a criar: 1" in texto


def test_registro_inexistente_bloqueia_ate_a_execucao(saida):
    novo(1)
    with pytest.raises(CommandError, match="nao encontrados ou ambiguos"):
        rodar("--minister-registration", "1", "--minister-registration", "999",
              "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    assert not MinisterAccount.objects.exists()
    assert not saida.exists()


def test_registro_ambiguo_bloqueia():
    novo(10, field_registration="20")
    novo(20)
    with pytest.raises(CommandError, match="ambiguos"):
        rodar("--minister-registration", "20")


def test_lista_vazia_e_recusada():
    with pytest.raises(CommandError, match="Informe ao menos uma"):
        rodar()


def test_lista_por_arquivo(tmp_path):
    novo(1)
    novo(2)
    lista = tmp_path / "lista.txt"
    lista.write_text("# piloto\n1\n\n 2 \n1\n", encoding="utf-8")
    assert "Acessos a criar: 2" in rodar("--arquivo", str(lista))


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------


def test_execucao_cria_acessos_individuais_com_troca_obrigatoria(saida):
    ministros = [novo(n) for n in (101, 102, 103)]
    texto = rodar(*sum((["--minister-registration", str(m.conamad_registration)] for m in ministros), []),
                  "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 3")
    assert "3 acesso(s) criado(s)." in texto
    assert "Entregue e APAGUE o arquivo." in texto
    credenciais = ler(saida)
    assert set(credenciais) == {"101", "102", "103"}
    assert len(set(credenciais.values())) == 3
    for m in ministros:
        conta = MinisterAccount.objects.get(minister=m)
        senha = credenciais[m.conamad_registration]
        assert conta.is_active and conta.must_change_password
        assert conta.login_identifier == m.conamad_registration
        assert conta.password_hash != senha and identify_hasher(conta.password_hash)
        assert conta.check_password(senha)
        # Nenhuma senha sai no terminal.
        assert senha not in texto
    if os.name == "posix":
        assert os.stat(saida).st_mode & 0o777 == 0o600


def test_auditoria_sem_senha(saida):
    m = novo(101)
    rodar("--minister-registration", "101", "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    senha = ler(saida)["101"]
    log = AuditLog.objects.get(event=AuditEvent.MINISTER_ACCOUNT_CREATED)
    assert log.entity_id == str(m.pk)
    # O sanitizador da trilha (Etapa 1) apaga o valor de qualquer chave com
    # "senha" no nome — inclusive o booleano senha_padrao.
    assert log.metadata == {"senha_padrao": "[REMOVIDO]", "origem": "provisionamento"}
    for registro in AuditLog.objects.all():
        assert senha not in str(registro.metadata) and senha not in (registro.user_agent or "")


def test_senha_nao_vai_para_log(saida, caplog):
    novo(101)
    with caplog.at_level(logging.DEBUG):
        rodar("--minister-registration", "101", "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    senha = ler(saida)["101"]
    assert senha not in caplog.text


def test_senha_nao_fica_em_texto_claro_no_banco(saida):
    from django.db import connection

    novo(101)
    rodar("--minister-registration", "101", "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    senha = ler(saida)["101"]
    with connection.cursor() as cursor:
        for tabela in ("ministers_ministeraccount", "ministers_minister", "audit_auditlog"):
            cursor.execute("SELECT * FROM {}".format(tabela))
            assert senha not in str(cursor.fetchall()), tabela


def test_execucao_exige_confirmacao_exata(saida):
    novo(101)
    for confirmacao in (None, "PROVISIONAR", "PROVISIONAR 2", "provisionar 1"):
        args = ["--minister-registration", "101", "--execute", "--saida", str(saida)]
        if confirmacao:
            args += ["--confirmar", confirmacao]
        with pytest.raises(CommandError, match="Confirmacao"):
            rodar(*args)
    assert not MinisterAccount.objects.exists()
    assert not saida.exists()


def test_execucao_exige_saida():
    novo(101)
    with pytest.raises(CommandError, match="--saida"):
        rodar("--minister-registration", "101", "--execute", "--confirmar", "PROVISIONAR 1")
    assert not MinisterAccount.objects.exists()


@pytest.mark.parametrize("dentro", ["BASE_DIR", "MEDIA_ROOT"])
def test_saida_dentro_do_projeto_ou_de_media_e_recusada(dentro):
    novo(101)
    pasta = Path(getattr(settings, dentro))
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / "credenciais-teste-nao-deve-existir.csv"
    with pytest.raises(CommandError, match="nao pode ficar dentro"):
        rodar("--minister-registration", "101", "--execute", "--saida", str(alvo), "--confirmar", "PROVISIONAR 1")
    assert not alvo.exists()
    assert not MinisterAccount.objects.exists()


def test_saida_existente_nao_e_sobrescrita(saida):
    novo(101)
    saida.write_text("nao me apague", encoding="utf-8")
    with pytest.raises(CommandError, match="ja existe"):
        rodar("--minister-registration", "101", "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    assert saida.read_text(encoding="utf-8") == "nao me apague"
    assert not MinisterAccount.objects.exists()


def test_conta_existente_nunca_e_sobrescrita(saida):
    m = services.create_minister(dados=dados_de_ministro(), password=SENHA_MINISTRO)
    antes = MinisterAccount.objects.filter(minister=m).values().get()
    novo(101)
    rodar("--minister-registration", m.conamad_registration, "--minister-registration", "101",
          "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    assert MinisterAccount.objects.filter(minister=m).values().get() == antes
    assert MinisterAccount.objects.get(minister=m).check_password(SENHA_MINISTRO)
    assert set(ler(saida)) == {"101"}


def test_inativo_e_ignorado_na_execucao(saida):
    inativo = novo(101)
    services.deactivate_minister(inativo)
    novo(102)
    rodar("--minister-registration", "101", "--minister-registration", "102",
          "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    assert not MinisterAccount.objects.filter(minister=inativo).exists()
    assert set(ler(saida)) == {"102"}


def test_rodar_de_novo_nao_cria_nem_redefine(saida, tmp_path):
    novo(101)
    rodar("--minister-registration", "101", "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    hash_antes = MinisterAccount.objects.get().password_hash
    texto = rodar("--minister-registration", "101", "--execute", "--saida", str(tmp_path / "outra.csv"),
                  "--confirmar", "PROVISIONAR 0")
    assert "Nada a criar." in texto
    assert MinisterAccount.objects.count() == 1
    assert MinisterAccount.objects.get().password_hash == hash_antes
    assert not (tmp_path / "outra.csv").exists()


def test_falha_ao_gravar_desfaz_tudo(saida, monkeypatch):
    from ministers.management.commands import provisionar_acessos_ministros as comando

    novo(101)

    def quebra(caminho, credenciais):
        raise OSError("disco cheio")

    monkeypatch.setattr(comando, "_gravar", quebra)
    with pytest.raises(OSError):
        rodar("--minister-registration", "101", "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    assert not MinisterAccount.objects.exists()
    assert not AuditLog.objects.filter(event=AuditEvent.MINISTER_ACCOUNT_CREATED).exists()
    assert not saida.exists()


def test_autor_vai_para_a_trilha(saida, admin_user, student_user):
    novo(101)
    with pytest.raises(CommandError, match="ADMIN ativo"):
        rodar("--minister-registration", "101", "--execute", "--saida", str(saida),
              "--confirmar", "PROVISIONAR 1", "--autor", student_user.email)
    rodar("--minister-registration", "101", "--execute", "--saida", str(saida),
          "--confirmar", "PROVISIONAR 1", "--autor", admin_user.email)
    assert AuditLog.objects.get(event=AuditEvent.MINISTER_ACCOUNT_CREATED).actor == admin_user


def test_cem_ministros_cem_senhas_diferentes(saida):
    for n in range(1, 101):
        novo(7000 + n)
    lista = saida.parent / "lista.txt"
    lista.write_text("\n".join(str(7000 + n) for n in range(1, 101)), encoding="utf-8")
    rodar("--arquivo", str(lista), "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 100")
    credenciais = ler(saida)
    assert len(credenciais) == 100
    assert len(set(credenciais.values())) == 100
    assert MinisterAccount.objects.filter(must_change_password=True, is_active=True).count() == 100
    assert len(set(MinisterAccount.objects.values_list("password_hash", flat=True))) == 100


# ---------------------------------------------------------------------------
# Primeiro login
# ---------------------------------------------------------------------------


def test_primeiro_login_exige_troca_e_rotaciona_a_sessao(saida):
    novo(101)
    rodar("--minister-registration", "101", "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    temporaria = ler(saida)["101"]
    client = Client()
    resposta = client.post(URL_LOGIN, {"identificador": "101", "senha": temporaria})
    assert resposta.status_code == 302 and resposta.url == URL_TROCAR_SENHA
    # Antes da troca, nada do portal abre.
    for url in (URL_PAINEL, "/ministros/mensalidades/", "/ministros/perfil/"):
        assert client.get(url).url == URL_TROCAR_SENHA
    chave_antes = client.session.session_key
    resposta = client.post(
        URL_TROCAR_SENHA,
        {"senha_atual": temporaria, "nova_senha1": SENHA_NOVA, "nova_senha2": SENHA_NOVA},
    )
    assert resposta.status_code == 302 and resposta.url == URL_PAINEL
    assert client.session.session_key != chave_antes
    assert client.get(URL_PAINEL).status_code == 200
    conta = MinisterAccount.objects.get()
    assert not conta.must_change_password and conta.check_password(SENHA_NOVA)
    assert not conta.check_password(temporaria)


def test_conta_bloqueada_perde_o_portal_na_hora(saida, admin_user):
    m = novo(101)
    rodar("--minister-registration", "101", "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    client = Client()
    client.post(URL_LOGIN, {"identificador": "101", "senha": ler(saida)["101"]})
    assert client.get(URL_TROCAR_SENHA).status_code == 200
    services.block_account(m, actor=admin_user)
    resposta = client.get(URL_TROCAR_SENHA)
    assert resposta.status_code == 302 and resposta.url.startswith(URL_LOGIN)


def test_provisionar_nao_altera_o_cpo(saida, tentativa):
    from ministers.tests.test_isolamento import retrato_do_cpo

    antes = retrato_do_cpo()
    novo(101)
    rodar("--minister-registration", "101", "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    assert retrato_do_cpo() == antes


def test_ministro_continua_sem_user(saida):
    from accounts.models import User

    usuarios = User.objects.count()
    novo(101)
    rodar("--minister-registration", "101", "--execute", "--saida", str(saida), "--confirmar", "PROVISIONAR 1")
    assert User.objects.count() == usuarios
    assert Minister.objects.get(conamad_registration="101").account.is_active
