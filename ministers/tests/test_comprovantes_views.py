"""Comprovantes nas telas (Fase 2E): portal do ministro e painel administrativo."""

import datetime
import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from audit.models import AuditEvent, AuditLog
from ministers import cobranca, comprovantes, services
from ministers import mensalidades as financeiro
from ministers.models import MinisterPaymentProof, MonthlyFeeStatus, ProofStatus
from ministers.tests.conftest import (
    URL_LOGOUT,
    arquivo_enviado,
    dados_de_ministro,
    entrar_no_portal,
    imagem_de_teste,
    pdf_de_teste,
)

pytestmark = pytest.mark.django_db

HOJE = timezone.localdate()
ONTEM = HOJE - datetime.timedelta(days=1)
DAQUI_A_10 = HOJE + datetime.timedelta(days=10)

OUTUBRO = "/ministros/mensalidades/2026/10/"
ENVIO_OUTUBRO = "/ministros/mensalidades/2026/10/comprovante/"
ARQUIVO_1 = "/ministros/mensalidades/2026/10/comprovantes/1/"
GUIA_OUTUBRO = "/ministros/mensalidades/2026/10/guia.pdf"
LISTA_PORTAL = "/ministros/mensalidades/"

FILA = "/admin-panel/ministros/comprovantes/"
SENHA_DE_B = "Evangelista#2026x"


def url_admin(comprovante, sufixo=""):
    return "/admin-panel/ministros/comprovantes/{}/{}".format(comprovante.pk, sufixo)


def html(resposta):
    return resposta.content.decode()


def nova(ministro, competencia="2026-10", valor="35.00", vencimento=DAQUI_A_10, cobrar=True):
    mensalidade = financeiro.create_fee(
        minister=ministro, reference_month=competencia, due_date=vencimento, amount=valor
    )
    if cobrar:
        cobranca.gerar_cobranca(mensalidade)
    return mensalidade


def enviar(mensalidade, conteudo=None, nome="comprovante.pdf"):
    return comprovantes.enviar_comprovante(mensalidade, arquivo_enviado(conteudo or pdf_de_teste(), nome))


def postar(client, conteudo=None, nome="comprovante.pdf", url=ENVIO_OUTUBRO, **extra):
    return client.post(url, {"arquivo": arquivo_enviado(conteudo or pdf_de_teste(), nome), **extra})


@pytest.fixture
def pendente(ministro_com_acesso, configuracao_pix):
    return nova(ministro_com_acesso)


@pytest.fixture
def portal(ministro_client_logado, pendente):
    return ministro_client_logado


def corpo_do_arquivo(resposta):
    return b"".join(resposta.streaming_content)


# ---------------------------------------------------------------------------
# Portal: envio
# ---------------------------------------------------------------------------


def test_cobranca_pendente_oferece_enviar_comprovante(portal):
    pagina = html(portal.get(OUTUBRO))
    assert "Copiar Pix" in pagina
    assert 'href="{}"'.format(ENVIO_OUTUBRO) in pagina
    assert "Enviar comprovante" in pagina


def test_formulario_de_envio(portal):
    resposta = portal.get(ENVIO_OUTUBRO)
    assert resposta.status_code == 200
    pagina = html(resposta)
    assert "Enviar comprovante" in pagina and "Selecionar comprovante" in pagina
    assert "Outubro/2026" in pagina and "R$ 35,00" in pagina
    assert "PDF, JPG ou PNG" in pagina and "Máximo 5 MB" in pagina
    assert 'enctype="multipart/form-data"' in pagina
    assert 'accept="application/pdf,image/jpeg,image/png' in pagina
    assert 'data-limite="5242880"' in pagina
    # So o arquivo e enviado: nada de valor, data, situacao ou ids.
    campos = set(re.findall(r'name="([^"]+)"', pagina.split("data-envio-comprovante", 1)[1].split("</form>", 1)[0]))
    assert campos == {"csrfmiddlewaretoken", "arquivo"}


def test_envio_valido_cria_um_submitted_e_volta_para_a_cobranca(portal, pendente):
    resposta = postar(portal)
    assert resposta.status_code == 302 and resposta.url == OUTUBRO
    (c,) = MinisterPaymentProof.objects.all()
    assert c.status == ProofStatus.SUBMITTED and c.fee_id == pendente.pk
    pagina = html(portal.get(OUTUBRO))
    assert "Comprovante recebido!" in pagina
    assert "Sua mensalidade de Outubro/2026 está aguardando conferência." in pagina
    assert "Você poderá acompanhar o status por aqui." in pagina


