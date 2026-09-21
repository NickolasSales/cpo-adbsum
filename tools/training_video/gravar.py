"""
Driver das gravacoes de treinamento.

O que este arquivo e
--------------------
Ferramenta de desenvolvimento. Nao e importado por nada em `config`, `common`,
`exams`, `courses`, `certificates` ou `accounts`, nao entra em requirements.txt
e nao roda na EC2. Vive num venv proprio (tools/training_video/.venv) para que
nem um `pip freeze` distraido consiga levar Playwright para producao.

O que ele faz
-------------
Abre um Chromium, entra em https://cpoadsum.nexeeo.com como aluno DEMO e faz
exatamente o que um aluno faria: le as instrucoes, inicia, marca alternativa,
espera o "Salvo", finaliza, ve o resultado. O video sai do proprio Playwright.

De onde vem o que ele clica
---------------------------
De common.demo_video, o mesmo modulo que o comando de gestao usa para CRIAR a
prova. O modulo nao importa Django — so `decimal` — justamente para poder ser
lido daqui, de um interpretador que nao tem Django instalado.

Se as duas pontas guardassem a propria copia do texto das alternativas,
bastaria alguem reescrever um enunciado para a gravacao passar a clicar no
lugar errado, e o video sairia com a resposta trocada sem ninguem perceber.

Ritmo
-----
As pausas sao generosas de proposito. O video e material de treinamento: quem
assiste precisa acompanhar o cursor, ler o enunciado e enxergar o "Salvo"
aparecer. Automacao no ritmo natural do Playwright produziria um video em que
a tela muda antes de a pessoa terminar de ler.

Uso
---
    set DEMO_VIDEO_PASSWORD=...
    tools/training_video/.venv/Scripts/python tools/training_video/record_approved.py
"""

import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from common import demo_video as cenario  # noqa: E402

# --- ambiente --------------------------------------------------------------

URL_BASE = os.environ.get("DEMO_VIDEO_URL", "https://cpoadsum.nexeeo.com").rstrip("/")
PASTA_DE_SAIDA = RAIZ / "artifacts" / "training-video"

# 1366x768 e a resolucao de notebook mais comum no Brasil e cabe inteira num
# player do WhatsApp sem o espectador precisar dar zoom. Gravar em 4K faria os
# elementos ficarem pequenos demais exatamente para quem mais precisa ver.
VIEWPORT = {"width": 1366, "height": 768}

# --- ritmo (em milissegundos) ----------------------------------------------

LEITURA = 3500      # tempo para ler uma tela nova
PAUSA = 2000        # respiro entre acoes
CURTA = 1200        # entre dois cliques da mesma acao
MODAL = 2500        # o aviso de "nao podera alterar" precisa ser lido

# Atraso que o Playwright aplica a CADA acao. Sem isso o clique acontece no
# mesmo frame em que o cursor chega, e o video fica com elementos mudando
# sozinhos.
LENTIDAO = 450

# Janela VISIVEL, e nao headless.
#
# Nao e preferencia: o Chromium headless nao carrega o visualizador de PDF.
# Pedir o arquivo por file:// em headless devolve "Download is starting" e o
# certificado nunca aparece na tela. Com janela visivel o visualizador abre
# normalmente, com miniatura, zoom e os botoes de baixar e imprimir — que e o
# que o aluno vai ver quando abrir o documento.
#
# Uma janela do Chromium abre durante a gravacao. Nao interaja com ela.
HEADLESS = os.environ.get("DEMO_VIDEO_HEADLESS", "").strip() == "1"


def _pausar(page, ms, motivo=""):
    """Pausa didatica. O motivo aparece no log para auditar o ritmo depois."""
    if motivo:
        print("    ... {} ({}ms)".format(motivo, ms))
    page.wait_for_timeout(ms)


def _exigir_senha():
    senha = os.environ.get(cenario.VARIAVEL_DA_SENHA, "").strip()
    if not senha:
        raise SystemExit(
            "Defina {} antes de gravar. A senha nao fica no repositorio.\n"
            "Ela e a mesma que `manage.py preparar_demo_video` imprimiu.".format(
                cenario.VARIAVEL_DA_SENHA
            )
        )
    return senha


