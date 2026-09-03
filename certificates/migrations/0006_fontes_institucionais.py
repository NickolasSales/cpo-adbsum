"""
Fontes institucionais: o negrito booleano vira peso.

O que muda
----------
    bold = False   ->   font_weight = 400
    bold = True    ->   font_weight = 700

E so isso, e e reversivel exatamente ao contrario. Nenhuma linha existente
troca de aparencia: 400 e 700 sao os mesmos dois desenhos que o booleano
alcancava.

Por que trocar
--------------
Montserrat e Bodoni Moda tem QUATRO pesos com arquivo proprio cada uma —
Regular, Medio, Semibold e Negrito. Um booleano so alcanca dois deles, e o
Semibold, que e o que aproxima o titulo da arte oficial, ficaria sem como ser
escolhido. Manter os dois — booleano e peso — criaria dois lugares para o
mesmo fato, e dois lugares divergem: bastaria gravar peso 500 com negrito
marcado para ninguem saber qual dos dois manda.

Os certificados ja emitidos nao sao tocados. O snapshot deles e JSON e
continua guardando `bold`; quem le e certificates.render._fonte, que entende
as tres geracoes de snapshot. Reescrever documento assinado para arrumar
nomenclatura seria trocar historico por estetica.

As constraints
--------------
`campo_familia_de_fonte_conhecida` e recriada porque a lista branca cresceu:
eram tres familias, agora sao sete. `campo_peso_de_fonte_conhecido` e nova, e
e a camada que sobrevive a um UPDATE direto no banco.
"""

from django.db import migrations, models
from django.db.models import Q

# Os valores CONGELADOS no momento desta migration.
#
# Migration nao importa da aplicacao: o codigo de amanha pode ganhar uma
# oitava familia, e esta migration precisa continuar produzindo o mesmo banco
# que produziu hoje. Ler a lista viva faria a historia mudar sozinha.
FAMILIAS = (
    "BODONI_MODA",
    "MONTSERRAT",
    "GREAT_VIBES",
    "ALLURA",
    "Helvetica",
    "Times",
    "Courier",
)

PESOS = (400, 500, 600, 700)

ROTULOS = ((400, "Regular"), (500, "Medio"), (600, "Semibold"), (700, "Negrito"))

ROTULOS_DAS_FAMILIAS = (
    ("BODONI_MODA", "Bodoni Moda"),
    ("MONTSERRAT", "Montserrat"),
    ("GREAT_VIBES", "Great Vibes"),
    ("ALLURA", "Allura"),
    ("Helvetica", "Helvetica"),
    ("Times", "Times"),
    ("Courier", "Courier"),
)


def peso_a_partir_do_negrito(apps, schema_editor):
    Campo = apps.get_model("certificates", "CertificateTemplateField")
    Campo.objects.filter(bold=True).update(font_weight=700)
    Campo.objects.filter(bold=False).update(font_weight=400)


def negrito_a_partir_do_peso(apps, schema_editor):
    """
    Volta para o booleano.

    Semibold e Medio viram negrito=False: sao mais leves que 700, e o
    booleano nao tem como guardar a diferenca. A perda e inerente a voltar
    atras, e acontece em silencio porque nao ha para onde escrever o resto.
    """
    Campo = apps.get_model("certificates", "CertificateTemplateField")
    Campo.objects.filter(font_weight__gte=700).update(bold=True)
    Campo.objects.filter(font_weight__lt=700).update(bold=False)


class Migration(migrations.Migration):

    dependencies = [
        ("certificates", "0005_editor_visual"),
    ]

    operations = [
        # A constraint antiga sai primeiro: ela lista as tres familias de
        # antes, e um campo em MONTSERRAT nao passaria por ela.
        migrations.RemoveConstraint(
            model_name="certificatetemplatefield",
            name="campo_familia_de_fonte_conhecida",
        ),
        migrations.AddField(
            model_name="certificatetemplatefield",
            name="font_weight",
            field=models.PositiveSmallIntegerField(
                choices=list(ROTULOS), default=400, verbose_name="peso"
            ),
        ),
        migrations.RunPython(peso_a_partir_do_negrito, negrito_a_partir_do_peso),
        migrations.RemoveField(
            model_name="certificatetemplatefield",
            name="bold",
        ),
        migrations.AlterField(
            model_name="certificatetemplatefield",
            name="font_family",
            field=models.CharField(
                choices=list(ROTULOS_DAS_FAMILIAS),
                default="Helvetica",
                max_length=32,
                verbose_name="fonte",
            ),
        ),
        migrations.AddConstraint(
            model_name="certificatetemplatefield",
            constraint=models.CheckConstraint(
                condition=Q(font_family__in=FAMILIAS),
                name="campo_familia_de_fonte_conhecida",
            ),
        ),
        migrations.AddConstraint(
            model_name="certificatetemplatefield",
            constraint=models.CheckConstraint(
                condition=Q(font_weight__in=PESOS),
                name="campo_peso_de_fonte_conhecido",
            ),
        ),
    ]
