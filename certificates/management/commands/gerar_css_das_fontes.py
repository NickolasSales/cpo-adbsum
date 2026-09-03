"""
Reescreve static/css/fontes-certificado.css a partir de certificates/fonts.py.

Rode depois de mexer no registro de fontes. O teste
`test_css_das_fontes_esta_em_dia` recusa a suite se voce esquecer.
"""

from django.core.management.base import BaseCommand

from certificates.css_das_fontes import caminho_do_css, gerar


class Command(BaseCommand):
    help = "Gera o CSS @font-face das fontes do certificado."

    def add_arguments(self, parser):
        parser.add_argument(
            "--conferir",
            action="store_true",
            help="Nao escreve; sai com codigo 1 se o arquivo estiver desatualizado.",
        )

    def handle(self, *args, **opcoes):
        destino = caminho_do_css()
        conteudo = gerar()

        atual = destino.read_text(encoding="utf-8") if destino.is_file() else None

        if opcoes["conferir"]:
            if atual == conteudo:
                self.stdout.write(self.style.SUCCESS("CSS das fontes em dia."))
                return
            self.stderr.write(
                "CSS das fontes desatualizado. Rode: "
                "python manage.py gerar_css_das_fontes"
            )
            raise SystemExit(1)

        if atual == conteudo:
            self.stdout.write("Nada a fazer: o CSS ja esta em dia.")
            return

        destino.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n" de proposito: o arquivo e versionado, e o CRLF do
        # Windows faria o diff mostrar o arquivo inteiro a cada geracao feita
        # numa maquina diferente.
        with destino.open("w", encoding="utf-8", newline="\n") as arquivo:
            arquivo.write(conteudo)

        self.stdout.write(
            self.style.SUCCESS("Escrito: {}".format(destino.name))
        )
