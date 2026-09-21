"""
Grava o video do aluno APROVADO.

Fluxo completo: login, area do aluno, modulo, instrucoes, inicio, as quatro
questoes com autosave, cronometro, finalizacao com o aviso, resultado
Aprovado, emissao do certificado, lista de certificados e pagina publica de
validacao.

    set DEMO_VIDEO_PASSWORD=...
    tools/training_video/.venv/Scripts/python tools/training_video/record_approved.py

Precisa do cenario preparado e sem tentativa gasta:

    python manage.py preparar_demo_video --reset
"""

from gravar import gravar

if __name__ == "__main__":
    gravar(
        aprovado=True,
        arquivo="01-como-realizar-a-prova-aprovado.webm",
        com_certificado=True,
    )
