"""
Preenche points_snapshot nas questoes de tentativas que ja existiam.

O campo nasceu nulo em 0006 porque nao havia como preenche-lo na criacao da
coluna. Aqui ele recebe o valor da Question relacionada — que e exatamente o
valor com que aquelas questoes foram apresentadas, ja que a prova publicada e
imutavel desde a Etapa 3.

Nao torna o campo obrigatorio no fim.

Isso e escolha, e nao esquecimento. Um NOT NULL exigiria que TODA linha
existente estivesse preenchida no instante do deploy, e um unico registro
orfao — uma tentativa de uma prova removida por script, por exemplo — faria a
migration falhar em producao, no meio de uma janela de manutencao, sem um
caminho obvio de saida. O ganho seria pequeno: quem escreve a coluna e
start_attempt, que sempre a preenche, e a constraint de teto ja trata o nulo
explicitamente.

Reversao: nao apaga nada. Desfazer esta migration nao precisa zerar os
valores, porque a coluna aceita nulo e um valor correto nao atrapalha.
"""

from django.db import migrations


def preencher(apps, schema_editor):
    AttemptQuestion = apps.get_model("exams", "AttemptQuestion")

    # Em lotes, e nao um UPDATE ... FROM unico, para nao segurar um lock longo
    # sobre a tabela caso ela ja seja grande no momento do deploy.
    pendentes = AttemptQuestion.objects.filter(points_snapshot__isnull=True)

    lote = []
    for linha in pendentes.select_related("question").iterator(chunk_size=500):
        linha.points_snapshot = linha.question.points
        lote.append(linha)
        if len(lote) >= 500:
            AttemptQuestion.objects.bulk_update(lote, ["points_snapshot"])
            lote = []

    if lote:
        AttemptQuestion.objects.bulk_update(lote, ["points_snapshot"])


def nada_a_desfazer(apps, schema_editor):
    """Reversivel por construcao: o valor gravado nao atrapalha ninguem."""


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0006_correcao_e_notas"),
    ]

    operations = [
        migrations.RunPython(preencher, nada_a_desfazer),
    ]
