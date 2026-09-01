"""
Compartilhamento do certificado.

O que este arquivo protege, em uma frase: o aluno compartilha o ENDERECO
PUBLICO do proprio certificado, o servidor monta tudo, e a trilha registra que
alguem apertou o botao — nada mais forte do que isso.

As tres fronteiras que os testes cercam:

    dono        certificado de outra pessoa e 404, nunca 403
    escrita     POST com CSRF; GET nao grava nada
    conteudo    mensagem e destino sao do servidor, e nao do navegador
"""

from urllib.parse import parse_qs, unquote, urlparse

import pytest
from django.test import Client
from django.urls import reverse

from audit.models import AuditEvent, AuditLog
from certificates.services import revoke_certificate

pytestmark = pytest.mark.django_db


def url_whatsapp(certificado):
    return reverse(
        "student:certificate_share_whatsapp",
        kwargs={"verification_code": certificado.verification_code},
    )


def url_nativo(certificado):
    return reverse(
        "student:certificate_share_native",
        kwargs={"verification_code": certificado.verification_code},
    )


def texto_da_mensagem(destino):
    """Decodifica o parametro text= do deep link do WhatsApp."""
    consulta = parse_qs(urlparse(destino).query)
    return unquote(consulta["text"][0])


def eventos_de_compartilhamento():
    return AuditLog.objects.filter(event=AuditEvent.CERTIFICATE_SHARE_INITIATED)


# ---------------------------------------------------------------------------
# WhatsApp: caminho feliz
# ---------------------------------------------------------------------------


def test_o_dono_compartilha_e_vai_para_o_whatsapp(
    student_client_logado, certificado
):
    resposta = student_client_logado.post(url_whatsapp(certificado))

    assert resposta.status_code == 302
    assert resposta["Location"].startswith("https://wa.me/?text=")


def test_o_compartilhamento_e_registrado_uma_unica_vez(
    student_client_logado, certificado, student_user
):
    student_client_logado.post(url_whatsapp(certificado))

    registros = eventos_de_compartilhamento()
    assert registros.count() == 1

    registro = registros.get()
    assert registro.actor_id == student_user.pk
    assert registro.entity_type == "Certificate"
    # entity_id e CharField: a trilha guarda ids de tipos diferentes.
    assert registro.entity_id == str(certificado.pk)
    assert registro.metadata == {"channel": "whatsapp"}


def test_dois_cliques_produzem_dois_registros(student_client_logado, certificado):
    """
    Nao ha deduplicacao, e e proposital.

    O evento conta tentativas de compartilhar, e compartilhar duas vezes com
    pessoas diferentes e comportamento normal. Agrupar os dois num so
    esconderia a segunda.
    """
    student_client_logado.post(url_whatsapp(certificado))
    student_client_logado.post(url_whatsapp(certificado))

    assert eventos_de_compartilhamento().count() == 2


def test_o_evento_significa_iniciado_e_nao_entregue(
    student_client_logado, certificado
):
    """
    A metadata nao afirma entrega, porque o sistema nao tem como saber.

    Depois do redirect quem conduz e o WhatsApp. O aluno pode fechar o
    aplicativo sem enviar para ninguem, e nada disso volta para ca. Qualquer
    chave sugerindo entrega seria uma afirmacao que este sistema nao pode
    sustentar.
    """
    student_client_logado.post(url_whatsapp(certificado))

    metadata = eventos_de_compartilhamento().get().metadata
    assert set(metadata) == {"channel"}
    for proibida in ("delivered", "sent", "read", "entregue", "enviada"):
        assert proibida not in metadata


# ---------------------------------------------------------------------------
# WhatsApp: a mensagem
# ---------------------------------------------------------------------------


def test_a_mensagem_leva_instituicao_modulo_e_url_publica(
    student_client_logado, certificado, settings
):
    settings.SITE_URL = "https://cpoadsum.nexeeo.com"

    resposta = student_client_logado.post(url_whatsapp(certificado))
    mensagem = texto_da_mensagem(resposta["Location"])

    assert certificado.institution_name_snapshot in mensagem
    assert certificado.modulo_impresso in mensagem
    assert (
        "https://cpoadsum.nexeeo.com/certificados/validar/{}/".format(
            certificado.verification_code
        )
        in mensagem
    )


def test_a_mensagem_nao_carrega_nada_privado(
    student_client_logado, certificado, student_user
):
    """
    O que sai de dentro do sistema numa conversa de WhatsApp.

    A mensagem vai para um grupo, um contato, um print. Nada nela pode ir
    alem do que o proprio certificado impresso ja mostra.
    """
    resposta = student_client_logado.post(url_whatsapp(certificado))
    mensagem = texto_da_mensagem(resposta["Location"])
    tentativa = certificado.attempt

    assert student_user.email not in mensagem
    assert str(tentativa.final_score) not in mensagem
    assert str(tentativa.public_id) not in mensagem
    # A pk da tentativa nao entra na lista: com um digito so, ela aparece por
    # acaso dentro de qualquer UUID e a assercao viraria ruido. O que vale
    # verificar sao as marcas que so existiriam se alguem tivesse acrescentado
    # o dado de proposito.
    for proibido in ("attempt", "resposta", "nota", "score", "tentativa"):
        assert proibido not in mensagem.lower()