@pytest.mark.parametrize("formato, nome", [("PNG", "print.png"), ("JPEG", "foto.jpg")])
def test_envio_de_imagem(portal, formato, nome):
    assert postar(portal, imagem_de_teste(formato), nome).status_code == 302
    assert MinisterPaymentProof.objects.get().mime_type.startswith("image/")


def test_campos_forjados_sao_ignorados(portal, pendente, outro_ministro, configuracao_pix):
    de_b = nova(outro_ministro, competencia="2026-09")
    postar(
        portal,
        amount="0.01",
        status="PAID",
        paid_at="2026-10-01",
        minister_id=outro_ministro.pk,
        fee_id=de_b.pk,
        fee=de_b.pk,
    )
    (c,) = MinisterPaymentProof.objects.all()
    assert c.fee_id == pendente.pk
    pendente.refresh_from_db()
    assert pendente.status == MonthlyFeeStatus.PENDING and pendente.amount == 35
    assert not MinisterPaymentProof.objects.filter(fee=de_b).exists()


def test_arquivo_invalido_mostra_o_erro_no_formulario(portal):
    resposta = postar(portal, b"MZ\x90\x00" + b"\x00" * 100, "comprovante.jpg")
    assert resposta.status_code == 200
    assert "não é um PDF, JPG ou PNG válido" in html(resposta)
    assert not MinisterPaymentProof.objects.exists()


def test_sem_arquivo(portal):
    resposta = portal.post(ENVIO_OUTUBRO, {})
    assert resposta.status_code == 200
    assert "Selecione o comprovante." in html(resposta)


def test_arquivo_grande_demais(portal):
    resposta = postar(portal, b"%PDF-1.4\n" + b"0" * (6 * 1024 * 1024))
    assert resposta.status_code == 200
    assert "O limite é 5 MB." in html(resposta)
    assert not MinisterPaymentProof.objects.exists()


def test_duplo_clique_nao_cria_dois(portal):
    assert postar(portal).status_code == 302
    segundo = postar(portal)
    assert segundo.status_code == 302 and segundo.url == OUTUBRO
    assert MinisterPaymentProof.objects.count() == 1
    assert "Já existe um comprovante aguardando análise" in html(portal.get(OUTUBRO))


def test_get_do_formulario_com_comprovante_em_analise_volta_para_a_cobranca(portal, pendente):
    enviar(pendente)
    resposta = portal.get(ENVIO_OUTUBRO)
    assert resposta.status_code == 302 and resposta.url == OUTUBRO


@pytest.mark.parametrize("metodo", ["put", "patch", "delete"])
def test_envio_so_aceita_get_e_post(portal, metodo):
    assert getattr(portal, metodo)(ENVIO_OUTUBRO).status_code == 405
    assert not MinisterPaymentProof.objects.exists()


def test_envio_exige_csrf(ministro_com_acesso, pendente):
    client = Client(enforce_csrf_checks=True)
    entrar = client.get("/ministros/login/")
    token = entrar.cookies["csrftoken"].value
    client.post("/ministros/login/", {"identificador": "17899", "senha": "Pastor#Seguro2026", "csrfmiddlewaretoken": token})
    assert client.get(OUTUBRO).status_code == 200
    assert postar(client).status_code == 403
    assert not MinisterPaymentProof.objects.exists()


# ---------------------------------------------------------------------------
# Portal: estados
# ---------------------------------------------------------------------------


def test_aguardando_analise_esconde_o_pix(portal, pendente):
    c = enviar(pendente)
    pagina = html(portal.get(OUTUBRO))
    assert "Comprovante enviado" in pagina
    assert "Aguardando conferência" in pagina
    assert "Aguardando análise" in pagina
    assert timezone.localtime(c.submitted_at).strftime("%d/%m/%Y %H:%M") in pagina
    assert "Copiar Pix" not in pagina
    assert "<svg" not in pagina
    assert cobranca.cobranca_vigente(pendente).pix_payload not in pagina
    assert "Baixar guia PDF" not in pagina
    assert 'href="{}"'.format(ENVIO_OUTUBRO) not in pagina
    assert portal.get(GUIA_OUTUBRO).status_code == 404


def test_lista_mostra_aguardando_analise_e_mantem_o_total_em_aberto(portal, pendente):
    antes = portal.get(LISTA_PORTAL).context["em_aberto"]
    enviar(pendente)
    resposta = portal.get(LISTA_PORTAL)
    assert "Aguardando análise" in html(resposta)
    assert resposta.context["em_aberto"] == antes == pendente.amount


