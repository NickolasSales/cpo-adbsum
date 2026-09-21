"""
Grava o video do aluno REPROVADO.

Mesmo caminho do aprovado ate a finalizacao, mas com duas questoes erradas:
2 de 4 pontos, nota 5,00, abaixo da minima de 8,00.

O video mostra a nota, a nota minima, o status Reprovado e a mensagem da
coordenacao — e confirma, na propria tela, que nao existe botao para emitir
certificado.

    set DEMO_VIDEO_PASSWORD=...
    tools/training_video/.venv/Scripts/python tools/training_video/record_failed.py

Precisa do cenario preparado e sem tentativa gasta:

    python manage.py preparar_demo_video --reset
"""

from gravar import gravar

if __name__ == "__main__":
    gravar(
        aprovado=False,
        arquivo="02-resultado-reprovado.webm",
        com_certificado=False,
    )