def test_a_mensagem_leva_a_validacao_e_nunca_ao_pdf(
    student_client_logado, certificado
):
    """
    O §38.

    O arquivo, uma vez enviado, nao volta atras: se o certificado for revogado
    amanha, quem recebeu o PDF continua com um documento de aparencia valida.
    O endereco publico continua respondendo a verdade.
    """
    resposta = student_client_logado.post(url_whatsapp(certificado))
    mensagem = texto_da_mensagem(resposta["Location"])

    assert "/validar/" in mensagem
    assert "/baixar/" not in mensagem
    assert ".pdf" not in mensagem


def test_o_navegador_nao_escolhe_a_mensagem(student_client_logado, certificado):
    """
    Se o texto viesse do POST, este endereco — que exige login e por isso
    parece confiavel — viraria um gerador de mensagens de terceiros.
    """
    resposta = student_client_logado.post(
        url_whatsapp(certificado),
        {
            "text": "Clique aqui para receber seu premio",
            "mensagem": "texto forjado",
            "message": "texto forjado",
        },
    )
    mensagem = texto_da_mensagem(resposta["Location"])

    assert "premio" not in mensagem
    assert "forjado" not in mensagem
    assert certificado.institution_name_snapshot in mensagem


def test_o_navegador_nao_escolhe_o_destino(student_client_logado, certificado):
    """Open redirect: nenhum parametro do POST desvia o Location."""
    resposta = student_client_logado.post(
        url_whatsapp(certificado),
        {
            "url": "https://exemplo-malicioso.invalid/",
            "next": "https://exemplo-malicioso.invalid/",
            "redirect": "https://exemplo-malicioso.invalid/",
            "proximo": "/admin-panel/",
        },
    )

    assert resposta["Location"].startswith("https://wa.me/?text=")
    assert "exemplo-malicioso" not in resposta["Location"]


def test_o_navegador_nao_escolhe_o_certificado(
    student_client_logado, certificado, outro_student, admin_user
):
    """
    O documento vem do caminho da URL cruzado com o dono da sessao.

    Um verification_code no corpo do POST nao e consultado em lugar nenhum.
    """
    import uuid

    resposta = student_client_logado.post(
        url_whatsapp(certificado),
        {
            "verification_code": str(uuid.uuid4()),
            "certificate_id": 999999,
        },
    )
    mensagem = texto_da_mensagem(resposta["Location"])

    assert str(certificado.verification_code) in mensagem


# ---------------------------------------------------------------------------
# WhatsApp: quem nao pode
# ---------------------------------------------------------------------------


def test_certificado_de_outro_aluno_responde_404(certificado, outro_student):
    """
    404, e nao 403. Um 403 confirmaria que aquele codigo existe.
    """
    cliente = Client()
    cliente.force_login(outro_student)

    resposta = cliente.post(url_whatsapp(certificado))

    assert resposta.status_code == 404
    assert eventos_de_compartilhamento().count() == 0


def test_o_anonimo_vai_para_o_login(client, certificado):
    resposta = client.post(url_whatsapp(certificado))

    assert resposta.status_code == 302
    assert "/login/" in resposta["Location"]
    assert eventos_de_compartilhamento().count() == 0


def test_o_admin_nao_compartilha_certificado_de_aluno(
    admin_client_logado, certificado
):
    resposta = admin_client_logado.post(url_whatsapp(certificado))

    assert resposta.status_code == 403
    assert eventos_de_compartilhamento().count() == 0


def test_get_nao_compartilha_nem_registra(student_client_logado, certificado):
    """
    Escrita nao acontece por GET.

    Um GET que grava enche a trilha toda vez que alguem passa o mouse sobre o
    link numa conversa, ou que um antivirus corporativo resolve buscar a URL.
    """
    resposta = student_client_logado.get(url_whatsapp(certificado))

    assert resposta.status_code == 405
    assert eventos_de_compartilhamento().count() == 0


def test_post_sem_csrf_e_recusado(certificado, student_user):
    cliente = Client(enforce_csrf_checks=True)
    cliente.force_login(student_user)

    resposta = cliente.post(url_whatsapp(certificado))

    assert resposta.status_code == 403
    assert eventos_de_compartilhamento().count() == 0


def test_certificado_revogado_nao_compartilha(
    student_client_logado, certificado, admin_user
):
    """
    Divulgar o link de um documento sem validade seria ajudar a apresentar
    como valido algo que nao e.
    """
    revoke_certificate(certificado, actor=admin_user, motivo="Erro administrativo.")

    resposta = student_client_logado.post(url_whatsapp(certificado))

    assert resposta.status_code == 409
    assert eventos_de_compartilhamento().count() == 0


# ---------------------------------------------------------------------------
# Compartilhamento nativo
# ---------------------------------------------------------------------------


