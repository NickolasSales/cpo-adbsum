"""
O cenario de demonstracao dos videos de treinamento.

O que estes testes protegem
---------------------------
O comando `preparar_demo_video` roda em PRODUCAO, num banco com turma real
matriculada. O risco nao e ele falhar — e ele funcionar e, de passagem,
encostar em dado de alguem.

Por isso a maior parte dos testes aqui nao verifica o que o comando cria:
verifica o que ele NAO toca. O teste de isolamento fotografa todo o banco
antes e depois e exige que a unica diferenca sejam as linhas DEMO.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from accounts.models import User, UserRole
from certificates.models import Certificate
from common import demo_video as cenario
from courses.models import Enrollment, EnrollmentStatus, Module
from exams.models import (
    Answer,
    Exam,
    ExamAttempt,
    ExamStatus,
    Question,
    QuestionOption,
    QuestionType,
)


def preparar(**opcoes):
    """Roda o comando engolindo a saida, que e longa e nao interessa aqui."""
    saida = StringIO()
    call_command("preparar_demo_video", stdout=saida, **opcoes)
    return saida.getvalue()


def limpar(**opcoes):
    saida = StringIO()
    call_command("limpar_demo_video", stdout=saida, **opcoes)
    return saida.getvalue()


def criar_tentativa(aluno, prova):
    """
    Comeca uma tentativa pelo servico real.

    Montar ExamAttempt na mao exigiria acertar attempt_number, started_at,
    expires_at, total_points_snapshot e passing_score_snapshot — todos sem
    default, porque o desenho quer que o prazo e a escala da nota sejam
    decididos uma unica vez, no start. Reproduzir isso no teste seria manter
    uma segunda copia da regra, que envelheceria sozinha.
    """
    from exams.services.attempt import start_attempt

    return start_attempt(aluno, prova)


def fotografar():
    """
    O estado do banco inteiro, como conjuntos de chaves.

    Guarda chave e nao objeto de proposito: o teste compara identidade de
    linha, e nao conteudo. Assim ele pega tanto uma linha criada quanto uma
    apagada, que e o que "nao alterar dado real" precisa significar.
    """
    return {
        "usuarios": set(User.objects.values_list("pk", flat=True)),
        "modulos": set(Module.objects.values_list("pk", flat=True)),
        "provas": set(Exam.objects.values_list("pk", flat=True)),
        "questoes": set(Question.objects.values_list("pk", flat=True)),
        "matriculas": set(Enrollment.objects.values_list("pk", flat=True)),
        "tentativas": set(ExamAttempt.objects.values_list("pk", flat=True)),
        "certificados": set(Certificate.objects.values_list("pk", flat=True)),
    }


# --- o cenario que o comando monta ----------------------------------------


def test_o_comando_cria_os_dois_alunos_de_demonstracao(db):
    preparar()

    alunos = User.objects.filter(email__in=cenario.EMAILS)
    assert alunos.count() == 2
    for aluno in alunos:
        assert aluno.role == UserRole.STUDENT
        assert aluno.is_active is True
        assert aluno.must_change_password is False
        assert aluno.is_staff is False
        assert aluno.is_superuser is False


def test_os_emails_usam_o_dominio_reservado(db):
    preparar()

    for email in cenario.EMAILS:
        assert email.endswith("@example.com"), (
            "o dominio .example e reservado pela RFC 2606 e nao entrega "
            "e-mail; qualquer outro dominio poderia ser de alguem"
        )


def test_o_comando_cria_o_modulo_de_demonstracao(db):
    preparar()

    modulo = Module.objects.get(code=cenario.CODIGO_DO_MODULO)
    assert modulo.is_active is True
    assert modulo.name == cenario.NOME_DO_MODULO


def test_somente_os_alunos_demo_estao_matriculados_no_modulo_demo(
    db, student_user, outro_student
):
    preparar()

    modulo = Module.objects.get(code=cenario.CODIGO_DO_MODULO)
    emails = set(
        Enrollment.objects.filter(module=modulo).values_list(
            "student__email", flat=True
        )
    )
    assert emails == set(cenario.EMAILS), (
        "se um aluno real for matriculado aqui, o modulo DEMO aparece na "
        "area dele"
    )


def test_a_prova_demo_sai_publicada_e_configurada(db):
    preparar()

    prova = Exam.objects.get(title=cenario.TITULO_DA_PROVA)
    assert prova.status == ExamStatus.PUBLISHED
    assert prova.duration_minutes == cenario.DURACAO_EM_MINUTOS
    assert prova.passing_score == cenario.NOTA_MINIMA
    assert prova.max_attempts == cenario.MAXIMO_DE_TENTATIVAS
    assert prova.show_score_after_submission is True
    assert prova.total_points == cenario.TOTAL_DE_PONTOS
    assert prova.is_archived is False
    assert prova.open_at < prova.close_at


def test_a_prova_demo_so_tem_questoes_objetivas(db):
    preparar()

    prova = Exam.objects.get(title=cenario.TITULO_DA_PROVA)
    tipos = set(prova.questions.values_list("type", flat=True))

    manuais = {QuestionType.SHORT_TEXT, QuestionType.ESSAY}
    assert not (tipos & manuais), (
        "questao de correcao manual travaria o resultado esperando um "
        "administrador, e o video precisa mostrar a nota saindo na hora"
    )


def test_a_prova_demo_tem_quatro_questoes_de_um_ponto(db):
    preparar()

    prova = Exam.objects.get(title=cenario.TITULO_DA_PROVA)
    questoes = list(prova.questions.filter(active=True).order_by("order"))

    assert len(questoes) == 4
    for questao in questoes:
        assert questao.points == cenario.PONTOS_POR_QUESTAO
    assert sum(q.points for q in questoes) == Decimal("4.00")


def test_o_enunciado_nao_traz_conteudo_do_cpo(db):
    preparar()

    prova = Exam.objects.get(title=cenario.TITULO_DA_PROVA)
    textos = " ".join(prova.questions.values_list("text", flat=True)).lower()

    for palavra in ("cpo", "presbitero", "diacono", "cooperador"):
        assert palavra not in textos, (
            "a prova DEMO nao pode conter conteudo real: e material de "
            "treinamento sobre a mecanica do sistema"
        )


def test_a_mensagem_de_reprovacao_orienta_o_aluno(db):
    preparar()

    prova = Exam.objects.get(title=cenario.TITULO_DA_PROVA)
    assert "coordenação" in prova.failure_message.lower()


# --- idempotencia ----------------------------------------------------------


def test_rodar_duas_vezes_nao_duplica_nada(db):
    preparar()
    depois_da_primeira = fotografar()

    preparar()
    depois_da_segunda = fotografar()

    assert depois_da_primeira == depois_da_segunda


def test_a_segunda_execucao_nao_reescreve_a_prova_publicada(db):
    preparar()
    prova = Exam.objects.get(title=cenario.TITULO_DA_PROVA)
    questoes_antes = set(prova.questions.values_list("pk", flat=True))
    opcoes_antes = set(
        QuestionOption.objects.filter(question__exam=prova).values_list(
            "pk", flat=True
        )
    )

    preparar()

    prova.refresh_from_db()
    assert prova.status == ExamStatus.PUBLISHED
    assert set(prova.questions.values_list("pk", flat=True)) == questoes_antes
    assert set(
        QuestionOption.objects.filter(question__exam=prova).values_list(
            "pk", flat=True
        )
    ) == opcoes_antes


def test_a_segunda_execucao_reabre_a_janela_da_prova(db):
    """
    A janela vencida e o estado em que uma gravacao futura encontraria a
    prova. Nao da para simular com close_at == open_at: a constraint
    exam_janela_coerente recusa no banco. O caso real e a janela inteira no
    passado.
    """
    preparar()
    prova = Exam.objects.get(title=cenario.TITULO_DA_PROVA)
    agora = timezone.now()
    prova.open_at = agora - timedelta(days=30)
    prova.close_at = agora - timedelta(days=15)
    prova.save(update_fields=["open_at", "close_at"])

    preparar()

    prova.refresh_from_db()
    assert prova.close_at > timezone.now(), (
        "sem reabrir a janela, a proxima gravacao esbarraria em "
        "'o periodo desta prova foi encerrado'"
    )


def test_preparar_reativa_o_cenario_desativado(db):
    preparar()
    limpar(desativar=True)

    preparar()

    modulo = Module.objects.get(code=cenario.CODIGO_DO_MODULO)
    assert modulo.is_active is True
    for aluno in User.objects.filter(email__in=cenario.EMAILS):
        assert aluno.is_active is True


# --- isolamento (secao 44 do enunciado) ------------------------------------


@pytest.fixture
def banco_com_dado_real(db, student_user, outro_student, admin_user, modulo):
    """Um pedaco de sistema real para o comando ter o que estragar."""
    from courses.services import create_enrollment

    from exams.services.exam import publish_exam

    agora = timezone.now()
    prova = Exam.objects.create(
        module=modulo,
        title="Avaliacao Real Que Nao Deve Ser Tocada",
        status=ExamStatus.DRAFT,
        open_at=agora - timedelta(days=1),
        close_at=agora + timedelta(days=30),
        duration_minutes=60,
        passing_score=Decimal("7.00"),
        max_attempts=2,
    )
    questao = Question.objects.create(
        exam=prova,
        type=QuestionType.TRUE_FALSE,
        text="Questao real",
        points=Decimal("1.00"),
        order=1,
    )
    QuestionOption.objects.create(
        question=questao, text="Verdadeiro", is_correct=True, order=1
    )
    QuestionOption.objects.create(
        question=questao, text="Falso", is_correct=False, order=2
    )
    # Publicada porque o teste do reset precisa de uma tentativa real, e
    # tentativa so nasce de prova publicada.
    publish_exam(prova)
    create_enrollment(student=student_user, module=modulo)
    return {"prova": prova, "questao": questao, "modulo": modulo}


def test_preparar_nao_altera_nenhum_dado_real(db, banco_com_dado_real):
    antes = fotografar()

    preparar()

    depois = fotografar()

    # Tudo que existia antes continua existindo.
    for tabela, chaves in antes.items():
        assert chaves <= depois[tabela], (
            "o comando removeu linha de {} que ja existia".format(tabela)
        )

    # E o que apareceu e exclusivamente DEMO.
    novos_usuarios = depois["usuarios"] - antes["usuarios"]
    assert set(
        User.objects.filter(pk__in=novos_usuarios).values_list("email", flat=True)
    ) == set(cenario.EMAILS)

    novos_modulos = depois["modulos"] - antes["modulos"]
    assert set(
        Module.objects.filter(pk__in=novos_modulos).values_list("code", flat=True)
    ) == {cenario.CODIGO_DO_MODULO}

    novas_provas = depois["provas"] - antes["provas"]
    assert set(
        Exam.objects.filter(pk__in=novas_provas).values_list("title", flat=True)
    ) == {cenario.TITULO_DA_PROVA}

    assert depois["tentativas"] == antes["tentativas"]
    assert depois["certificados"] == antes["certificados"]


def test_preparar_nao_mexe_na_prova_real(db, banco_com_dado_real):
    prova = banco_com_dado_real["prova"]
    antes = (prova.title, prova.status, prova.passing_score, prova.max_attempts)

    preparar()

    prova.refresh_from_db()
    assert (
        prova.title,
        prova.status,
        prova.passing_score,
        prova.max_attempts,
    ) == antes


def test_preparar_nao_matricula_aluno_real_no_modulo_demo(
    db, banco_com_dado_real, student_user
):
    preparar()

    modulo_demo = Module.objects.get(code=cenario.CODIGO_DO_MODULO)
    assert not Enrollment.objects.filter(
        module=modulo_demo, student=student_user
    ).exists()


def test_preparar_nao_desativa_modulo_real(db, banco_com_dado_real):
    modulo = banco_com_dado_real["modulo"]

    preparar()

    modulo.refresh_from_db()
    assert modulo.is_active is True


# --- reset (secao 30 do enunciado) -----------------------------------------


@pytest.fixture
def tentativa_real(db, student_user, banco_com_dado_real):
    """Uma tentativa de aluno real, que o reset nunca pode alcancar."""
    return criar_tentativa(student_user, banco_com_dado_real["prova"])


def test_o_reset_nao_toca_tentativa_de_aluno_real(db, tentativa_real):
    preparar(reset=True)

    assert ExamAttempt.objects.filter(pk=tentativa_real.pk).exists(), (
        "o reset apagou a tentativa de um aluno real"
    )


def test_o_reset_apaga_a_tentativa_do_aluno_demo(db):
    preparar()

    aluno = User.objects.get(email=cenario.EMAIL_APROVADO)
    prova = Exam.objects.get(title=cenario.TITULO_DA_PROVA)
    tentativa = criar_tentativa(aluno, prova)

    preparar(reset=True)

    assert not ExamAttempt.objects.filter(pk=tentativa.pk).exists()


def test_o_reset_apaga_as_respostas_junto(db):
    """
    O CASCADE precisa levar a arvore inteira: tentativa -> questao da
    tentativa -> resposta. Uma linha orfa em AttemptQuestion faria a proxima
    gravacao comecar com resto da anterior.
    """
    from exams.models import AttemptQuestion

    preparar()

    aluno = User.objects.get(email=cenario.EMAIL_APROVADO)
    prova = Exam.objects.get(title=cenario.TITULO_DA_PROVA)
    tentativa = criar_tentativa(aluno, prova)

    # start_attempt ja montou as questoes da tentativa; so falta a resposta.
    linha = tentativa.questions.first()
    assert linha is not None
    Answer.objects.create(attempt_question=linha, saved_at=timezone.now())

    preparar(reset=True)

    assert not Answer.objects.filter(attempt_question=linha).exists()
    assert not AttemptQuestion.objects.filter(pk=linha.pk).exists()


def test_o_reset_sem_tentativa_nao_quebra(db):
    preparar()
    preparar(reset=True)

    assert ExamAttempt.objects.count() == 0


# --- limpeza (secoes 31, 46, 47, 48) ---------------------------------------


def test_a_limpeza_e_dry_run_por_padrao(db):
    preparar()

    saida = limpar()

    assert "DRY RUN" in saida
    for aluno in User.objects.filter(email__in=cenario.EMAILS):
        assert aluno.is_active is True
    assert Module.objects.get(code=cenario.CODIGO_DO_MODULO).is_active is True


def test_a_limpeza_conta_o_que_existe(db):
    preparar()

    saida = limpar()

    assert "Usuarios DEMO ................. 2" in saida
    assert "Prova DEMO .................... 1" in saida
    assert "Questoes DEMO ................. 4" in saida


def test_desativar_fecha_as_contas_demo(db):
    preparar()

    limpar(desativar=True)

    for aluno in User.objects.filter(email__in=cenario.EMAILS):
        assert aluno.is_active is False, (
            "conta de demonstracao com senha conhecida nao pode ficar ativa"
        )


def test_desativar_tira_o_modulo_demo_do_ar(db):
    preparar()

    limpar(desativar=True)

    assert Module.objects.get(code=cenario.CODIGO_DO_MODULO).is_active is False


def test_desativar_preserva_o_cenario(db):
    preparar()

    limpar(desativar=True)

    assert User.objects.filter(email__in=cenario.EMAILS).count() == 2
    assert Exam.objects.filter(title=cenario.TITULO_DA_PROVA).exists()
    assert Question.objects.filter(
        exam__title=cenario.TITULO_DA_PROVA
    ).count() == 4


def test_desativar_nao_toca_dado_real(db, banco_com_dado_real, student_user):
    preparar()
    antes = fotografar()

    limpar(desativar=True)

    assert fotografar() == antes
    student_user.refresh_from_db()
    assert student_user.is_active is True
    banco_com_dado_real["modulo"].refresh_from_db()
    assert banco_com_dado_real["modulo"].is_active is True


def test_remover_exige_a_frase_de_confirmacao(db):
    from django.core.management.base import CommandError

    preparar()

    with pytest.raises(CommandError):
        limpar(remover=True)

    assert User.objects.filter(email__in=cenario.EMAILS).count() == 2


def test_remover_apaga_o_cenario_inteiro(db):
    preparar()

    limpar(remover=True, confirm="REMOVER-CENARIO-DEMO")

    assert not User.objects.filter(email__in=cenario.EMAILS).exists()
    assert not Module.objects.filter(code=cenario.CODIGO_DO_MODULO).exists()
    assert not Exam.objects.filter(title=cenario.TITULO_DA_PROVA).exists()


def test_remover_nao_toca_dado_real(db, banco_com_dado_real, tentativa_real):
    preparar()

    limpar(remover=True, confirm="REMOVER-CENARIO-DEMO")

    assert ExamAttempt.objects.filter(pk=tentativa_real.pk).exists()
    assert Exam.objects.filter(pk=banco_com_dado_real["prova"].pk).exists()
    assert Module.objects.filter(pk=banco_com_dado_real["modulo"].pk).exists()


def test_a_limpeza_nunca_apaga_a_trilha_de_auditoria(db):
    from audit.models import AuditLog

    preparar()
    antes = AuditLog.objects.count()

    limpar(remover=True, confirm="REMOVER-CENARIO-DEMO")

    assert AuditLog.objects.count() >= antes, (
        "a trilha e append-only: os eventos da demonstracao registram que "
        "ela aconteceu"
    )


# --- o roteiro de respostas ------------------------------------------------


def test_o_roteiro_aprovado_acerta_tudo(db):
    roteiro = cenario.roteiro_de_respostas(aprovado=True)

    assert len(roteiro) == 4
    for questao, (enunciado, marcar) in zip(cenario.QUESTOES, roteiro):
        corretas = {
            alt["texto"] for alt in questao["alternativas"] if alt["correta"]
        }
        assert set(marcar) == corretas
        assert enunciado == questao["enunciado"]


def test_o_roteiro_reprovado_erra_o_suficiente_para_reprovar():
    roteiro = cenario.roteiro_de_respostas(aprovado=False)

    acertos = 0
    for questao, (_, marcar) in zip(cenario.QUESTOES, roteiro):
        corretas = {
            alt["texto"] for alt in questao["alternativas"] if alt["correta"]
        }
        if set(marcar) == corretas:
            acertos += 1

    assert acertos == 2
    assert cenario.nota_esperada(aprovado=False) < cenario.NOTA_MINIMA


def test_a_nota_do_aprovado_alcanca_a_minima():
    assert cenario.nota_esperada(aprovado=True) >= cenario.NOTA_MINIMA
    assert cenario.nota_esperada(aprovado=True) == Decimal("10")


def test_toda_questao_tem_alternativa_errada_disponivel():
    """Sem isso o cenario reprovado nao teria como errar."""
    for questao in cenario.QUESTOES:
        erradas = [a for a in questao["alternativas"] if not a["correta"]]
        assert erradas, "questao {} nao tem alternativa incorreta".format(
            questao["ordem"]
        )


def test_o_roteiro_localiza_questao_por_enunciado_e_nao_por_posicao():
    """
    A prova pode ter sorteio de questoes ligado. Se o roteiro dependesse da
    ordem, a gravacao marcaria a alternativa de outra questao.
    """
    enunciados = [enunciado for enunciado, _ in cenario.roteiro_de_respostas(True)]
    assert len(set(enunciados)) == len(enunciados), (
        "dois enunciados iguais tornariam a busca por texto ambigua"
    )


# --- o certificado do cenario (secoes 24 e 43) -----------------------------


@pytest.fixture
def modelo_ativo(modelo_de_certificado):
    """
    Um modelo de certificado ativo, como o que a producao tem.

    Reaproveita o fixture do conftest em vez de montar um na mao: um modelo
    ACTIVE precisa de arte (constraint modelo_ativo_tem_arte) e de campos, e
    reproduzir essas precondicoes aqui seria manter uma segunda definicao do
    que torna um modelo valido.
    """
    return modelo_de_certificado


def test_o_modulo_demo_nasce_com_os_dados_do_certificado(db):
    preparar()

    modulo = Module.objects.get(code=cenario.CODIGO_DO_MODULO)
    assert modulo.dados_do_certificado_ausentes() == [], (
        "sem estes campos a emissao e recusada e o video do aprovado para "
        "antes do certificado"
    )


def test_os_textos_do_certificado_demo_se_identificam(db):
    preparar()

    modulo = Module.objects.get(code=cenario.CODIGO_DO_MODULO)
    assert "DEMONSTRAÇÃO" in modulo.certificate_location
    assert "DEMONSTRAÇÃO" in modulo.nome_no_certificado


def test_o_modulo_demo_aponta_para_o_modelo_ativo(db, modelo_ativo):
    preparar()

    modulo = Module.objects.get(code=cenario.CODIGO_DO_MODULO)
    assert modulo.certificate_template_id == modelo_ativo.pk


def test_apontar_o_modelo_nao_o_ativa_nem_o_torna_global(db, modelo_ativo):
    from certificates.models import CertificateTemplate

    antes = CertificateTemplate.objects.get(pk=modelo_ativo.pk)
    estado = (antes.status, antes.is_global, antes.version)

    preparar()

    depois = CertificateTemplate.objects.get(pk=modelo_ativo.pk)
    assert (depois.status, depois.is_global, depois.version) == estado, (
        "ativar ou globalizar um modelo trocaria a aparencia de todo "
        "documento emitido dali para a frente"
    )


def test_o_modelo_nao_vaza_para_modulo_real(db, banco_com_dado_real, modelo_ativo):
    modulo_real = banco_com_dado_real["modulo"]
    antes = modulo_real.certificate_template_id

    preparar()

    modulo_real.refresh_from_db()
    assert modulo_real.certificate_template_id == antes, (
        "o comando so pode apontar o modelo no modulo DEMO"
    )


def test_o_comando_nao_cria_modelo_de_certificado(db):
    from certificates.models import CertificateTemplate

    preparar()

    assert CertificateTemplate.objects.count() == 0, (
        "inventar um layout produziria documento oficial com estetica que "
        "ninguem aprovou; sem modelo ativo o video apenas para no resultado"
    )


def test_sem_modelo_ativo_o_modulo_demo_fica_sem_modelo(db):
    preparar()

    modulo = Module.objects.get(code=cenario.CODIGO_DO_MODULO)
    assert modulo.certificate_template_id is None
