"""
Provisionamento de acessos ao Portal do Ministro (Fase 2F).

Por que nao a senha padrao
--------------------------
A importacao (Etapa 1) cria acessos com DEFAULT_MINISTER_PASSWORD: a mesma
senha inicial para todos. Para 84 ministros isso e uma senha compartilhada —
quem recebeu a sua sabe a de todos os outros que ainda nao trocaram. Aqui
cada acesso nasce com uma senha temporaria PROPRIA, sorteada com `secrets`,
e com troca obrigatoria no primeiro login.

    planejar()   le o cadastro e diz o que aconteceria. NAO escreve nada.
    executar()   cria os acessos numa transacao e devolve as senhas, uma
                 unica vez, para quem chamou entregar.

Regras
------
    ministro nao encontrado     a lista esta errada: nada e executado
    registro ambiguo            idem (casa com mais de um ministro)
    ministro inativo            ignorado
    ja tem acesso               ignorado — nunca redefine senha; reset e
                                outra acao, explicita, na ficha do ministro
    login ja usado por outro    ignorado

A senha temporaria nao vai para o banco (so o hash), nem para a trilha, nem
para log. Quem a recebe e o comando, que a grava num arquivo 0600 fora do
repositorio e diz onde.
"""

import secrets
from dataclasses import dataclass

from django.db import transaction

from common.exceptions import DomainError
from ministers import services
from ministers.models import Minister, MinisterAccount
from ministers.normalizacao import normalizar_registro

# Sem I, O, l, 0 e 1: a senha vai ser lida num papel ou numa tela de celular
# e digitada a mao.
MAIUSCULAS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
MINUSCULAS = "abcdefghijkmnopqrstuvwxyz"
DIGITOS = "23456789"
# Especiais que nenhum teclado de celular esconde e que nao confundem CSV,
# shell ou planilha.
ESPECIAIS = "#@%+=?!"
TAMANHO_DA_SENHA = 14

CRIAR = "CRIAR"
JA_TEM_ACESSO = "JA_TEM_ACESSO"
INATIVO = "INATIVO"
LOGIN_EM_USO = "LOGIN_EM_USO"
NAO_ENCONTRADO = "NAO_ENCONTRADO"
AMBIGUO = "AMBIGUO"

ROTULOS = {
    CRIAR: "acesso sera criado",
    JA_TEM_ACESSO: "ja tem acesso — ignorado (senha NAO e redefinida)",
    INATIVO: "ministro inativo — ignorado",
    LOGIN_EM_USO: "inscricao ja usada como login por outro acesso — ignorado",
    NAO_ENCONTRADO: "nenhum ministro com esta inscricao ou matricula",
    AMBIGUO: "casa com mais de um ministro",
}
BLOQUEIAM = {NAO_ENCONTRADO, AMBIGUO}


def gerar_senha_temporaria(identificador=""):
    """
    14 caracteres de `secrets`, com pelo menos uma maiuscula, uma minuscula,
    um digito e um especial, e aprovada pelos mesmos validadores da troca de
    senha (inclusive "nao contem a inscricao"). Nao deriva de nome, data,
    inscricao ou de nada do cadastro.
    """
    todos = MAIUSCULAS + MINUSCULAS + DIGITOS + ESPECIAIS
    sorteio = secrets.SystemRandom()
    while True:
        caracteres = [
            sorteio.choice(MAIUSCULAS),
            sorteio.choice(MINUSCULAS),
            sorteio.choice(DIGITOS),
            sorteio.choice(ESPECIAIS),
        ] + [sorteio.choice(todos) for _ in range(TAMANHO_DA_SENHA - 4)]
        sorteio.shuffle(caracteres)
        senha = "".join(caracteres)
        try:
            return services.validar_senha(senha, identificador=identificador)
        except DomainError:
            continue


@dataclass
class Linha:
    pedido: str
    situacao: str
    ministro: Minister = None

    @property
    def rotulo(self):
        return ROTULOS[self.situacao]


@dataclass
class Plano:
    linhas: list

    def com(self, situacao):
        return [linha for linha in self.linhas if linha.situacao == situacao]

    @property
    def a_criar(self):
        return self.com(CRIAR)

    @property
    def bloqueado(self):
        return any(linha.situacao in BLOQUEIAM for linha in self.linhas)


def _registros(pedidos):
    vistos, saida = set(), []
    for pedido in pedidos:
        registro = normalizar_registro(pedido)
        if registro and registro not in vistos:
            vistos.add(registro)
            saida.append(registro)
    return saida


def planejar(pedidos):
    """Somente leitura. A inscricao CONAMAD ou a matricula do campo."""
    registros = _registros(pedidos)
    if not registros:
        raise DomainError("Informe ao menos uma inscricao CONAMAD ou matricula do campo.")
    linhas = []
    for registro in registros:
        candidatos = list(
            Minister.objects.filter(conamad_registration=registro)
            | Minister.objects.filter(field_registration=registro)
        )
        if not candidatos:
            linhas.append(Linha(registro, NAO_ENCONTRADO))
            continue
        if len({m.pk for m in candidatos}) > 1:
            linhas.append(Linha(registro, AMBIGUO))
            continue
        ministro = candidatos[0]
        if MinisterAccount.objects.filter(minister=ministro).exists():
            situacao = JA_TEM_ACESSO
        elif not ministro.is_active:
            situacao = INATIVO
        elif MinisterAccount.objects.filter(login_identifier=ministro.conamad_registration).exists():
            situacao = LOGIN_EM_USO
        else:
            situacao = CRIAR
        linhas.append(Linha(registro, situacao, ministro))

    # O mesmo ministro pedido duas vezes (por inscricao e por matricula)
    # entra uma vez so.
    vistos = set()
    unicas = []
    for linha in linhas:
        chave = linha.ministro.pk if linha.ministro is not None else ("pedido", linha.pedido)
        if chave not in vistos:
            vistos.add(chave)
            unicas.append(linha)
    return Plano(unicas)


def executar(plano, *, ao_criar=None, actor=None):
    """
    Cria os acessos do plano numa transacao so e devolve [(ministro, senha)].

    Tudo e conferido de novo com o ministro travado: se alguem criou um
    acesso entre o dry-run e a execucao, esse ministro e pulado — nunca
    sobrescrito. `ao_criar(credenciais)` roda DENTRO da transacao (e quem
    grava o arquivo): se ela falhar, nenhum acesso fica criado sem senha
    entregue.
    """
    if plano.bloqueado:
        raise DomainError("A lista tem registros nao encontrados ou ambiguos. Corrija antes de executar.")
    credenciais = []
    with transaction.atomic():
        for linha in plano.a_criar:
            ministro = Minister.objects.select_for_update().get(pk=linha.ministro.pk)
            if (
                not ministro.is_active
                or MinisterAccount.objects.filter(minister=ministro).exists()
                or MinisterAccount.objects.filter(login_identifier=ministro.conamad_registration).exists()
            ):
                continue
            senha = gerar_senha_temporaria(ministro.conamad_registration)
            services._criar_acesso(
                ministro,
                senha=senha,
                must_change_password=True,
                origem="provisionamento",
                actor=actor,
            )
            credenciais.append((ministro, senha))
        if ao_criar is not None and credenciais:
            ao_criar(credenciais)
    return credenciais
