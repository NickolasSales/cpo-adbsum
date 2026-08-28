"""
Coerencia da linhagem imposta pelo banco.

exams.services ja garante que uma prova nasce raiz (root nulo, parent nulo,
versao 1) e que toda copia aponta raiz e origem com versao a partir de 2.
Este arquivo cobre a camada de baixo: mesmo que alguem escreva um caminho
novo, um comando de gestao ou uma migration de dados, o banco recusa estados
que nao descrevem uma linhagem valida.

Por isso todos os ataques aqui usam objects.create e QuerySet.update, que nao
passam por form, nem por full_clean, nem pelos services. Um teste que so
tentasse pelo formulario estaria testando o formulario, nao a constraint.

Os limites do que o banco consegue impor sozinho
------------------------------------------------
Uma CheckConstraint enxerga apenas a propria linha. Ela consegue exigir que
raiz, origem e versao sejam coerentes *entre si*, e e isso que esta aqui. Ela
nao consegue exigir que parent.root_exam seja a mesma raiz da linha, porque
isso e comparacao entre linhas. Essa consistencia continua sendo do service, e
esta coberta em test_duplication.py.

As constraints de unicidade e faixa de versao ficam em test_models.py, na
secao Versionamento.
"""

from contextlib import contextmanager

import pytest
from django.db import IntegrityError, transaction

from exams.models import Exam
from exams.services import create_exam, duplicate_exam

pytestmark = pytest.mark.django_db

RAIZ_E_VERSAO = "exam_raiz_e_versao_coerentes"
LINHAGEM_PARENT = "exam_linhagem_parent_coerente"


def criar(modulo, **campos):
    """Escreve direto na tabela, sem passar por service nem validacao."""
    campos.setdefault("title", "Prova")
    return Exam.objects.create(module=modulo, **campos)


@contextmanager
def recusado_por(*nomes):
    """
    Exige que o bloco falhe por uma constraint especifica.

    Checar apenas IntegrityError seria fraco demais: a tabela tem nove
    constraints, e um teste mal montado passaria por esbarrar em outra. Foi
    isso que quase aconteceu em test_models.py, onde duas linhas de teste
    criavam copias sem origem — o teste da unicidade teria passado sem nunca
    exercitar a unicidade.

    psycopg expoe o nome violado em diag.constraint_name, que nao depende do
    idioma do servidor. A mensagem de texto do PostgreSQL vem traduzida e nao
    serviria para comparar.

    Aceita mais de um nome porque ha escritas que quebram as duas invariantes
    ao mesmo tempo. Nesses casos qual das duas dispara e ordem de verificacao
    do PostgreSQL, detalhe interno que nao vale fixar num teste; o que importa
    e que a escrita foi recusada pelo hardening da linhagem, e nao por acaso.
    """
    with pytest.raises(IntegrityError) as erro:
        with transaction.atomic():
            yield

    origem = erro.value.__cause__
    violada = getattr(getattr(origem, "diag", None), "constraint_name", None)
    assert violada in nomes, "esperava {}, veio {}".format(" ou ".join(nomes), violada)


# ---------------------------------------------------------------------------
# exam_raiz_e_versao_coerentes
#
#   (root_exam IS NULL AND version = 1) OR (root_exam IS NOT NULL AND version >= 2)
#
# Ter raiz e ser a raiz sao estados exclusivos. Ou a prova e a primeira
# versao e nao aponta para ninguem, ou ela deriva de uma linhagem e sua
# versao e no minimo a segunda.
# ---------------------------------------------------------------------------


def test_versao_um_apontando_para_uma_raiz_e_recusada(prova, modulo):
    """
    O estado mais enganoso dos quatro: a linha parece uma copia, mas se diz
    primeira versao. A linhagem passaria a ter duas provas reivindicando ser
    o comeco, e da_linhagem_de devolveria um conjunto sem ordem definida.
    """
    with recusado_por(RAIZ_E_VERSAO):
        criar(modulo, version=1, root_exam=prova, parent_exam=prova)


