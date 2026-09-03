"""
A logo institucional nas telas.

Tres perguntas atravessam o arquivo, e a terceira e a que mais importa:

    a logo aparece onde deveria?
    o caminho dela e um static valido, e nao uma string que parece um?
    ela NAO aparece onde nao deveria — dentro do PDF do certificado?

A ultima existe porque o certificado ja carrega a identidade AD Bras na
propria arte de fundo. Um segundo logo desenhado por cima sairia impresso,
duplicado, em documento oficial — e ninguem repara nisso olhando a tela do
editor, so no papel.
"""

import pathlib
import re

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import reverse

pytestmark = pytest.mark.django_db

RAIZ = pathlib.Path(__file__).resolve().parents[2]

# O `<img>` da marca, onde quer que ele apareca.
LOGO = re.compile(r"<img[^>]*cpo-marca-app__logo[^>]*>", re.S)


def imagens_da_marca(corpo):
    return LOGO.findall(corpo)


def atributo(tag, nome):
    achado = re.search(r'%s="([^"]*)"' % nome, tag)
    return achado.group(1) if achado else None


# ---------------------------------------------------------------------------
# O asset
# ---------------------------------------------------------------------------


def test_o_caminho_da_logo_esta_em_settings():
    assert settings.APP_LOGO == "branding/adbras-sumare-logo.jpg"
    assert settings.APP_LOGO_ALT


def test_o_context_processor_expoe_a_logo():
    from common.context_processors import institution

    contexto = institution(None)

    assert contexto["APP_LOGO"] == settings.APP_LOGO
    assert contexto["APP_LOGO_ALT"] == settings.APP_LOGO_ALT


def test_o_arquivo_existe_e_o_django_o_encontra():
    """
    Nao basta o arquivo estar no disco: ele precisa estar onde os finders do
    staticfiles procuram, senao o collectstatic nao o leva e producao devolve
    404 com a pagina inteira funcionando.
    """
    encontrado = finders.find(settings.APP_LOGO)

    assert encontrado is not None, "os finders nao acham a logo"
    assert pathlib.Path(encontrado).is_file()
    assert pathlib.Path(encontrado).stat().st_size > 0


def test_o_arquivo_e_uma_imagem_de_verdade():
    from PIL import Image

    with Image.open(finders.find(settings.APP_LOGO)) as imagem:
        largura, altura = imagem.size
        formato = imagem.format

    assert formato == "JPEG"
    # Quadrada. As caixas da marca sao quadradas por causa disto; se um dia o
    # arquivo virar retangular, o object-fit evita a distorcao mas a caixa
    # ganha faixa vazia — e este teste avisa antes.
    assert largura == altura
    assert largura >= 320, "pequena demais para a caixa de 120px do login"


def test_o_arquivo_nao_foi_alterado():
    """
    A logo e o asset oficial. Recorte, recompressao ou remocao de fundo sao
    exatamente o que o pedido proibe — e nenhum dos tres deixa aviso.
    """
    import hashlib

    caminho = pathlib.Path(finders.find(settings.APP_LOGO))
    soma = hashlib.sha256(caminho.read_bytes()).hexdigest()

    assert soma == (
        "1ed1a42480bb486b91b72d9aacc288db8ad0e91262c166e4c19932f72bb49ecf"
    )


def test_a_url_estatica_resolve():
    url = static(settings.APP_LOGO)

    assert url.startswith(settings.STATIC_URL)
    assert url.endswith(".jpg")


def test_a_logo_fica_dentro_do_static_do_projeto():
    caminho = pathlib.Path(finders.find(settings.APP_LOGO)).resolve()

    assert any(
        caminho.is_relative_to(pasta.resolve())
        for pasta in settings.STATICFILES_DIRS
    )


# ---------------------------------------------------------------------------
# As telas
# ---------------------------------------------------------------------------


def test_o_login_traz_a_logo_o_nome_e_o_subtitulo(client):
    corpo = client.get(reverse("accounts:login")).content.decode("utf-8")

    marcas = imagens_da_marca(corpo)
    assert len(marcas) == 1

    assert atributo(marcas[0], "src") == static(settings.APP_LOGO)
    assert atributo(marcas[0], "alt") == settings.APP_LOGO_ALT

    # A logo acompanha os textos; nao os substitui.
    assert settings.APP_NAME in corpo
    assert settings.APP_SUBTITLE in corpo
    assert "cpo-marca-app__emblema--grande" in corpo


def test_a_troca_de_senha_tambem_traz_a_marca(admin_client_logado):
    """
    Tela da ADMINISTRACAO, e nao do aluno: a senha do aluno e definida pela
    coordenacao, e quem for aluno recebe 403 aqui. Usa a mesma casca do login,
    entao carrega a mesma marca.
    """
    corpo = admin_client_logado.get(
        reverse("accounts:change_password")
    ).content.decode("utf-8")

    assert len(imagens_da_marca(corpo)) == 1
    assert "cpo-marca-app__emblema--grande" in corpo


