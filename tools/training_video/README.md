# Vídeos de treinamento do aluno

Ferramenta de desenvolvimento para gravar a demonstração do fluxo do aluno.

**Nada aqui roda em produção.** O Playwright vive num venv próprio
(`tools/training_video/.venv`), fora de `requirements.txt`, e nenhum módulo da
aplicação importa este diretório. O venv e a pasta de saída estão no
`.gitignore`.

## O que é gravado

| Arquivo | Cenário | Resultado |
|---|---|---|
| `01-como-realizar-a-prova-aprovado.webm` | acerta as 4 questões | 4/4 → 10,00 → Aprovado |
| `02-resultado-reprovado.webm` | acerta 2 de 4 | 2/4 → 5,00 → Reprovado |

Os roteiros para narração estão em `roteiro-aprovado.txt` e
`roteiro-reprovado.txt`.

## Preparar o ambiente (uma vez)

```bash
python -m venv tools/training_video/.venv
tools/training_video/.venv/Scripts/python -m pip install playwright
tools/training_video/.venv/Scripts/python -m playwright install chromium
```

## Gravar

1. Monte (ou reative) o cenário e libere as tentativas:

   ```bash
   python manage.py preparar_demo_video --reset
   ```

   O comando imprime a senha sorteada **uma única vez**. Guarde-a: ela não é
   gravada em arquivo nem na trilha de auditoria. Para usar uma senha sua,
   defina `DEMO_VIDEO_PASSWORD` antes de rodar.

2. Grave, exportando a mesma senha:

   ```bash
   export DEMO_VIDEO_PASSWORD='...'
   tools/training_video/.venv/Scripts/python tools/training_video/record_approved.py
   tools/training_video/.venv/Scripts/python tools/training_video/record_failed.py
   ```

   Os vídeos saem em `artifacts/training-video/`.

3. Para regravar, rode o `--reset` de novo: a prova DEMO permite uma
   tentativa por aluno, e sem o reset a segunda gravação não consegue começar.

### Gravar contra um servidor local

```bash
export DEMO_VIDEO_URL='http://127.0.0.1:8099'
```

Por padrão a gravação aponta para `https://cpoadsum.nexeeo.com`, que é o que
o aluno realmente usa.

## Converter para MP4

O Playwright grava `.webm`. O ffmpeg que ele já traz converte para MP4 sem
instalar nada na aplicação:

```bash
"$LOCALAPPDATA/ms-playwright/ffmpeg-1011/ffmpeg-win64.exe" \
  -i artifacts/training-video/01-como-realizar-a-prova-aprovado.webm \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
  artifacts/training-video/01-como-realizar-a-prova-aprovado.mp4
```

## Encerrar depois de gravar

```bash
python manage.py limpar_demo_video              # dry run, só conta
python manage.py limpar_demo_video --desativar  # fecha as contas DEMO
```

`--desativar` é o encerramento normal: desativa os dois usuários e o módulo,
preservando o cenário. Para gravar de novo basta rodar `preparar_demo_video`,
que reativa tudo.

Para apagar o cenário inteiro:

```bash
python manage.py limpar_demo_video --remover --confirm REMOVER-CENARIO-DEMO
```

A trilha de auditoria nunca é apagada por nenhum dos dois.

## Onde mora o cenário

Em [`common/demo_video.py`](../../common/demo_video.py): os dois alunos, o
módulo, a prova, as quatro questões e quais alternativas cada cenário marca.

Fonte única de propósito. O comando de gestão **cria** esses registros e o
script do Playwright **clica** neles; se cada ponta tivesse sua cópia do texto
das alternativas, bastaria reescrever um enunciado para a gravação passar a
clicar no lugar errado.

O módulo não importa Django — só `decimal` — justamente para ser legível pelo
venv da gravação, que não tem Django instalado.

## Certificado

O vídeo do aprovado só mostra a emissão do certificado se houver um modelo de
certificado **ativo** e o módulo DEMO apontar para ele. `preparar_demo_video`
faz esse apontamento no módulo DEMO, e **apenas** nele — nunca ativa um modelo
nem o torna global, porque ativar troca a aparência de todo documento emitido
dali para a frente.

Se não houver modelo ativo, a gravação registra o aviso, termina no resultado
e não falha.