# --- passos do fluxo -------------------------------------------------------


def entrar(page, email, senha):
    """Login. A senha vai para um campo type=password e nunca para a URL."""
    print("  login")
    page.goto("{}/login/".format(URL_BASE), wait_until="networkidle")
    _pausar(page, LEITURA, "tela de login")

    page.get_by_label("E-mail").fill(email)
    _pausar(page, CURTA)
    page.get_by_label("Senha").fill(senha)
    _pausar(page, CURTA)

    page.get_by_role("button", name="Entrar").click()
    page.wait_for_url("**/aluno/**", timeout=30000)
    _pausar(page, LEITURA, "area do aluno")


def abrir_modulo(page):
    print("  modulo de demonstracao")
    cartao = page.locator(".card", has_text=cenario.CODIGO_DO_MODULO)
    if cartao.count() == 0:
        cartao = page.locator("body")
    cartao.first.scroll_into_view_if_needed()
    _pausar(page, PAUSA, "localizar o modulo")

    page.get_by_role("link", name="Acessar modulo").first.click()
    page.wait_for_load_state("networkidle")
    _pausar(page, LEITURA, "modulo aberto")


def abrir_instrucoes(page):
    print("  instrucoes")
    page.get_by_role("link", name="Ver instrucoes").first.click()
    page.wait_for_load_state("networkidle")
    # A tela mais importante do video: e aqui que o aluno descobre quanto
    # tempo tem, quantas questoes sao e que as respostas salvam sozinhas.
    _pausar(page, LEITURA + 2500, "ler as instrucoes com calma")


def iniciar(page):
    print("  iniciar prova")
    page.get_by_role("button", name="Iniciar prova").click()
    page.wait_for_url("**/tentativas/**", timeout=30000)
    page.wait_for_load_state("networkidle")
    _pausar(page, PAUSA, "prova aberta")

    # O cronometro so ganha valor depois do primeiro tick do script da pagina.
    relogio = page.locator(".cpo-relogio")
    expect(relogio).not_to_have_text("--:--", timeout=15000)
    relogio.scroll_into_view_if_needed()
    _pausar(page, LEITURA, "mostrar o cronometro correndo")


def _bloco_da_questao(page, enunciado):
    """
    A section da questao, localizada pelo ENUNCIADO.

    Pelo texto e nao pela posicao porque a prova pode ter sorteio de questoes
    ligado: a terceira questao da tela nem sempre e a terceira do cadastro.

    O trecho e cortado em 40 caracteres para nao depender de pontuacao ou de
    quebra de linha que o template possa introduzir no meio do enunciado.
    """
    trecho = enunciado[:40]
    bloco = page.locator("section.cpo-questao").filter(has_text=trecho)
    if bloco.count() != 1:
        raise RuntimeError(
            "Esperava 1 questao contendo {!r}, encontrei {}.".format(
                trecho, bloco.count()
            )
        )
    return bloco


def _marcar(page, bloco, texto):
    alternativa = bloco.get_by_label(texto, exact=True)
    alternativa.scroll_into_view_if_needed()
    _pausar(page, CURTA)
    alternativa.check()


def esperar_salvo(page):
    """
    Espera o aviso "Salvo" (secao 19 do enunciado).

    Nao e so sincronizacao de teste: e o ponto pedagogico do video. O aluno
    precisa aprender que a resposta ficou registrada sem ele clicar em salvar.
    """
    aviso = page.locator(".cpo-estado-salvo")
    expect(aviso).to_have_text("Salvo", timeout=20000)
    _pausar(page, PAUSA, 'destacar o aviso "Salvo"')


def responder(page, enunciado, textos, trocar_antes=None):
    """
    Responde uma questao.

    trocar_antes: alternativa marcada primeiro e depois desmarcada, para o
    video mostrar que da para mudar de ideia antes de finalizar (secao 10).
    """
    print("    questao: {}...".format(enunciado[:45]))
    bloco = _bloco_da_questao(page, enunciado)
    bloco.scroll_into_view_if_needed()
    _pausar(page, PAUSA, "ler o enunciado")

    if trocar_antes:
        _marcar(page, bloco, trocar_antes)
        esperar_salvo(page)
        _pausar(page, PAUSA, "primeira escolha, que sera trocada")

    for texto in textos:
        _marcar(page, bloco, texto)

    esperar_salvo(page)