def test_recusado_mostra_o_motivo_e_devolve_o_pix(portal, pendente, admin_user):
    comprovantes.recusar_comprovante(enviar(pendente), motivo="Imagem ilegível.", actor=admin_user)
    pagina = html(portal.get(OUTUBRO))
    assert "Comprovante recusado" in pagina
    assert "Motivo: Imagem ilegível." in pagina
    assert "Envie um novo comprovante." in pagina
    assert "Enviar novo comprovante" in pagina
    assert "Copiar Pix" in pagina
    assert portal.get(GUIA_OUTUBRO).status_code == 200
    # Sem nome de quem analisou.
    assert admin_user.full_name not in pagina


def test_reenvio_pelo_portal_preserva_o_historico(portal, pendente, admin_user):
    postar(portal)
    comprovantes.recusar_comprovante(MinisterPaymentProof.objects.get(), motivo="Valor não corresponde.", actor=admin_user)
    assert portal.get(ENVIO_OUTUBRO).status_code == 200
    assert postar(portal, imagem_de_teste("PNG"), "novo.png").status_code == 302
    assert MinisterPaymentProof.objects.count() == 2
    pagina = html(portal.get(OUTUBRO))
    assert "Comprovantes enviados" in pagina
    assert pagina.count("Enviado em") == 2
    assert "Recusado" in pagina and "Aguardando análise" in pagina
    assert "Valor não corresponde." in pagina


def test_aprovado_mostra_pago_sem_upload_nem_pix(portal, pendente, admin_user):
    comprovantes.aprovar_comprovante(enviar(pendente), paid_at=HOJE, actor=admin_user)
    pagina = html(portal.get(OUTUBRO))
    assert "Mensalidade paga" in pagina
    assert "Pagamento confirmado em {}".format(HOJE.strftime("%d/%m/%Y")) in pagina
    assert "forma: Pix" in pagina
    assert "Copiar Pix" not in pagina and "<svg" not in pagina
    assert "Enviar comprovante" not in pagina
    assert "Aprovado" in pagina
    resposta = portal.get(ENVIO_OUTUBRO)
    assert resposta.status_code == 302 and resposta.url == OUTUBRO
    postar(portal)
    assert MinisterPaymentProof.objects.count() == 1


@pytest.mark.parametrize(
    "transicao",
    [
        lambda m, a: financeiro.exempt_fee(m, reason="isento", actor=a),
        lambda m, a: financeiro.cancel_fee(m, reason="cancelada", actor=a),
        lambda m, a: financeiro.register_payment(m, paid_at=HOJE, payment_method="CASH", actor=a),
    ],
)
def test_isenta_cancelada_e_paga_nao_aceitam_upload(portal, pendente, admin_user, transicao):
    transicao(pendente, admin_user)
    pagina = html(portal.get(OUTUBRO))
    assert "Enviar comprovante" not in pagina
    assert portal.get(ENVIO_OUTUBRO).status_code == 302
    resposta = postar(portal)
    assert resposta.status_code == 302 and resposta.url == OUTUBRO
    assert not MinisterPaymentProof.objects.exists()


