"""Leitura e analise de CSV na importacao de alunos (students.importers)."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import User
from common.exceptions import DomainError
from courses.models import Enrollment, EnrollmentStatus, Module
from students.importers import RowStatus, analisar, confirmar, ler_arquivo

pytestmark = pytest.mark.django_db


TAMANHO_MAXIMO_BYTES = 5 * 1024 * 1024


def upload(conteudo, nome="alunos.csv", encoding="utf-8"):
    """Monta o arquivo enviado pelo administrador como o Django o entregaria."""
    return SimpleUploadedFile(nome, conteudo.encode(encoding), content_type="text/csv")


def linha(numero, nome, email, modulo):
    """Linha crua no formato que ler_arquivo devolve para analisar."""
    return {"numero": numero, "nome": nome, "email": email, "modulo": modulo}


def status_de(resultado):
    return [item.status for item in resultado.linhas]


# ---------------------------------------------------------------------------
# Leitura do arquivo
# ---------------------------------------------------------------------------


def test_csv_utf8_valido_devolve_as_linhas():
    arquivo = upload(
        "nome,email,modulo\n"
        "Joao da Silva,joao@exemplo.test,MOD1\n"
        "Maria Oliveira,maria@exemplo.test,MOD2\n"
    )

    linhas = ler_arquivo(arquivo)

    assert linhas == [
        linha(2, "Joao da Silva", "joao@exemplo.test", "MOD1"),
        linha(3, "Maria Oliveira", "maria@exemplo.test", "MOD2"),
    ]


def test_csv_com_bom_e_lido_como_um_csv_comum():
    """O Excel grava BOM ao salvar como 'CSV UTF-8'; e o arquivo mais comum."""
    arquivo = upload(
        "nome,email,modulo\nJoao da Silva,joao@exemplo.test,MOD1\n",
        encoding="utf-8-sig",
    )

    linhas = ler_arquivo(arquivo)

    # O BOM nao pode sobrar grudado no primeiro cabecalho, senao a coluna
    # "nome" deixaria de ser reconhecida.
    assert linhas == [linha(2, "Joao da Silva", "joao@exemplo.test", "MOD1")]


def test_csv_com_acentos_preserva_o_nome_do_aluno():
    arquivo = upload(
        "nome,email,modulo\nJoao Conceicao,joao@exemplo.test,MOD1\n".replace(
            "Joao Conceicao", "João Conceição"
        )
    )

    linhas = ler_arquivo(arquivo)

    assert linhas[0]["nome"] == "João Conceição"


def test_csv_em_codificacao_nao_utf8_e_recusado_com_orientacao():
    """
    Nao existe fallback silencioso para cp1252: um nome decodificado errado
    seria gravado corrompido no cadastro sem ninguem perceber.
    """
    conteudo = "nome,email,modulo\nJoao,joao@exemplo.test,MOD1\n".encode("utf-16")
    arquivo = SimpleUploadedFile("alunos.csv", conteudo, content_type="text/csv")

    with pytest.raises(DomainError) as erro:
        ler_arquivo(arquivo)

    assert "UTF-8" in str(erro.value)


def test_separador_ponto_e_virgula_e_reconhecido():
    """O Excel em portugues grava CSV com ponto e virgula."""
    arquivo = upload(
        "nome;email;modulo\n"
        "Joao da Silva;joao@exemplo.test;MOD1\n"
        "Maria Oliveira;maria@exemplo.test;MOD2\n"
    )

    linhas = ler_arquivo(arquivo)

    assert linhas == [
        linha(2, "Joao da Silva", "joao@exemplo.test", "MOD1"),
        linha(3, "Maria Oliveira", "maria@exemplo.test", "MOD2"),
    ]


@pytest.mark.parametrize(
    "conteudo",
    [
        "Nome,EMAIL,Modulo\nJoao da Silva,joao@exemplo.test,MOD1\n",
        "nome;e-mail;modulo\nJoao da Silva;joao@exemplo.test;MOD1\n",
        "NOME COMPLETO,E-Mail,Código do Módulo\n"
        "Joao da Silva,joao@exemplo.test,MOD1\n",
        "Aluno;E-MAIL;MÓDULOS\nJoao da Silva;joao@exemplo.test;MOD1\n",
    ],
)
def test_cabecalhos_com_caixa_e_acentos_variados_sao_reconhecidos(conteudo):
    linhas = ler_arquivo(upload(conteudo))

    assert linhas == [linha(2, "Joao da Silva", "joao@exemplo.test", "MOD1")]


def test_espacos_em_volta_dos_valores_sao_removidos():
    arquivo = upload(
        "nome,email,modulo\n"
        "  Joao da Silva  ,  joao@exemplo.test  ,  MOD1  \n"
    )

    linhas = ler_arquivo(arquivo)

    assert linhas == [linha(2, "Joao da Silva", "joao@exemplo.test", "MOD1")]


def test_coluna_obrigatoria_ausente_informa_qual_falta():
    arquivo = upload("nome,email\nJoao da Silva,joao@exemplo.test\n")

    with pytest.raises(DomainError) as erro:
        ler_arquivo(arquivo)

    mensagem = str(erro.value)
    assert "Faltando: modulo." in mensagem


def test_todas_as_colunas_ausentes_sao_listadas():
    arquivo = upload("coluna a,coluna b\nvalor,outro\n")

    with pytest.raises(DomainError) as erro:
        ler_arquivo(arquivo)

    mensagem = str(erro.value)
    assert "Faltando: nome, email, modulo." in mensagem


def test_arquivo_vazio_e_recusado():
    with pytest.raises(DomainError) as erro:
        ler_arquivo(SimpleUploadedFile("alunos.csv", b"", content_type="text/csv"))

    assert "vazio" in str(erro.value).lower()


def test_arquivo_so_com_espacos_e_recusado():
    with pytest.raises(DomainError) as erro:
        ler_arquivo(upload("   \n\n"))

    assert "vazio" in str(erro.value).lower()


def test_arquivo_apenas_com_cabecalho_e_recusado():
    with pytest.raises(DomainError) as erro:
        ler_arquivo(upload("nome,email,modulo\n"))

    assert "nenhuma linha de dados" in str(erro.value)


def test_extensao_nao_suportada_e_recusada():
    arquivo = upload(
        "nome,email,modulo\nJoao da Silva,joao@exemplo.test,MOD1\n",
        nome="alunos.txt",
    )

    with pytest.raises(DomainError) as erro:
        ler_arquivo(arquivo)

    assert "Formato nao suportado" in str(erro.value)


def test_arquivo_acima_do_limite_e_recusado_antes_da_leitura():
    """
    O tamanho e conferido antes de decodificar: recusar cedo evita carregar
    um arquivo abusivo inteiro em memoria.
    """
    excesso = "nome,email,modulo\n" + "x" * (TAMANHO_MAXIMO_BYTES + 1)
    arquivo = upload(excesso)

    with pytest.raises(DomainError) as erro:
        ler_arquivo(arquivo)

    assert "limite de 5 MB" in str(erro.value)


def test_linhas_em_branco_no_meio_do_arquivo_sao_ignoradas():
    arquivo = upload(
        "nome,email,modulo\n"
        "Joao da Silva,joao@exemplo.test,MOD1\n"
        "\n"
        "Maria Oliveira,maria@exemplo.test,MOD2\n"
        "   \n"
        "Pedro Alves,pedro@exemplo.test,MOD1\n"
    )

    linhas = ler_arquivo(arquivo)

    # A numeracao continua sendo a do arquivo, para que o administrador ache
    # a linha na planilha dele.
    assert [item["numero"] for item in linhas] == [2, 4, 6]
    assert [item["nome"] for item in linhas] == [
        "Joao da Silva",
        "Maria Oliveira",
        "Pedro Alves",
    ]


# ---------------------------------------------------------------------------
# Analise: um status por cenario
# ---------------------------------------------------------------------------


def test_analise_marca_novo_aluno(modulo):
    resultado = analisar([linha(2, "Ana Nova", "ana.nova@exemplo.test", "MOD1")])

    assert status_de(resultado) == [RowStatus.NOVO_ALUNO]
    assert resultado.importaveis[0].email == "ana.nova@exemplo.test"


def test_analise_marca_aluno_existente(student_user, outro_modulo):
    resultado = analisar(
        [linha(2, student_user.full_name, student_user.email, "MOD2")]
    )

    assert status_de(resultado) == [RowStatus.ALUNO_EXISTENTE]
    assert resultado.linhas[0].aviso == ""


def test_analise_marca_matricula_existente(matricula):
    resultado = analisar(
        [
            linha(
                2,
                matricula.student.full_name,
                matricula.student.email,
                matricula.module.code,
            )
        ]
    )

    assert status_de(resultado) == [RowStatus.MATRICULA_EXISTENTE]
    assert resultado.importaveis == []


def test_analise_marca_matricula_inativa_existente(matricula):
    """Reativar e decisao administrativa consciente, nunca efeito da importacao."""
    matricula.status = EnrollmentStatus.INACTIVE
    matricula.access_enabled = False
    matricula.save(update_fields=["status", "access_enabled"])

    resultado = analisar(
        [
            linha(
                2,
                matricula.student.full_name,
                matricula.student.email,
                matricula.module.code,
            )
        ]
    )

    assert status_de(resultado) == [RowStatus.MATRICULA_INATIVA]
    assert "Reative pela tela de matriculas" in resultado.linhas[0].aviso
    assert resultado.importaveis == []


def test_analise_marca_linha_duplicada(modulo):
    resultado = analisar(
        [
            linha(2, "Ana Nova", "ana.nova@exemplo.test", "MOD1"),
            linha(3, "Ana Nova", "ANA.NOVA@Exemplo.Test", "mod1"),
        ]
    )

    # A duplicidade e detectada apos normalizar e-mail e codigo: a segunda
    # linha aponta exatamente para o mesmo par aluno/modulo.
    assert status_de(resultado) == [RowStatus.NOVO_ALUNO, RowStatus.LINHA_DUPLICADA]
    assert resultado.linhas_duplicadas == 1


def test_analise_marca_modulo_nao_encontrado(modulo):
    resultado = analisar([linha(2, "Ana Nova", "ana.nova@exemplo.test", "MOD404")])

    assert status_de(resultado) == [RowStatus.MODULO_NAO_ENCONTRADO]
    assert resultado.importaveis == []


def test_analise_marca_email_invalido(modulo):
    resultado = analisar([linha(2, "Ana Nova", "ana.nova.exemplo.test", "MOD1")])

    assert status_de(resultado) == [RowStatus.EMAIL_INVALIDO]
    assert resultado.linhas_invalidas == 1


def test_analise_marca_nome_ausente(modulo):
    resultado = analisar([linha(2, "   ", "ana.nova@exemplo.test", "MOD1")])

    assert status_de(resultado) == [RowStatus.NOME_AUSENTE]


def test_analise_marca_modulo_ausente(modulo):
    resultado = analisar([linha(2, "Ana Nova", "ana.nova@exemplo.test", "  ")])

    assert status_de(resultado) == [RowStatus.MODULO_AUSENTE]


def test_analise_marca_email_de_administrador(admin_user, modulo):
    """Promover um ADMIN a aluno por planilha seria mudanca de papel silenciosa."""
    resultado = analisar([linha(2, admin_user.full_name, admin_user.email, "MOD1")])

    assert status_de(resultado) == [RowStatus.EMAIL_DE_ADMIN]
    assert resultado.importaveis == []
    assert Enrollment.objects.filter(student=admin_user).count() == 0


def test_analise_marca_modulo_inativo_como_nao_encontrado(modulo_inativo):
    resultado = analisar([linha(2, "Ana Nova", "ana.nova@exemplo.test", "MOD9")])

    assert status_de(resultado) == [RowStatus.MODULO_NAO_ENCONTRADO]
    assert "inativo" in resultado.linhas[0].aviso


# ---------------------------------------------------------------------------
# Identificacao do modulo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("informado", ["MOD1", "mod1", "  Mod1  "])
def test_modulo_e_identificado_pelo_codigo_ignorando_caixa(modulo, informado):
    resultado = analisar([linha(2, "Ana Nova", "ana.nova@exemplo.test", informado)])

    assert status_de(resultado) == [RowStatus.NOVO_ALUNO]


def test_modulo_e_identificado_pelo_nome_exato(modulo):
    resultado = analisar([linha(2, "Ana Nova", "ana.nova@exemplo.test", "Modulo 1")])

    assert status_de(resultado) == [RowStatus.NOVO_ALUNO]


def test_nome_de_modulo_ambiguo_nao_e_resolvido(modulo):
    """
    Com dois modulos de mesmo nome, escolher um deles matricularia o aluno no
    modulo errado em silencio. A linha precisa cair como nao encontrada.
    """
    Module.objects.create(name="Modulo 1", code="MOD1B", order=3)

    resultado = analisar([linha(2, "Ana Nova", "ana.nova@exemplo.test", "Modulo 1")])

    assert status_de(resultado) == [RowStatus.MODULO_NAO_ENCONTRADO]


def test_codigo_continua_resolvendo_quando_o_nome_e_ambiguo(modulo):
    Module.objects.create(name="Modulo 1", code="MOD1B", order=3)

    resultado = analisar([linha(2, "Ana Nova", "ana.nova@exemplo.test", "MOD1B")])

    assert status_de(resultado) == [RowStatus.NOVO_ALUNO]


# ---------------------------------------------------------------------------
# Divergencia de cadastro e contagens
# ---------------------------------------------------------------------------


def test_aluno_existente_com_nome_diferente_gera_aviso_sem_alterar_o_banco(
    student_user, outro_modulo
):
    """A importacao matricula; nunca sobrescreve o cadastro que ja existe."""
    resultado = analisar([linha(2, "Joao Silva", student_user.email, "MOD2")])

    analisada = resultado.linhas[0]
    assert analisada.status == RowStatus.ALUNO_EXISTENTE
    assert "difere do cadastro atual" in analisada.aviso
    assert student_user.full_name in analisada.aviso

    student_user.refresh_from_db()
    assert student_user.full_name == "Joao da Silva"


def test_mesmo_aluno_em_dois_modulos_conta_um_novo_aluno_e_duas_matriculas(
    modulo, outro_modulo
):
    resultado = analisar(
        [
            linha(2, "Ana Nova", "ana.nova@exemplo.test", "MOD1"),
            linha(3, "Ana Nova", "ana.nova@exemplo.test", "MOD2"),
        ]
    )

    assert resultado.novos_alunos == 1
    assert resultado.novas_matriculas == 2
    assert resultado.total_linhas == 2


def test_resumo_soma_cada_categoria_de_linha(
    student_user, matricula, outro_modulo, admin_user
):
    resultado = analisar(
        [
            linha(2, "Ana Nova", "ana.nova@exemplo.test", "MOD1"),
            linha(3, student_user.full_name, student_user.email, "MOD2"),
            linha(4, student_user.full_name, student_user.email, "MOD1"),
            linha(5, "Ana Nova", "ana.nova@exemplo.test", "MOD1"),
            linha(6, "Bruno Erro", "bruno-sem-arroba", "MOD1"),
            linha(7, admin_user.full_name, admin_user.email, "MOD1"),
        ]
    )

    assert resultado.resumo() == {
        "total_linhas": 6,
        "novos_alunos": 1,
        "alunos_existentes": 1,
        "novas_matriculas": 2,
        "linhas_invalidas": 2,
        "modulo_inexistente": 0,
        "matriculas_existentes": 1,
        "linhas_duplicadas": 1,
    }


def test_analise_nao_escreve_nada_no_banco(student_user, modulo, outro_modulo):
    usuarios_antes = User.objects.count()
    matriculas_antes = Enrollment.objects.count()

    analisar(
        [
            linha(2, "Ana Nova", "ana.nova@exemplo.test", "MOD1"),
            linha(3, student_user.full_name, student_user.email, "MOD2"),
        ]
    )

    assert User.objects.count() == usuarios_antes
    assert Enrollment.objects.count() == matriculas_antes


# ---------------------------------------------------------------------------
# Confirmacao a partir do arquivo lido
# ---------------------------------------------------------------------------


def test_fluxo_do_arquivo_ate_a_confirmacao_cria_aluno_e_matriculas(
    modulo, outro_modulo
):
    arquivo = upload(
        "nome;e-mail;modulo\n"
        "Ana Nova;ANA.NOVA@Exemplo.Test;mod1\n"
        "Ana Nova;ana.nova@exemplo.test;Modulo 2\n"
    )

    resumo = confirmar(ler_arquivo(arquivo))

    aluno = User.objects.get(email="ana.nova@exemplo.test")
    assert resumo["alunos_criados"] == 1
    assert resumo["matriculas_criadas"] == 2
    # A partir da Etapa 5 o aluno nao troca a propria senha, entao a flag
    # nasce False tambem na importacao: obriga-lo a trocar o mandaria para
    # uma tela que agora responde 403.
    assert aluno.must_change_password is False
    assert set(
        Enrollment.objects.filter(student=aluno).values_list("module__code", flat=True)
    ) == {"MOD1", "MOD2"}


def test_confirmar_sem_nenhuma_linha_importavel_e_recusado(matricula):
    linhas = [
        linha(
            2,
            matricula.student.full_name,
            matricula.student.email,
            matricula.module.code,
        )
    ]

    with pytest.raises(DomainError) as erro:
        confirmar(linhas)

    assert "Nao ha nenhuma linha valida para importar." in str(erro.value)


def test_confirmar_recusa_tudo_quando_a_senha_inicial_nao_esta_configurada(
    settings, modulo
):
    """Falha antes de qualquer escrita: conta sem senha seria acessivel por todos."""
    settings.DEFAULT_STUDENT_PASSWORD = ""
    usuarios_antes = User.objects.count()

    with pytest.raises(DomainError):
        confirmar([linha(2, "Ana Nova", "ana.nova@exemplo.test", "MOD1")])

    assert User.objects.count() == usuarios_antes