def finalizar(page):
    print("  finalizar")
    page.get_by_role("button", name="Finalizar prova").click()
    modal = page.locator("#modalFinalizar")
    expect(modal).to_be_visible(timeout=10000)
    # O aviso de que nao dara para alterar depois precisa ficar legivel.
    _pausar(page, MODAL, "ler o aviso do modal")

    page.locator("#cpo-confirmar-envio").click()

    # O envio nao cai direto no resultado: passa por uma tela de confirmacao
    # ("Prova enviada com sucesso"), que e justamente o que tranquiliza o
    # aluno de que deu certo. Ela fica no video.
    page.wait_for_load_state("networkidle")
    expect(page.locator("body")).to_contain_text(
        "Prova enviada com sucesso", timeout=20000
    )
    _pausar(page, LEITURA, "confirmacao de envio")

    page.get_by_role("link", name="Ver resultado").first.click()
    page.wait_for_url("**/resultados/**", timeout=30000)
    page.wait_for_load_state("networkidle")
    _pausar(page, LEITURA, "resultado na tela")


def mostrar_resultado(page, aprovado):
    """Confere na tela o que o banco vai confirmar depois."""
    corpo = page.locator("body")
    esperado = "Aprovado" if aprovado else "Reprovado"
    expect(corpo).to_contain_text(esperado, timeout=10000)
    print("    resultado na tela: {}".format(esperado))
    _pausar(page, LEITURA, "deixar o resultado visivel")

    if not aprovado:
        # A mensagem administrativa e o que orienta o aluno reprovado.
        expect(corpo).to_contain_text("coordenação", timeout=10000)
        _pausar(page, LEITURA, "ler a mensagem da coordenacao")

        # Secao 11: confirmar visualmente que nao ha como emitir certificado.
        assert page.get_by_role("button", name="Emitir certificado").count() == 0, (
            "apareceu botao de emitir certificado numa tentativa reprovada"
        )
        print("    confirmado: nenhum botao de emitir certificado")


def emitir_certificado(page):
    """
    Emite e mostra o certificado.

    Devolve False sem gravar nada de certificado quando o botao nao existe.
    Isso acontece quando nenhum modelo de certificado esta ativo, e e um
    estado real do sistema — nao um erro do script. Melhor um video que
    termina no resultado do que um script que morre no meio da gravacao.
    """
    print("  certificado")
    botao = page.get_by_role("button", name="Emitir certificado")
    if botao.count() == 0:
        print("    AVISO: nenhum botao de emitir certificado nesta tela.")
        return False

    botao.scroll_into_view_if_needed()
    _pausar(page, PAUSA, "onde fica o botao de emitir")
    botao.click()
    page.wait_for_load_state("networkidle")

    # A emissao bem-sucedida JA cai na lista de certificados; nao ha um
    # segundo clique. Quando ela e recusada, a view devolve o aluno para o
    # resultado com a mensagem do dominio — e e por esta diferenca de URL,
    # e nao por adivinhar texto, que o script sabe o que aconteceu.
    if "/certificados" not in page.url:
        alerta = page.locator(".alert").first
        motivo = alerta.inner_text().strip() if alerta.count() else "(sem mensagem)"
        print("    AVISO: a emissao foi recusada pelo sistema.")
        print("    Motivo na tela: {}".format(motivo))
        return False

    _pausar(page, LEITURA, "lista de certificados")
    endereco_da_lista = page.url

    # --- baixar e ABRIR o documento ---------------------------------------
    #
    # Mostrar so onde fica o botao deixava a parte mais importante de fora: o
    # aluno quer ver o certificado. Entao baixamos de verdade e abrimos o
    # arquivo, que e exatamente o que ele fara.
    #
    # A view manda Content-Disposition: attachment, entao o navegador baixa em
    # vez de exibir. Abrir o arquivo salvo depois nao e um truque de gravacao:
    # e o segundo passo real de quem clicou em baixar.
    baixar = page.get_by_role("link", name="Baixar PDF").first
    baixar.scroll_into_view_if_needed()
    baixar.hover()
    _pausar(page, PAUSA, "apontar o botao Baixar PDF")

    with page.expect_download(timeout=60000) as captura:
        baixar.click()
    arquivo = captura.value

    destino = PASTA_DE_SAIDA / "certificado-demonstracao.pdf"
    arquivo.save_as(str(destino))
    print("    PDF salvo: {}".format(destino))
    _pausar(page, PAUSA, "download concluido")

    # Mesma aba, e nao uma nova: o Playwright grava um video POR PAGINA, e
    # abrir outra aba partiria a gravacao em dois arquivos.
    page.goto(destino.as_uri())

    # Parada longa, e nao zoom. Tentei Control+= aqui: o visualizador de PDF
    # e uma extensao do proprio Chromium e nao responde ao atalho enviado
    # pela pagina — o video ficava com quatro segundos em que nada acontecia.
    # A 93% o documento ja cabe inteiro na tela, entao o que falta e tempo
    # para ler, nao aproximacao.
    _pausar(page, LEITURA * 2 + 2000, "o certificado na tela, para leitura")

    # --- de volta ao sistema ----------------------------------------------
    page.goto(endereco_da_lista, wait_until="networkidle")
    _pausar(page, PAUSA, "de volta a lista")

    page.get_by_role("link", name="Pagina de validacao").first.click()
    page.wait_for_load_state("networkidle")
    _pausar(page, LEITURA + 1500, "pagina publica de validacao")
    return True