def test_versao_dois_sem_raiz_e_recusada(prova, modulo):
    """
    Uma raiz que se diz segunda versao. Duplicar essa prova calcularia a
    proxima versao sobre uma linhagem que so contem ela mesma, e a numeracao
    passaria a mentir sobre quantas versoes existiram.
    """
    with recusado_por(RAIZ_E_VERSAO):
        criar(modulo, version=2, root_exam=None, parent_exam=None)


def test_promover_uma_copia_a_raiz_sem_baixar_a_versao_e_recusado(
    prova_publicada, admin_user
):
    """
    Ataque por update, e nao por create: a linha nasce valida e depois alguem
    tenta transforma-la em raiz mantendo a versao. E o caminho que um script
    de correcao de dados mal escrito tomaria.

    As duas referencias sao zeradas no mesmo update de proposito. Zerar so a
    raiz quebraria tambem exam_linhagem_parent_coerente, e o teste passaria
    sem provar nada sobre a coerencia entre raiz e versao — que e justamente
    o que ele existe para verificar.
    """
    copia = duplicate_exam(prova_publicada, actor=admin_user)
    assert copia.version == 2

    with recusado_por(RAIZ_E_VERSAO):
        Exam.objects.filter(pk=copia.pk).update(root_exam=None, parent_exam=None)


def test_rebaixar_a_versao_de_uma_copia_para_um_e_recusado(
    prova_publicada, admin_user
):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    with recusado_por(RAIZ_E_VERSAO):
        Exam.objects.filter(pk=copia.pk).update(version=1)


# ---------------------------------------------------------------------------
# exam_linhagem_parent_coerente
#
#   (root_exam IS NULL AND parent_exam IS NULL)
#   OR (root_exam IS NOT NULL AND parent_exam IS NOT NULL)
#
# As duas referencias tem papeis diferentes, mas existem ou faltam juntas.
# Uma copia sem origem perde a procedencia; uma raiz com origem nao e raiz.
# ---------------------------------------------------------------------------


def test_copia_sem_origem_e_recusada(prova, modulo):
    """
    Raiz preenchida e origem vazia. A prova pertenceria a linhagem sem que
    ninguem soubesse de qual versao ela saiu, e a procedencia se perderia
    justamente no historico que o versionamento existe para preservar.
    """
    with recusado_por(LINHAGEM_PARENT):
        criar(modulo, version=2, root_exam=prova, parent_exam=None)


def test_raiz_com_origem_e_recusada(prova, modulo):
    """
    Origem preenchida e raiz vazia. A prova se diz comeco de linhagem e ao
    mesmo tempo derivada de outra.
    """
    with recusado_por(LINHAGEM_PARENT):
        criar(modulo, version=1, root_exam=None, parent_exam=prova)


def test_apagar_a_origem_de_uma_copia_por_update_e_recusado(
    prova_publicada, admin_user
):
    copia = duplicate_exam(prova_publicada, actor=admin_user)

    with recusado_por(LINHAGEM_PARENT):
        Exam.objects.filter(pk=copia.pk).update(parent_exam=None)


def test_dar_origem_a_uma_raiz_por_update_e_recusado(prova, modulo, admin_user):
    outra = create_exam(module=modulo, title="Outra prova", actor=admin_user)

    with recusado_por(LINHAGEM_PARENT):
        Exam.objects.filter(pk=prova.pk).update(parent_exam=outra)


# ---------------------------------------------------------------------------
# O que continua valido
#
# Uma constraint que recusa tudo tambem passaria nos testes acima. Estes
# fixam o outro lado: os estados que o sistema realmente produz precisam
# continuar gravaveis.
# ---------------------------------------------------------------------------


def test_raiz_continua_valida(modulo, admin_user):
    raiz = create_exam(module=modulo, title="Avaliacao", actor=admin_user)

    assert raiz.version == 1
    assert raiz.root_exam_id is None
    assert raiz.parent_exam_id is None


