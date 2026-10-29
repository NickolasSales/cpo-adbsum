"""
Cria acessos ao Portal do Ministro com senha temporaria individual.

    # dry-run (o padrao): mostra o que aconteceria, nao grava nada
    manage.py provisionar_acessos_ministros --minister-registration 44303

    # execucao: exige --execute, o arquivo de saida e a frase de confirmacao
    manage.py provisionar_acessos_ministros --minister-registration 44303 \
        --minister-registration 132927 --execute \
        --saida /root/credenciais-piloto.csv --confirmar "PROVISIONAR 2"

Sem lista, nao faz nada: nao existe "todos". A liberacao em massa, quando for
decidida, passa uma lista (--arquivo, um registro por linha) — e o dry-run
dela mostra cada ministro antes.

As senhas temporarias so aparecem no arquivo de --saida: criado agora, com
permissao 0600, fora do repositorio e fora de media/static. Nunca no
terminal, no log ou na trilha. Depois de entregar as credenciais, apague o
arquivo. Ver ministers.provisionamento para as regras.
"""

import csv
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User, UserRole
from common.exceptions import DomainError
from ministers import provisionamento


def _dentro(caminho, pasta):
    try:
        caminho.relative_to(Path(pasta).resolve())
        return True
    except ValueError:
        return False


def _validar_saida(texto):
    if not texto:
        raise CommandError("--execute exige --saida <arquivo> para as credenciais.")
    caminho = Path(texto).expanduser().resolve()
    proibidas = [settings.BASE_DIR, settings.MEDIA_ROOT]
    if getattr(settings, "STATIC_ROOT", None):
        proibidas.append(settings.STATIC_ROOT)
    if any(_dentro(caminho, pasta) for pasta in proibidas):
        raise CommandError(
            "O arquivo de credenciais nao pode ficar dentro do projeto, de media ou de static."
        )
    if caminho.exists():
        raise CommandError("{} ja existe. Escolha outro arquivo; nada foi feito.".format(caminho))
    if not caminho.parent.is_dir():
        raise CommandError("A pasta {} nao existe.".format(caminho.parent))
    return caminho


def _gravar(caminho, credenciais):
    # O_EXCL: se o arquivo surgir entre a validacao e a escrita, falha em vez
    # de escrever por cima. 0600 desde a criacao, nao depois.
    descritor = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descritor, "w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(["nome", "login (inscricao CONAMAD)", "senha temporaria"])
        for ministro, senha in credenciais:
            escritor.writerow([ministro.full_name, ministro.conamad_registration, senha])


class Command(BaseCommand):
    help = (
        "Cria acessos ao portal com senha temporaria individual e troca obrigatoria. "
        "Dry-run por padrao."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--minister-registration",
            action="append",
            default=[],
            dest="registros",
            help="Inscricao CONAMAD ou matricula do campo. Repita para varios.",
        )
        parser.add_argument(
            "--arquivo",
            help="Arquivo texto com um registro por linha (linhas vazias e # sao ignoradas).",
        )
        parser.add_argument("--execute", action="store_true", help="Grava. Sem isto, so mostra.")
        parser.add_argument("--saida", help="Arquivo NOVO para as credenciais (obrigatorio com --execute).")
        parser.add_argument("--confirmar", help='Frase exata mostrada no dry-run, ex.: "PROVISIONAR 3".')
        parser.add_argument("--autor", help="E-mail do ADMIN que responde pela execucao (vai para a trilha).")

    def _pedidos(self, opcoes):
        pedidos = list(opcoes["registros"])
        if opcoes["arquivo"]:
            try:
                with open(opcoes["arquivo"], encoding="utf-8") as arquivo:
                    for linha in arquivo:
                        linha = linha.strip()
                        if linha and not linha.startswith("#"):
                            pedidos.append(linha)
            except OSError as erro:
                raise CommandError("Nao foi possivel ler {}: {}".format(opcoes["arquivo"], erro))
        return pedidos

    def handle(self, *args, **opcoes):
        try:
            plano = provisionamento.planejar(self._pedidos(opcoes))
        except DomainError as erro:
            raise CommandError(str(erro))

        for linha in plano.linhas:
            ministro = linha.ministro
            if ministro is None:
                self.stdout.write("- {}: {}".format(linha.pedido, linha.rotulo))
                continue
            self.stdout.write(
                "- {}: {} (CONAMAD {}{}) — {}".format(
                    linha.pedido,
                    ministro.full_name,
                    ministro.conamad_registration,
                    ", campo {}".format(ministro.field_registration) if ministro.field_registration else "",
                    linha.rotulo,
                )
            )
        total = len(plano.a_criar)
        frase = "PROVISIONAR {}".format(total)
        self.stdout.write("")
        self.stdout.write("Acessos a criar: {}".format(total))
        self.stdout.write("Senha temporaria: individual, gerada na execucao (nao e mostrada aqui)")
        self.stdout.write("Troca obrigatoria no primeiro login: SIM")

        if plano.bloqueado:
            raise CommandError("Ha registros nao encontrados ou ambiguos. Corrija a lista; nada foi feito.")
        if not opcoes["execute"]:
            self.stdout.write(self.style.WARNING("DRY-RUN: nada foi gravado."))
            if total:
                self.stdout.write('Para executar: --execute --saida <arquivo> --confirmar "{}"'.format(frase))
            return
        if not total:
            self.stdout.write("Nada a criar.")
            return

        caminho = _validar_saida(opcoes["saida"])
        if opcoes["confirmar"] != frase:
            raise CommandError('Confirmacao ausente ou diferente. Use --confirmar "{}"; nada foi feito.'.format(frase))

        autor = None
        if opcoes["autor"]:
            autor = User.objects.filter(
                email__iexact=opcoes["autor"], role=UserRole.ADMIN, is_active=True
            ).first()
            if autor is None:
                raise CommandError("--autor precisa ser o e-mail de um ADMIN ativo.")

        try:
            credenciais = provisionamento.executar(
                plano, ao_criar=lambda creds: _gravar(caminho, creds), actor=autor
            )
        except Exception:
            # Se o arquivo chegou a ser escrito e a transacao desfez, ele
            # descreve acessos que nao existem: apaga.
            if caminho.exists():
                caminho.unlink()
            raise

        self.stdout.write(self.style.SUCCESS("{} acesso(s) criado(s).".format(len(credenciais))))
        if credenciais:
            self.stdout.write("Credenciais em {} (0600). Entregue e APAGUE o arquivo.".format(caminho))
        if len(credenciais) != total:
            self.stdout.write(
                "{} ministro(s) mudaram entre a conferencia e a execucao e foram pulados.".format(
                    total - len(credenciais)
                )
            )
