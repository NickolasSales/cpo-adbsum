"""
Endurecimento de producao: padroes seguros e excecoes explicitas.

Estes testes existem por causa de uma valvula de escape: sob DEBUG=False os
cookies de sessao e de CSRF sao marcados como Secure, e um deploy acessado por
IP em HTTP nunca receberia esses cookies de volta. Tornar as duas opcoes
parametrizaveis resolve o smoke test, mas cria um risco novo — alguem trocar o
padrao, ou um ambiente esquecer de declarar e ficar inseguro sem aviso.

O que fica travado aqui:

  1. o padrao de producao e seguro em todas as quatro opcoes;
  2. o afrouxamento so acontece por declaracao explicita no ambiente;
  3. desenvolvimento nao e afetado.

O modulo de settings e avaliado uma unica vez no arranque do processo, entao
cada cenario roda num subprocesso com o ambiente montado do zero. E mais lento
do que remendar o modulo em memoria, e e o unico jeito de exercitar exatamente
o caminho que roda em producao.
"""

import json
import os
import subprocess
import sys

import pytest

OPCOES = (
    "SESSION_COOKIE_SECURE",
    "CSRF_COOKIE_SECURE",
    "SECURE_SSL_REDIRECT",
    "SECURE_HSTS_SECONDS",
)

PROGRAMA = (
    "import django, json;"
    "django.setup();"
    "from django.conf import settings as s;"
    "print(json.dumps({nome: getattr(s, nome, None) for nome in "
    + repr(list(OPCOES + ("DEBUG",)))
    + "}))"
)


def resolver(**ambiente):
    """Devolve as opcoes de endurecimento resolvidas para o ambiente dado."""
    base = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "SECRET_KEY": "chave-usada-somente-neste-teste",
        "DATABASE_URL": "postgresql://usuario:senha@127.0.0.1:5432/banco",
    }
    base.update(ambiente)

    concluido = subprocess.run(
        [sys.executable, "-c", PROGRAMA],
        env=base,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert concluido.returncode == 0, concluido.stderr
    return json.loads(concluido.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# Padrao de producao
# ---------------------------------------------------------------------------


def test_producao_sem_declarar_nada_e_segura():
    """
    O cenario que mais importa: um .env que nao menciona nenhuma das opcoes.

    E o estado de qualquer ambiente novo, e precisa ser o estado seguro.
    """
    resolvido = resolver(DEBUG="False")

    assert resolvido["DEBUG"] is False
    assert resolvido["SESSION_COOKIE_SECURE"] is True
    assert resolvido["CSRF_COOKIE_SECURE"] is True
    assert resolvido["SECURE_SSL_REDIRECT"] is True
    assert resolvido["SECURE_HSTS_SECONDS"] == 31536000


@pytest.mark.parametrize("opcao", OPCOES)
def test_cada_opcao_tem_padrao_seguro_isoladamente(opcao):
    """
    Contraprova por opcao.

    Se alguem trocar um dos quatro padroes, o teste aponta qual.
    """
    resolvido = resolver(DEBUG="False")
    esperado = 31536000 if opcao == "SECURE_HSTS_SECONDS" else True
    assert resolvido[opcao] == esperado


# ---------------------------------------------------------------------------
# Excecao explicita
# ---------------------------------------------------------------------------


def test_smoke_por_http_pode_afrouxar_declarando_no_ambiente():
    """
    A combinacao usada no deploy de fumaca por IP.

    Sem ela o navegador nao devolveria o cookie de sessao e o login seria
    impossivel. Com ela, DEBUG continua False — a pagina de erro detalhada
    nao e exposta.
    """
    resolvido = resolver(
        DEBUG="False",
        SESSION_COOKIE_SECURE="False",
        CSRF_COOKIE_SECURE="False",
        SECURE_SSL_REDIRECT="False",
        SECURE_HSTS_SECONDS="0",
    )

    assert resolvido["DEBUG"] is False
    assert resolvido["SESSION_COOKIE_SECURE"] is False
    assert resolvido["CSRF_COOKIE_SECURE"] is False
    assert resolvido["SECURE_SSL_REDIRECT"] is False
    assert resolvido["SECURE_HSTS_SECONDS"] == 0


def test_afrouxar_uma_opcao_nao_afrouxa_as_outras():
    """Cada valvula e independente; nao ha efeito em cascata."""
    resolvido = resolver(DEBUG="False", SESSION_COOKIE_SECURE="False")

    assert resolvido["SESSION_COOKIE_SECURE"] is False
    assert resolvido["CSRF_COOKIE_SECURE"] is True
    assert resolvido["SECURE_SSL_REDIRECT"] is True
    assert resolvido["SECURE_HSTS_SECONDS"] == 31536000


@pytest.mark.parametrize("valor", ["True", "true", "1", "sim", "on", "yes"])
def test_valores_afirmativos_restauram_a_protecao(valor):
    """
    Depois do HTTPS entrar, o .env volta a declarar True.

    Aceitar as varias grafias evita que um "true" minusculo seja lido como
    falso e deixe o ambiente inseguro sem ninguem perceber.
    """
    resolvido = resolver(DEBUG="False", SESSION_COOKIE_SECURE=valor)
    assert resolvido["SESSION_COOKIE_SECURE"] is True


@pytest.mark.parametrize("valor", ["", "nao", "0", "off", "qualquer-coisa"])
def test_somente_valores_reconhecidos_desligam_a_protecao(valor):
    """
    env_bool trata como falso tudo que nao for afirmativo.

    Documentado aqui de proposito: um erro de digitacao no .env desliga a
    protecao em vez de manter o padrao. E o comportamento do helper em todo
    o projeto, e quem revisar um .env precisa saber disso.
    """
    resolvido = resolver(DEBUG="False", SESSION_COOKIE_SECURE=valor)
    assert resolvido["SESSION_COOKIE_SECURE"] is False


# ---------------------------------------------------------------------------
# Desenvolvimento
# ---------------------------------------------------------------------------


def test_desenvolvimento_nao_recebe_o_bloco_de_endurecimento():
    """
    Sob DEBUG=True nada disso e aplicado.

    Se fosse, o servidor local em HTTP redirecionaria para HTTPS e ninguem
    conseguiria trabalhar.
    """
    resolvido = resolver(DEBUG="True")

    assert resolvido["DEBUG"] is True
    assert resolvido["SESSION_COOKIE_SECURE"] is False
    assert resolvido["CSRF_COOKIE_SECURE"] is False
    assert resolvido["SECURE_SSL_REDIRECT"] is False
    assert resolvido["SECURE_HSTS_SECONDS"] == 0