def test_cadeia_de_versoes_continua_valida(prova_publicada, admin_user):
    """
    A cadeia que o sistema produz de verdade: v3 deriva da v2, mas as duas
    apontam para a mesma raiz. As constraints precisam aceitar exatamente
    isso, incluindo o caso em que origem e raiz sao provas diferentes.
    """
    v2 = duplicate_exam(prova_publicada, actor=admin_user)
    v3 = duplicate_exam(v2, actor=admin_user)

    assert (v2.version, v2.root_exam_id, v2.parent_exam_id) == (
        2,
        prova_publicada.pk,
        prova_publicada.pk,
    )
    assert (v3.version, v3.root_exam_id, v3.parent_exam_id) == (
        3,
        prova_publicada.pk,
        v2.pk,
    )


def test_versao_alta_gravada_direto_continua_valida(prova, modulo):
    """
    A constraint fala de coerencia, nao de sequencia. Uma migration de dados
    legitima pode precisar gravar a v7 sem que a v6 exista, e isso nao e o
    que estamos proibindo.
    """
    copia = criar(modulo, version=7, root_exam=prova, parent_exam=prova)
    assert copia.pk is not None


def test_todas_as_provas_criadas_pelos_services_satisfazem_as_constraints(
    prova_publicada, admin_user
):
    """
    Fecha o circulo entre as duas camadas: o que o service produz precisa
    caber no que o banco aceita. Se um dia divergirem, o erro aparece aqui e
    nao em producao.
    """
    v2 = duplicate_exam(prova_publicada, actor=admin_user)
    duplicate_exam(v2, actor=admin_user)

    for exam in Exam.objects.all():
        e_raiz = exam.root_exam_id is None
        assert e_raiz == (exam.parent_exam_id is None)
        assert (exam.version == 1) if e_raiz else (exam.version >= 2)


# ---------------------------------------------------------------------------
# Consequencia de apagar uma prova da linhagem
# ---------------------------------------------------------------------------


def test_apagar_a_raiz_de_uma_linhagem_com_versoes_e_recusado(
    prova_publicada, admin_user
):
    """
    Documenta um efeito real das constraints novas, e nao um comportamento
    desejado por si so.

    root_exam e parent_exam usam SET_NULL. Apagar a v1 tentaria zerar as duas
    referencias da v2, que ficaria com raiz nula e versao 2 — exatamente o
    estado que passamos a proibir. Antes deste hardening o delete passava e
    deixava a v2 se dizendo raiz da propria linhagem, com a numeracao ja
    gasta; agora ele falha, ruidosamente.

    Nao existe rota, service nem tela que apague provas: o Django admin esta
    somente leitura e a Etapa 3 nao criou delete_exam. Este teste fixa a
    consequencia para quem for escrever essa rota um dia, e a decisao entre
    trocar SET_NULL por PROTECT ou tratar o caso no service fica registrada
    no README.
    """
    duplicate_exam(prova_publicada, actor=admin_user)

    with recusado_por(RAIZ_E_VERSAO, LINHAGEM_PARENT):
        prova_publicada.delete()


def test_apagar_uma_prova_sem_versoes_continua_funcionando(modulo, admin_user):
    """
    O contraponto do teste acima: sem linhagem derivada nao ha referencia
    para zerar, e o delete nao esbarra em constraint nenhuma.
    """
    solta = create_exam(module=modulo, title="Prova sem copias", actor=admin_user)
    identificador = solta.pk

    solta.delete()

    assert not Exam.objects.filter(pk=identificador).exists()


def test_apagar_a_linhagem_inteira_num_unico_queryset_tambem_e_recusado(
    prova_publicada, admin_user
):
    """
    O resultado contraintuitivo, e por isso o teste mais util do arquivo.

    Seria razoavel esperar que apagar todas as versoes de uma vez passasse,
    ja que nenhuma linha sobreviveria para ficar incoerente. Nao passa: o
    collector do Django emite

        UPDATE exams_exam SET parent_exam_id = NULL WHERE parent_exam_id IN (...)

    *antes* dos DELETEs, e esse UPDATE atinge tambem as linhas que serao
    apagadas em seguida. Por um instante a v2 fica com origem nula e raiz
    preenchida, e a constraint recusa nesse instante.

    Quem precisar apagar uma linhagem deve ir da versao mais nova para a mais
    antiga, uma de cada vez, como no teste seguinte.
    """
    duplicate_exam(prova_publicada, actor=admin_user)
    linhagem = list(
        Exam.objects.da_linhagem_de(prova_publicada).values_list("pk", flat=True)
    )
    assert len(linhagem) == 2

    with recusado_por(RAIZ_E_VERSAO, LINHAGEM_PARENT):
        Exam.objects.filter(pk__in=linhagem).delete()