# --- orquestracao ----------------------------------------------------------


def gravar(*, aprovado, arquivo, com_certificado):
    """
    Grava um cenario inteiro e devolve o caminho do .webm.

    O video e do CONTEXTO, e o Playwright so o finaliza quando o contexto
    fecha. Por isso o rename acontece depois do close, e nao antes.
    """
    senha = _exigir_senha()
    email = cenario.EMAIL_APROVADO if aprovado else cenario.EMAIL_REPROVADO
    roteiro = cenario.roteiro_de_respostas(aprovado=aprovado)

    PASTA_DE_SAIDA.mkdir(parents=True, exist_ok=True)
    destino = PASTA_DE_SAIDA / arquivo

    print("Gravando {} ({})".format(arquivo, "aprovado" if aprovado else "reprovado"))
    print("  alvo: {}".format(URL_BASE))

    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=HEADLESS, slow_mo=LENTIDAO)
        contexto = navegador.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(PASTA_DE_SAIDA),
            record_video_size=VIEWPORT,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            accept_downloads=True,
        )
        page = contexto.new_page()
        try:
            entrar(page, email, senha)
            abrir_modulo(page)
            abrir_instrucoes(page)
            iniciar(page)

            for indice, (enunciado, textos) in enumerate(roteiro):
                # Na segunda questao do video do aprovado, mostrar que da
                # para trocar a resposta antes de finalizar (secao 10).
                trocar = None
                if aprovado and indice == 1:
                    trocar = _outra_alternativa(indice, textos)
                responder(page, enunciado, textos, trocar_antes=trocar)

            # Um ultimo olhar no cronometro antes de enviar.
            page.locator(".cpo-relogio").scroll_into_view_if_needed()
            _pausar(page, PAUSA, "conferir o tempo restante")

            finalizar(page)
            mostrar_resultado(page, aprovado)

            if aprovado and com_certificado:
                emitir_certificado(page)

            _pausar(page, PAUSA, "encerramento")
        finally:
            video = page.video
            contexto.close()
            navegador.close()

        bruto = Path(video.path())
        if destino.exists():
            destino.unlink()
        bruto.rename(destino)

    print("  video: {}".format(destino))
    return destino


def _outra_alternativa(indice, corretas):
    """Uma alternativa incorreta da questao, para a demonstracao de troca."""
    questao = cenario.QUESTOES[indice]
    for alternativa in questao["alternativas"]:
        if alternativa["texto"] not in corretas:
            return alternativa["texto"]
    return None
