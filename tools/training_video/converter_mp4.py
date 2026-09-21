"""
Converte os .webm gravados para .mp4.

Por que existe
--------------
O Playwright grava em WebM/VP8 e nao sabe gerar outra coisa: o ffmpeg que ele
traz embutido foi compilado so com libvpx e png, sem nenhum muxer de MP4. E
MP4 e o formato que abre sem drama no WhatsApp, no YouTube e em qualquer
editor — que e para onde estes videos vao.

Entao aqui procuramos um ffmpeg de verdade. Se nao houver, o script explica
como instalar e sai sem erro: o WebM ja e um entregavel valido, e faltar MP4
nao pode derrubar a producao dos videos.

Nada disso encosta na aplicacao. ffmpeg e ferramenta de quem edita video, nao
dependencia do Django.

Uso
---
    tools/training_video/.venv/Scripts/python tools/training_video/converter_mp4.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PASTA = RAIZ / "artifacts" / "training-video"


def achar_ffmpeg():
    """
    Um ffmpeg capaz de gerar MP4.

    O do PATH vem primeiro. Depois o do winget, porque a instalacao dele
    altera o PATH apenas para shells abertos DEPOIS — e quem acabou de
    instalar costuma estar no shell de antes.

    O ffmpeg do Playwright e ignorado de proposito: ele existe, responde
    -version e falha na hora de escrever o arquivo, porque nao tem o muxer.
    """
    doPath = shutil.which("ffmpeg")
    if doPath:
        return Path(doPath)

    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if base.is_dir():
        for candidato in base.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"):
            return candidato
    return None


def converter(ffmpeg, origem):
    destino = origem.with_suffix(".mp4")
    print("  {} -> {}".format(origem.name, destino.name))

    comando = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(origem),
        # H.264 em yuv420p e a combinacao que toca em celular antigo e em
        # player de rede social. Perfis mais novos economizariam banda e
        # falhariam justamente no aparelho de quem mais precisa do video.
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        # Joga o indice para o comeco do arquivo: o video comeca a tocar
        # antes de terminar de baixar.
        "-movflags", "+faststart",
        str(destino),
    ]
    subprocess.run(comando, check=True)

    mb = destino.stat().st_size / (1024 * 1024)
    print("     {:.1f} MB".format(mb))
    return destino


def main():
    origens = sorted(PASTA.glob("*.webm"))
    if not origens:
        print("Nenhum .webm em {}. Grave antes de converter.".format(PASTA))
        return 0

    ffmpeg = achar_ffmpeg()
    if ffmpeg is None:
        print("ffmpeg nao encontrado. Os .webm continuam validos.")
        print("Para gerar MP4 tambem:")
        print("    winget install --id Gyan.FFmpeg")
        print("e rode este script de novo, num shell novo.")
        return 0

    print("ffmpeg: {}".format(ffmpeg))
    for origem in origens:
        converter(ffmpeg, origem)
    return 0


if __name__ == "__main__":
    sys.exit(main())