def test_apagar_a_linhagem_da_versao_mais_nova_para_a_mais_antiga_funciona(
    prova_publicada, admin_user
):
    """
    O caminho suportado para remover uma linhagem inteira.

    Cada versao so e apagada quando ninguem mais aponta para ela, entao nao
    existe o instante intermediario que a constraint recusa. Fica registrado
    aqui porque nao e obvio, e porque a Etapa 3 nao tem rota de exclusao de
    prova: quem escrever essa rota precisa desta ordem.
    """
    duplicate_exam(prova_publicada, actor=admin_user)

    for exam in Exam.objects.da_linhagem_de(prova_publicada).order_by("-version"):
        exam.delete()

    assert Exam.objects.count() == 0


# ---------------------------------------------------------------------------
# As constraints existem no schema
# ---------------------------------------------------------------------------


def test_as_duas_constraints_estao_declaradas_no_modelo():
    """
    Guarda contra remocao silenciosa. Apagar uma constraint do Meta e uma
    linha; sem esta verificacao, os testes de recusa acima simplesmente
    parariam de falhar por outro motivo e ninguem notaria.
    """
    nomes = {
        constraint.name for constraint in Exam._meta.constraints
    }
    assert "exam_raiz_e_versao_coerentes" in nomes
    assert "exam_linhagem_parent_coerente" in nomes


def test_as_constraints_existem_no_banco():
    """
    O modelo declarar nao prova que a migration aplicou. Esta consulta le o
    catalogo do PostgreSQL e confirma que as duas chegaram na tabela.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname
              FROM pg_constraint
             WHERE conrelid = %s::regclass
               AND contype = 'c'
            """,
            [Exam._meta.db_table],
        )
        nomes = {linha[0] for linha in cursor.fetchall()}

    assert "exam_raiz_e_versao_coerentes" in nomes
    assert "exam_linhagem_parent_coerente" in nomes


def test_sem_as_constraints_as_mesmas_escritas_passariam(prova, modulo):
    """
    Contraprova: mostra que sao as constraints que recusam, e nao outra coisa.

    Os testes de recusa acima passariam mesmo que o motivo do IntegrityError
    fosse outro — um NOT NULL esquecido, uma FK invalida. Aqui as duas
    constraints sao derrubadas e exatamente as mesmas escritas passam a ser
    aceitas, o que so pode acontecer se eram elas as responsaveis.

    DDL no PostgreSQL e transacional e o pytest-django envolve cada teste numa
    transacao que sofre rollback no fim, entao a tabela volta ao normal sem
    que este teste precise recriar nada.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        # As linhas criadas pelas fixtures deixam gatilhos de chave estrangeira
        # pendentes nesta transacao, e o PostgreSQL recusa ALTER TABLE enquanto
        # houver algum. Forcar a verificacao agora esvazia a fila.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(
            'ALTER TABLE "{}" DROP CONSTRAINT {}, DROP CONSTRAINT {}'.format(
                Exam._meta.db_table, RAIZ_E_VERSAO, LINHAGEM_PARENT
            )
        )

    # Os quatro estados que o hardening existe para proibir.
    assert criar(modulo, version=1, root_exam=prova, parent_exam=prova).pk
    assert criar(modulo, version=2, root_exam=None, parent_exam=None).pk
    assert criar(modulo, version=2, root_exam=prova, parent_exam=None).pk
    assert criar(modulo, version=1, root_exam=None, parent_exam=prova).pk