def test_o_canal_nativo_registra_e_responde_204(
    student_client_logado, certificado
):
    resposta = student_client_logado.post(url_nativo(certificado))

    assert resposta.status_code == 204
    assert resposta.content == b""
    assert eventos_de_compartilhamento().get().metadata == {"channel": "native"}


def test_o_canal_nativo_tambem_e_so_do_dono(certificado, outro_student):
    cliente = Client()
    cliente.force_login(outro_student)

    assert cliente.post(url_nativo(certificado)).status_code == 404
    assert eventos_de_compartilhamento().count() == 0


def test_o_canal_nativo_recusa_revogado(
    student_client_logado, certificado, admin_user
):
    revoke_certificate(certificado, actor=admin_user, motivo="Erro.")

    assert student_client_logado.post(url_nativo(certificado)).status_code == 409
    assert eventos_de_compartilhamento().count() == 0


def test_o_canal_nativo_recusa_get(student_client_logado, certificado):
    assert student_client_logado.get(url_nativo(certificado)).status_code == 405
    assert eventos_de_compartilhamento().count() == 0


def test_o_canal_nao_vem_do_navegador(student_client_logado, certificado):
    """
    Cada canal e uma rota, e nao um parametro.

    O valor vai para a trilha de auditoria: com rota fixa, o conjunto de
    canais possiveis e o conjunto de rotas que existem.
    """
    student_client_logado.post(url_nativo(certificado), {"channel": "telegram"})

    assert eventos_de_compartilhamento().get().metadata == {"channel": "native"}


def test_canal_desconhecido_e_recusado_no_servico(certificado, student_user):
    from certificates.services import registrar_compartilhamento
    from common.exceptions import DomainError

    with pytest.raises(DomainError):
        registrar_compartilhamento(
            certificado, canal="telegram", actor=student_user
        )
    assert eventos_de_compartilhamento().count() == 0


# ---------------------------------------------------------------------------
# A tela
# ---------------------------------------------------------------------------


def test_a_lista_mostra_os_botoes_de_compartilhar(
    student_client_logado, certificado
):
    corpo = student_client_logado.get(
        reverse("student:certificate_list")
    ).content.decode("utf-8")

    assert "Compartilhar no WhatsApp" in corpo
    assert url_whatsapp(certificado) in corpo
    assert url_nativo(certificado) in corpo
    assert "csrfmiddlewaretoken" in corpo


def test_o_botao_de_whatsapp_tem_texto_visivel(student_client_logado, certificado):
    """
    §57: texto, e nao apenas icone.

    Um botao so com o simbolo do WhatsApp e mudo para leitor de tela e
    ambiguo para quem nunca viu aquele desenho.
    """
    corpo = student_client_logado.get(
        reverse("student:certificate_list")
    ).content.decode("utf-8")

    compacto = " ".join(corpo.split())

    assert "> Compartilhar no WhatsApp </button>" in compacto


def test_certificado_revogado_nao_mostra_botao_de_compartilhar(
    student_client_logado, certificado, admin_user
):
    revoke_certificate(certificado, actor=admin_user, motivo="Erro.")

    corpo = student_client_logado.get(
        reverse("student:certificate_list")
    ).content.decode("utf-8")

    assert "Compartilhar no WhatsApp" not in corpo
    assert url_whatsapp(certificado) not in corpo


def test_o_cartao_mostra_o_codigo_resumido_e_nao_o_inteiro(
    student_client_logado, certificado
):
    """
    §48. O UUID inteiro e o elemento mais longo do cartao no celular e nao
    serve para quem so quer baixar o PDF. Ele continua acessivel pelo botao de
    copiar e na pagina de validacao.
    """
    corpo = student_client_logado.get(
        reverse("student:certificate_list")
    ).content.decode("utf-8")

    assert certificado.codigo_resumido in corpo
    # O codigo completo existe na pagina, mas so dentro do atributo do botao
    # de copiar — nunca como texto visivel do cartao.
    assert 'data-codigo="{}"'.format(certificado.verification_code) in corpo
    assert "<code class=\"cpo-quebra\">{}</code>".format(
        certificado.verification_code
    ) not in corpo


def test_a_mensagem_do_compartilhamento_nativo_ja_vem_pronta_do_servidor(
    student_client_logado, certificado
):
    """
    navigator.share() so e aceito dentro do gesto do usuario.

    Se o JavaScript fosse buscar o texto no servidor primeiro, o Safari do
    iOS ja teria considerado o gesto encerrado e recusaria a folha de
    compartilhamento. Por isso o texto — montado pelo servidor — chega no
    HTML, e o registro na trilha vai depois.
    """
    corpo = student_client_logado.get(
        reverse("student:certificate_list")
    ).content.decode("utf-8")

    assert 'data-texto="' in corpo
    # Trecho que so existe dentro da mensagem montada pelo servico. O nome da
    # instituicao sozinho nao serviria: ele ja aparece no topo da pagina por
    # APP_NAME, e o teste passaria mesmo com o atributo vazio.
    assert "Valide meu certificado:" in corpo