def test_vencida_aceita_upload(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    nova(ministro_com_acesso, vencimento=ONTEM)
    assert "Enviar comprovante" in html(ministro_client_logado.get(OUTUBRO))
    assert postar(ministro_client_logado).status_code == 302
    assert MinisterPaymentProof.objects.count() == 1


def test_sem_cobranca_nao_oferece_upload(ministro_client_logado, ministro_com_acesso, configuracao_pix):
    nova(ministro_com_acesso, cobrar=False)
    assert "Enviar comprovante" not in html(ministro_client_logado.get(OUTUBRO))
    assert ministro_client_logado.get(ENVIO_OUTUBRO).status_code == 302
    postar(ministro_client_logado)
    assert not MinisterPaymentProof.objects.exists()


# ---------------------------------------------------------------------------
# Portal: isolamento entre ministros
# ---------------------------------------------------------------------------


def test_a_ve_o_proprio_arquivo(portal, pendente):
    conteudo = pdf_de_teste("de A")
    enviar(pendente, conteudo)
    resposta = portal.get(ARQUIVO_1)
    assert resposta.status_code == 200
    assert corpo_do_arquivo(resposta) == conteudo
    assert resposta["Content-Type"] == "application/pdf"
    assert resposta["Content-Disposition"].startswith("inline")
    assert 'filename="comprovante-outubro-2026-1.pdf"' in resposta["Content-Disposition"]


def test_outro_ministro_nao_alcanca_nada_de_a(portal, pendente, outro_ministro):
    c = enviar(pendente)
    b = Client()
    entrar_no_portal(b, "132927", SENHA_DE_B)
    for url in (OUTUBRO, ENVIO_OUTUBRO, ARQUIVO_1, GUIA_OUTUBRO):
        assert b.get(url).status_code == 404, url
    assert postar(b).status_code == 404
    assert MinisterPaymentProof.objects.count() == 1
    # Nem pelo id, nem pelo caminho interno.
    for url in (
        "/ministros/mensalidades/2026/10/comprovantes/{}/".format(c.pk),
        "/media/" + c.file.name,
        "/ministros/" + c.file.name,
    ):
        assert b.get(url).status_code == 404, url


def test_b_ve_o_proprio_e_nao_o_de_a(portal, pendente, outro_ministro, configuracao_pix):
    de_a = pdf_de_teste("de A")
    de_b = pdf_de_teste("de B")
    enviar(pendente, de_a)
    enviar(nova(outro_ministro), de_b)
    b = Client()
    entrar_no_portal(b, "132927", SENHA_DE_B)
    assert corpo_do_arquivo(b.get(ARQUIVO_1)) == de_b
    assert corpo_do_arquivo(portal.get(ARQUIVO_1)) == de_a


@pytest.mark.parametrize("parametro", ["minister", "ministro", "minister_id", "fee", "id", "pk", "comprovante"])
def test_query_string_nao_troca_o_dono(portal, pendente, outro_ministro, configuracao_pix, parametro):
    de_a = pdf_de_teste("de A")
    enviar(pendente, de_a)
    alheia = nova(outro_ministro)
    alheio = enviar(alheia, pdf_de_teste("de B"))
    for valor in (outro_ministro.pk, alheia.pk, alheio.pk):
        resposta = portal.get("{}?{}={}".format(ARQUIVO_1, parametro, valor))
        assert corpo_do_arquivo(resposta) == de_a


@pytest.mark.parametrize("numero", ["0", "2", "99", "999"])
def test_numero_inexistente_e_404(portal, pendente, numero):
    enviar(pendente)
    assert portal.get("/ministros/mensalidades/2026/10/comprovantes/{}/".format(numero)).status_code == 404


def test_competencia_inexistente_e_404(portal):
    for url in (
        "/ministros/mensalidades/2026/11/comprovante/",
        "/ministros/mensalidades/2026/13/comprovante/",
        "/ministros/mensalidades/2026/11/comprovantes/1/",
    ):
        assert portal.get(url).status_code == 404, url


def test_sessao_adulterada_nao_envia(portal, pendente, outro_ministro):
    sessao = portal.session
    sessao["_minister_account_id"] = outro_ministro.account.pk
    sessao.save()
    resposta = postar(portal)
    assert resposta.status_code == 302 and resposta.url.startswith("/ministros/login/")
    assert not MinisterPaymentProof.objects.exists()


def test_bloqueado_perde_o_acesso(portal, pendente, ministro_com_acesso, admin_user):
    enviar(pendente)
    services.block_account(ministro_com_acesso, actor=admin_user)
    for url in (OUTUBRO, ENVIO_OUTUBRO, ARQUIVO_1):
        resposta = portal.get(url)
        assert resposta.status_code == 302 and resposta.url.startswith("/ministros/login/"), url
    assert postar(portal).status_code == 302
    assert MinisterPaymentProof.objects.count() == 1


def test_logout_e_voltar_nao_reexibe_o_comprovante(portal, pendente):
    enviar(pendente)
    portal.post(URL_LOGOUT)
    for url in (OUTUBRO, ENVIO_OUTUBRO, ARQUIVO_1):
        resposta = portal.get(url)
        assert resposta.status_code == 302 and resposta.url.startswith("/ministros/login/"), url


@pytest.mark.parametrize("url", [ENVIO_OUTUBRO, ARQUIVO_1, OUTUBRO])
def test_portal_sem_cache(portal, pendente, url):
    if url != ENVIO_OUTUBRO:
        enviar(pendente)
    resposta = portal.get(url)
    assert resposta.status_code == 200
    controle = resposta["Cache-Control"]
    for diretiva in ("no-store", "no-cache", "must-revalidate", "private", "max-age=0"):
        assert diretiva in controle, diretiva


def test_arquivo_do_portal_com_nosniff(portal, pendente):
    enviar(pendente, imagem_de_teste("PNG"), "print.png")
    resposta = portal.get(ARQUIVO_1)
    assert resposta["X-Content-Type-Options"] == "nosniff"
    assert resposta["Content-Type"] == "image/png"


def test_aluno_e_anonimo_nao_enviam(client, student_client_logado, pendente):
    for c in (client, student_client_logado):
        resposta = postar(c)
        assert resposta.status_code == 302 and resposta.url.startswith("/ministros/login/")
    assert not MinisterPaymentProof.objects.exists()


def test_media_nao_e_servido(portal, pendente):
    c = enviar(pendente)
    assert Client().get("/media/" + c.file.name).status_code == 404
    assert portal.get("/media/" + c.file.name).status_code == 404
    assert Client().get("/media/ministers/payment_proofs/").status_code == 404


# ---------------------------------------------------------------------------
# Admin: fila
# ---------------------------------------------------------------------------


def _ministro(numero):
    m = services.create_minister(
        dados=dados_de_ministro(
            full_name="Ministro Fila {}".format(numero),
            conamad_registration=str(8800 + numero),
            field_registration=str(700 + numero),
        )
    )
    return m


def _fila(configuracao_pix, quantos=3):
    enviados = []
    for i in range(quantos):
        c = enviar(nova(_ministro(i)))
        MinisterPaymentProof.objects.filter(pk=c.pk).update(
            submitted_at=timezone.now() - datetime.timedelta(hours=10 - i)
        )
        enviados.append(c)
    return enviados


def test_fila_abre_nos_aguardando_do_mais_antigo(admin_client_logado, configuracao_pix, admin_user):
    a, b, c = _fila(configuracao_pix)
    comprovantes.recusar_comprovante(b, motivo="ilegível", actor=admin_user)
    resposta = admin_client_logado.get(FILA)
    assert resposta.status_code == 200
    assert [x.pk for x in resposta.context["comprovantes"]] == [a.pk, c.pk]
    assert resposta.context["aguardando"] == 2
    pagina = html(resposta)
    for coluna in ("Ministro", "Matrícula", "Competência", "Valor", "Vencimento", "Enviado em", "Situação", "Ações"):
        assert ">{}<".format(coluna) in pagina
    assert "Analisar" in pagina


def test_todas_poe_aguardando_primeiro(admin_client_logado, configuracao_pix, admin_user):
    a, b, c = _fila(configuracao_pix)
    comprovantes.recusar_comprovante(a, motivo="ilegível", actor=admin_user)
    comprovantes.aprovar_comprovante(c, paid_at=HOJE, actor=admin_user)
    ordem = [x.pk for x in admin_client_logado.get(FILA + "?situacao=todas").context["comprovantes"]]
    assert ordem == [b.pk, c.pk, a.pk]


@pytest.mark.parametrize("situacao", ["APPROVED", "REJECTED"])
def test_filtro_de_situacao(admin_client_logado, configuracao_pix, admin_user, situacao):
    a, b, _ = _fila(configuracao_pix)
    comprovantes.aprovar_comprovante(a, paid_at=HOJE, actor=admin_user)
    comprovantes.recusar_comprovante(b, motivo="ilegível", actor=admin_user)
    esperado = a if situacao == "APPROVED" else b
    lista = admin_client_logado.get(FILA + "?situacao=" + situacao).context["comprovantes"]
    assert [x.pk for x in lista] == [esperado.pk]


def test_situacao_invalida_cai_na_fila(admin_client_logado, configuracao_pix):
    _fila(configuracao_pix, 1)
    assert admin_client_logado.get(FILA + "?situacao=<script>").context["filtros"]["situacao"] == "SUBMITTED"


@pytest.mark.parametrize("busca", ["Fila 1", "8801", "701"])
def test_busca_por_nome_conamad_e_matricula(admin_client_logado, configuracao_pix, busca):
    _, b, _ = _fila(configuracao_pix)
    assert [x.pk for x in admin_client_logado.get(FILA, {"q": busca}).context["comprovantes"]] == [b.pk]


def test_filtro_de_competencia_e_data(admin_client_logado, configuracao_pix):
    m = _ministro(50)
    setembro = enviar(nova(m, competencia="2026-09"))
    outubro = enviar(nova(m, competencia="2026-10"))
    MinisterPaymentProof.objects.filter(pk=setembro.pk).update(submitted_at=timezone.now() - datetime.timedelta(days=20))
    assert [x.pk for x in admin_client_logado.get(FILA, {"competencia": "2026-09"}).context["comprovantes"]] == [setembro.pk]
    desde = (HOJE - datetime.timedelta(days=2)).isoformat()
    assert [x.pk for x in admin_client_logado.get(FILA, {"enviado_de": desde}).context["comprovantes"]] == [outubro.pk]
    ate = (HOJE - datetime.timedelta(days=10)).isoformat()
    assert [x.pk for x in admin_client_logado.get(FILA, {"enviado_ate": ate}).context["comprovantes"]] == [setembro.pk]
    # Data invalida e ignorada, nao quebra a tela.
    assert admin_client_logado.get(FILA, {"enviado_de": "ontem"}).status_code == 200


def test_fila_paginada_com_consultas_constantes(admin_client_logado, configuracao_pix):
    _fila(configuracao_pix, 3)
    with CaptureQueriesContext(connection) as poucos:
        admin_client_logado.get(FILA)
    _fila_extra = [enviar(nova(_ministro(100 + i))) for i in range(27)]
    with CaptureQueriesContext(connection) as muitos:
        resposta = admin_client_logado.get(FILA)
    assert len(muitos) == len(poucos)
    assert len(resposta.context["comprovantes"]) == 25
    assert resposta.context["page_obj"].paginator.count == 30
    assert admin_client_logado.get(FILA + "?page=2").context["page_obj"].object_list.__len__() == 5


def test_nome_e_motivo_sao_escapados(admin_client_logado, ministro_com_acesso, configuracao_pix, admin_user):
    services.update_minister(
        ministro_com_acesso, dados=dados_de_ministro(full_name="<script>alert(1)</script> Silva"), actor=admin_user
    )
    m = nova(ministro_com_acesso)
    c = enviar(m, nome="<img src=x onerror=alert(1)>.pdf")
    pagina = html(admin_client_logado.get(FILA)) + html(admin_client_logado.get(url_admin(c)))
    assert "<script>alert(1)</script>" not in pagina and "<img src=x" not in pagina
    assert "&lt;script&gt;" in pagina and "&lt;img src=x" in pagina
    comprovantes.recusar_comprovante(c, motivo="<b>ilegível</b><script>x()</script>", actor=admin_user)
    # Cliente proprio: admin_client_logado e o portal usariam o mesmo `client`.
    ministro = Client()
    entrar_no_portal(ministro)
    portal = html(ministro.get(OUTUBRO))
    assert "<script>x()</script>" not in portal and "<b>ilegível</b>" not in portal
    assert "&lt;b&gt;ilegível&lt;/b&gt;" in portal


# ---------------------------------------------------------------------------
# Admin: detalhe, arquivo, aprovar e recusar
# ---------------------------------------------------------------------------


def test_detalhe_mostra_ministro_mensalidade_e_historico(admin_client_logado, pendente, admin_user):
    primeiro = enviar(pendente)
    comprovantes.recusar_comprovante(primeiro, motivo="Imagem ilegível.", actor=admin_user)
    segundo = enviar(pendente, imagem_de_teste("PNG"), "print.png")
    pagina = html(admin_client_logado.get(url_admin(segundo)))
    for texto in (
        pendente.minister.full_name,
        "17899",
        "7764",
        "Sede",
        "Pastor Presidente",
        "10/2026",
        "R$ 35,00",
        pendente.due_date.strftime("%d/%m/%Y"),
        "Aprovar pagamento?",
        "Confirmar aprovação",
        "Recusar comprovante",
        "Imagem ilegível.",
        "Comprovantes desta mensalidade",
    ):
        assert texto in pagina, texto
    # Imagem: preview pela view protegida, nunca /media/.
    assert 'src="{}"'.format(url_admin(segundo, "arquivo/")) in pagina
    assert "/media/" not in pagina
    assert url_admin(segundo, "baixar/") in pagina


def test_detalhe_de_pdf_abre_em_outra_aba(admin_client_logado, pendente):
    c = enviar(pendente)
    pagina = html(admin_client_logado.get(url_admin(c)))
    assert "Abrir comprovante" in pagina and 'target="_blank"' in pagina
    assert "<img class=\"cpo-comprovante__preview" not in pagina


def test_sugestao_da_data_e_o_dia_do_envio(admin_client_logado, pendente):
    c = enviar(pendente)
    form = admin_client_logado.get(url_admin(c)).context["form_aprovar"]
    assert form.initial["paid_at"] == timezone.localdate(c.submitted_at)


@pytest.mark.parametrize("sufixo, disposicao", [("arquivo/", "inline"), ("baixar/", "attachment")])
def test_arquivo_para_o_admin(admin_client_logado, pendente, sufixo, disposicao):
    conteudo = imagem_de_teste("JPEG")
    c = enviar(pendente, conteudo, "foto.jpg")
    resposta = admin_client_logado.get(url_admin(c, sufixo))
    assert resposta.status_code == 200
    assert corpo_do_arquivo(resposta) == conteudo
    assert resposta["Content-Type"] == "image/jpeg"
    assert resposta["Content-Disposition"].startswith(disposicao)
    assert 'filename="comprovante-outubro-2026-{}.jpg"'.format(c.pk) in resposta["Content-Disposition"]
    assert resposta["X-Content-Type-Options"] == "nosniff"
    for diretiva in ("no-store", "no-cache", "must-revalidate", "private"):
        assert diretiva in resposta["Cache-Control"], diretiva


def test_arquivo_sumido_do_disco_e_404(admin_client_logado, pendente):
    import os

    c = enviar(pendente)
    os.remove(c.file.path)
    assert admin_client_logado.get(url_admin(c, "arquivo/")).status_code == 404


def test_aprovar_pela_tela(admin_client_logado, pendente, admin_user):
    c = enviar(pendente)
    resposta = admin_client_logado.post(url_admin(c, "aprovar/"), {"paid_at": HOJE.isoformat()})
    assert resposta.status_code == 302 and resposta.url == url_admin(c)
    c.refresh_from_db()
    pendente.refresh_from_db()
    assert c.status == ProofStatus.APPROVED and c.reviewed_by == admin_user
    assert pendente.status == MonthlyFeeStatus.PAID and pendente.payment_method == "PIX"
    pagina = html(admin_client_logado.get(url_admin(c)))
    assert "Comprovante aprovado." in pagina
    assert "Confirmar aprovação" not in pagina


def test_aprovar_sem_data_mostra_o_erro(admin_client_logado, pendente):
    c = enviar(pendente)
    resposta = admin_client_logado.post(url_admin(c, "aprovar/"), {"paid_at": ""})
    assert resposta.status_code == 200
    assert "obrigatório" in html(resposta)
    c.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED


def test_aprovar_com_data_futura_mostra_o_erro(admin_client_logado, pendente):
    c = enviar(pendente)
    resposta = admin_client_logado.post(url_admin(c, "aprovar/"), {"paid_at": (HOJE + datetime.timedelta(days=3)).isoformat()})
    assert resposta.status_code == 200
    assert "não pode ser posterior ao envio" in html(resposta)
    pendente.refresh_from_db()
    assert pendente.status == MonthlyFeeStatus.PENDING


def test_recusar_exige_motivo(admin_client_logado, pendente):
    c = enviar(pendente)
    for motivo in ("", "    "):
        resposta = admin_client_logado.post(url_admin(c, "recusar/"), {"motivo": motivo})
        assert resposta.status_code == 200
        c.refresh_from_db()
        assert c.status == ProofStatus.SUBMITTED
    resposta = admin_client_logado.post(url_admin(c, "recusar/"), {"motivo": "x" * 1001})
    assert resposta.status_code == 200
    c.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED


def test_recusar_pela_tela(admin_client_logado, pendente):
    c = enviar(pendente)
    resposta = admin_client_logado.post(url_admin(c, "recusar/"), {"motivo": "Comprovante de outra competência."})
    assert resposta.status_code == 302
    c.refresh_from_db()
    pendente.refresh_from_db()
    assert c.status == ProofStatus.REJECTED and c.rejection_reason == "Comprovante de outra competência."
    assert pendente.status == MonthlyFeeStatus.PENDING


def test_segunda_decisao_recebe_409_e_nao_sobrescreve(admin_client_logado, pendente):
    c = enviar(pendente)
    admin_client_logado.post(url_admin(c, "aprovar/"), {"paid_at": HOJE.isoformat()})
    resposta = admin_client_logado.post(url_admin(c, "recusar/"), {"motivo": "tarde"})
    assert resposta.status_code == 409
    assert "já foi analisado" in html(resposta)
    resposta = admin_client_logado.post(url_admin(c, "aprovar/"), {"paid_at": HOJE.isoformat()})
    assert resposta.status_code == 409
    c.refresh_from_db()
    assert c.status == ProofStatus.APPROVED and c.rejection_reason == ""
    assert AuditLog.objects.filter(event=AuditEvent.MINISTER_FEE_PAYMENT_REGISTERED).count() == 1


@pytest.mark.parametrize("sufixo", ["aprovar/", "recusar/"])
def test_decisao_por_get_e_405(admin_client_logado, pendente, sufixo):
    c = enviar(pendente)
    assert admin_client_logado.get(url_admin(c, sufixo)).status_code == 405
    c.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED


@pytest.mark.parametrize("sufixo, dados", [("aprovar/", {"paid_at": HOJE.isoformat()}), ("recusar/", {"motivo": "x"})])
def test_decisao_exige_csrf(admin_user, pendente, sufixo, dados):
    c = enviar(pendente)
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_user)
    assert client.post(url_admin(c, sufixo), dados).status_code == 403
    c.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED


def test_aluno_recebe_403_em_tudo(student_client_logado, pendente):
    c = enviar(pendente)
    for sufixo in ("", "arquivo/", "baixar/"):
        assert student_client_logado.get(url_admin(c, sufixo)).status_code == 403
    assert student_client_logado.get(FILA).status_code == 403
    assert student_client_logado.post(url_admin(c, "aprovar/"), {"paid_at": HOJE.isoformat()}).status_code == 403
    assert student_client_logado.post(url_admin(c, "recusar/"), {"motivo": "x"}).status_code == 403
    c.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED


def test_ministro_nao_acessa_o_painel(portal, pendente):
    c = enviar(pendente)
    for url in (FILA, url_admin(c), url_admin(c, "arquivo/"), url_admin(c, "baixar/")):
        resposta = portal.get(url)
        assert resposta.status_code == 302 and resposta.url.startswith("/login/"), url
    resposta = portal.post(url_admin(c, "aprovar/"), {"paid_at": HOJE.isoformat()})
    assert resposta.status_code == 302 and resposta.url.startswith("/login/")
    c.refresh_from_db()
    assert c.status == ProofStatus.SUBMITTED


def test_anonimo_vai_para_o_login(client, pendente):
    c = enviar(pendente)
    for url in (FILA, url_admin(c), url_admin(c, "arquivo/")):
        resposta = client.get(url)
        assert resposta.status_code == 302 and resposta.url.startswith("/login/"), url