def test_o_aluno_nao_alcanca_a_troca_de_senha(student_client_logado):
    """A regra que o teste acima descobriu. Vale registrar."""
    resposta = student_client_logado.get(reverse("accounts:change_password"))

    assert resposta.status_code == 403


def test_o_painel_administrativo_traz_a_logo(admin_client_logado):
    corpo = admin_client_logado.get(
        reverse("admin_panel:dashboard")
    ).content.decode("utf-8")

    marcas = imagens_da_marca(corpo)
    # Lateral fixa, cabecalho do offcanvas e barra do topo mobile. Sao tres
    # lugares porque sao tres larguras de tela, e o CSS mostra um por vez.
    assert len(marcas) == 3
    for tag in marcas:
        assert atributo(tag, "src") == static(settings.APP_LOGO)
        assert atributo(tag, "alt") == settings.APP_LOGO_ALT

    assert "cpo-marca-app__emblema--lateral" in corpo
    assert "Painel administrativo" in corpo


def test_o_menu_mobile_do_admin_traz_a_marca(admin_client_logado):
    corpo = admin_client_logado.get(
        reverse("admin_panel:dashboard")
    ).content.decode("utf-8")

    offcanvas = re.search(
        r'<div class="offcanvas offcanvas-start.*?</div>\s*</div>\s*</div>',
        corpo,
        re.S,
    )
    assert offcanvas, "offcanvas do menu administrativo nao encontrado"
    assert imagens_da_marca(offcanvas.group(0))
    assert settings.APP_NAME in offcanvas.group(0)


def test_a_area_do_aluno_traz_a_logo(student_client_logado):
    corpo = student_client_logado.get(
        reverse("student:dashboard")
    ).content.decode("utf-8")

    marcas = imagens_da_marca(corpo)
    assert len(marcas) == 1
    assert atributo(marcas[0], "alt") == settings.APP_LOGO_ALT
    assert "Area do aluno" in corpo


def test_o_nome_completo_sobrevive_ao_celular(student_client_logado):
    """
    Abaixo de sm a barra mostra "CPO" por falta de espaco. O nome completo
    continua no aria-label do link — a logo nunca e a unica fonte dele.
    """
    corpo = student_client_logado.get(
        reverse("student:dashboard")
    ).content.decode("utf-8")

    link = re.search(r'<a class="cpo-marca cpo-marca-app.*?</a>', corpo, re.S)
    assert link
    assert 'aria-label="{} - Area do aluno"'.format(settings.APP_NAME) in link.group(0)
    assert 'title="{}"'.format(settings.APP_NAME) in link.group(0)


TELAS_DO_ALUNO = [
    "student:dashboard",
    "student:certificate_list",
]


@pytest.mark.parametrize("nome", TELAS_DO_ALUNO)
def test_as_telas_do_aluno_herdam_a_marca(student_client_logado, nome):
    """
    Herdam do layout, e nao repetem `<img>` cada uma. Uma marca por tela.
    """
    corpo = student_client_logado.get(reverse(nome)).content.decode("utf-8")

    assert len(imagens_da_marca(corpo)) == 1


TELAS_ADMIN = [
    "admin_panel:dashboard",
    "admin_panel:student_list",
    "admin_panel:certificate_list",
    "admin_panel:audit_log_list",
    "admin_panel:certificate_template_list",
]


@pytest.mark.parametrize("nome", TELAS_ADMIN)
def test_as_telas_administrativas_herdam_a_marca(admin_client_logado, nome):
    corpo = admin_client_logado.get(reverse(nome)).content.decode("utf-8")

    assert len(imagens_da_marca(corpo)) == 3


def test_a_validacao_publica_tem_identidade(client, student_user):
    """
    Quem chega aqui veio pelo QR de um documento impresso e nao tem conta. A
    pagina precisa dizer de quem ela e antes de dizer qualquer outra coisa.
    """
    from common.tests.test_branding import certificado_direto

    certificado = certificado_direto(student_user, codigo="LOGO1")
    corpo = client.get(
        reverse(
            "certificates:validate",
            kwargs={"verification_code": certificado.verification_code},
        )
    ).content.decode("utf-8")

    assert len(imagens_da_marca(corpo)) == 1
    assert "cpo-marca-app__emblema--medio" in corpo
    # A imagem acompanha o nome escrito; conferir documento nao pode depender
    # de a imagem ter carregado.
    assert settings.INSTITUTION_NAME in corpo


# ---------------------------------------------------------------------------
# Como a logo entra no HTML
# ---------------------------------------------------------------------------