def test_logout_do_admin_e_voltar(admin_client_logado, pendente):
    c = enviar(pendente)
    for url in (FILA, url_admin(c)):
        controle = admin_client_logado.get(url)["Cache-Control"]
        assert "no-store" in controle and "private" in controle, url
    admin_client_logado.post("/logout/")
    for url in (FILA, url_admin(c), url_admin(c, "arquivo/")):
        resposta = admin_client_logado.get(url)
        assert resposta.status_code == 302 and resposta.url.startswith("/login/"), url


def test_id_inexistente_e_404(admin_client_logado):
    for sufixo in ("", "arquivo/", "baixar/"):
        assert admin_client_logado.get("/admin-panel/ministros/comprovantes/999999/" + sufixo).status_code == 404


# ---------------------------------------------------------------------------
# Admin: dashboards, menu e ficha
# ---------------------------------------------------------------------------


def test_dashboards_mostram_o_numero_real(admin_client_logado, configuracao_pix, admin_user):
    a, b, c = _fila(configuracao_pix)
    comprovantes.recusar_comprovante(a, motivo="x", actor=admin_user)
    resposta = admin_client_logado.get("/admin-panel/ministros/")
    assert resposta.context["comprovantes_em_analise"] == 2
    assert "Comprovantes aguardando análise" in html(resposta)
    resposta = admin_client_logado.get("/admin-panel/ministros/mensalidades/")
    assert resposta.context["comprovantes_em_analise"] == 2
    assert "2 comprovantes aguardando análise" in html(resposta)


def test_menu_tem_comprovantes(admin_client_logado):
    pagina = html(admin_client_logado.get(FILA))
    assert 'href="/admin-panel/ministros/comprovantes/"' in pagina
    assert ">Comprovantes<" in pagina


def test_ficha_com_comprovante_em_analise_manda_analisar(admin_client_logado, pendente):
    c = enviar(pendente)
    ficha = "/admin-panel/ministros/mensalidades/{}/".format(pendente.pk)
    pagina = html(admin_client_logado.get(ficha))
    assert "Analisar comprovante" in pagina and url_admin(c) in pagina
    assert "Registrar pagamento" not in pagina
    assert "Comprovante enviado pelo ministro" in pagina
    # E a baixa manual pela tela continua protegida no servico.
    resposta = admin_client_logado.post(
        ficha + "pagamento/", {"paid_at": HOJE.isoformat(), "payment_method": "CASH"}
    )
    assert resposta.status_code == 200
    assert "Aprove ou recuse o comprovante antes" in html(resposta)
    pendente.refresh_from_db()
    assert pendente.status == MonthlyFeeStatus.PENDING