def test_nenhuma_logo_fica_sem_texto_alternativo(client, admin_client_logado):
    corpos = [
        client.get(reverse("accounts:login")).content.decode("utf-8"),
        admin_client_logado.get(
            reverse("admin_panel:dashboard")
        ).content.decode("utf-8"),
    ]

    for corpo in corpos:
        for tag in imagens_da_marca(corpo):
            assert atributo(tag, "alt"), "logo institucional com alt vazio"


def test_o_tamanho_vem_do_css_e_nao_de_style_no_markup(admin_client_logado):
    """
    Sem isto a identidade voltaria a morar em dezenas de arquivos — que e o
    problema que o componente existe para resolver.
    """
    corpo = admin_client_logado.get(
        reverse("admin_panel:dashboard")
    ).content.decode("utf-8")

    for tag in imagens_da_marca(corpo):
        assert "style=" not in tag


def test_o_markup_da_marca_mora_em_um_arquivo_so():
    """
    Um unico template escreve o `<img>` da logo. Os layouts o incluem.
    """
    escrevem = [
        caminho
        for caminho in (RAIZ / "templates").rglob("*.html")
        if "cpo-marca-app__logo" in caminho.read_text(encoding="utf-8")
    ]

    assert [c.name for c in escrevem] == ["_marca.html"]


def test_nenhuma_tela_busca_a_logo_de_fora(client, admin_client_logado, student_client_logado):
    """A imagem e servida pelos staticfiles. Nada de CDN."""
    corpos = [
        client.get(reverse("accounts:login")).content.decode("utf-8"),
        admin_client_logado.get(reverse("admin_panel:dashboard")).content.decode("utf-8"),
        student_client_logado.get(reverse("student:dashboard")).content.decode("utf-8"),
    ]

    for corpo in corpos:
        for tag in imagens_da_marca(corpo):
            src = atributo(tag, "src")
            assert src.startswith(settings.STATIC_URL)
            assert "//" not in src.replace(settings.STATIC_URL, "", 1)
            assert not src.startswith("http")


def test_o_css_da_marca_existe_e_nao_distorce():
    css = (RAIZ / "static" / "css" / "app.css").read_text(encoding="utf-8")

    for classe in (
        ".cpo-marca-app",
        ".cpo-marca-app__emblema",
        ".cpo-marca-app__logo",
        ".cpo-marca-app__nome",
        ".cpo-marca-app__sub",
    ):
        assert classe in css, classe

    bloco = css.split(".cpo-marca-app__logo {", 1)[1].split("}", 1)[0]
    assert "object-fit: contain" in bloco
    assert "max-width: 100%" in bloco
    assert "height: auto" in bloco


def test_o_tamanho_no_login_respeita_a_faixa_pedida():
    """
    100–140px no desktop, 80–110px no celular. O clamp entrega 84px na
    largura menor e 120px no maior, e nada entre os dois passa disso — e o
    que impede a logo de empurrar o formulario para fora da tela.
    """
    css = (RAIZ / "static" / "css" / "app.css").read_text(encoding="utf-8")
    bloco = css.split(".cpo-marca-app__emblema--grande {", 1)[1].split("}", 1)[0]

    assert "clamp(5.25rem, 22vw, 7.5rem)" in bloco  # 84px .. 120px


# ---------------------------------------------------------------------------
# O que a logo NAO deve fazer
# ---------------------------------------------------------------------------

MODULOS_DO_DOCUMENTO = (
    "certificates/render.py",
    "certificates/pdf.py",
    "certificates/snapshot.py",
)


@pytest.mark.parametrize("modulo", MODULOS_DO_DOCUMENTO)
def test_o_renderizador_do_pdf_nao_conhece_a_logo(modulo):
    """
    O guarda estrutural do "nao duplique a identidade no documento".

    O certificado ja traz a marca AD Bras na propria arte de fundo. Se um dia
    alguem achar que o PDF "tambem precisa" da logo, e desenhar por codigo, o
    documento sai com duas — e a descoberta acontece impressa. Este teste
    falha antes.

    Quem quiser a logo no certificado poe como elemento do modelo, no editor,
    de proposito e visivel no preview.
    """
    fonte = (RAIZ / modulo).read_text(encoding="utf-8")

    assert "APP_LOGO" not in fonte
    assert "branding/" not in fonte


def test_o_pdf_do_certificado_nao_ganha_imagem_a_mais(
    db, admin_user, student_user, modelo_de_certificado
):
    """
    A prova no documento, e nao so no codigo.

    O modelo de teste tem arte de fundo e um QR. O PDF precisa sair com essas
    DUAS imagens — nem mais uma.
    """
    import re as regex

    from certificates.render import render_from_snapshot
    from certificates.snapshot import montar_snapshot, valores_de_preview

    pdf = render_from_snapshot(
        montar_snapshot(modelo_de_certificado), valores_de_preview()
    )

    imagens = regex.findall(rb"/Subtype\s*/Image", pdf)

    assert len(imagens) == 2, "o documento ganhou imagem que ninguem configurou"
