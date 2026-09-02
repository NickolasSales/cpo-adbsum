"""
Data de conclusao congelada, e fonte separada em familia + estilo.

Tres mudancas, todas aditivas. Nenhuma migration anterior foi tocada.

1. Certificate.completed_at_snapshot
   A data em que a avaliacao foi fechada, copiada de ExamAttempt.graded_at.
   Os certificados que ja existem recebem a data da propria tentativa: ela
   e o dado historicamente correto e ja esta no banco. Nao ha invencao aqui
   — nenhuma linha recebe "hoje".

2. CertificateTemplateField.bold / .italic
   O estilo passa a ser escolhido a parte da familia.

3. font_family passa a guardar a FAMILIA
   "Times-BoldItalic" vira ("Times", bold=True, italic=True). A conversao
   e reversivel: o caminho de volta recompoe o nome.

A ordem importa. A constraint de familia entra depois da conversao, senao
recusaria as linhas que ainda guardam o nome composto.
"""

from django.db import migrations, models
from django.db.models import OuterRef, Subquery

# A tabela de fontes, congelada aqui de proposito.
#
# Importar de certificates.models faria esta migration mudar de
# comportamento no dia em que a tabela ganhar uma familia — e uma migration
# ja aplicada precisa continuar significando o que significava quando rodou.
COMPOSICAO = {
    ("Helvetica", False, False): "Helvetica",
    ("Helvetica", True, False): "Helvetica-Bold",
    ("Helvetica", False, True): "Helvetica-Oblique",
    ("Helvetica", True, True): "Helvetica-BoldOblique",
    ("Times", False, False): "Times-Roman",
    ("Times", True, False): "Times-Bold",
    ("Times", False, True): "Times-Italic",
    ("Times", True, True): "Times-BoldItalic",
    ("Courier", False, False): "Courier",
    ("Courier", True, False): "Courier-Bold",
    ("Courier", False, True): "Courier-Oblique",
    ("Courier", True, True): "Courier-BoldOblique",
}

DECOMPOSICAO = {nome: chave for chave, nome in COMPOSICAO.items()}


def congelar_data_de_conclusao(apps, schema_editor):
    """
    Copia graded_at da tentativa para os certificados existentes.

    Subquery e nao F(): um UPDATE do Django nao atravessa chave estrangeira
    no lado direito da atribuicao.

    Certificados de tentativa sem graded_at — se existirem — ficam nulos, e
    o renderizador cai em issued_at. Preencher com a data de hoje seria
    afirmar uma conclusao que ninguem registrou.
    """
    Certificate = apps.get_model("certificates", "Certificate")
    ExamAttempt = apps.get_model("exams", "ExamAttempt")

    Certificate.objects.filter(completed_at_snapshot__isnull=True).update(
        completed_at_snapshot=Subquery(
            ExamAttempt.objects.filter(pk=OuterRef("attempt_id")).values(
                "graded_at"
            )[:1]
        )
    )


def separar_familia_e_estilo(apps, schema_editor):
    Campo = apps.get_model("certificates", "CertificateTemplateField")
    for pk, nome in Campo.objects.values_list("pk", "font_family"):
        familia, negrito, italico = DECOMPOSICAO.get(nome, ("Helvetica", False, False))
        Campo.objects.filter(pk=pk).update(
            font_family=familia, bold=negrito, italic=italico
        )


def recompor_nome_da_fonte(apps, schema_editor):
    Campo = apps.get_model("certificates", "CertificateTemplateField")
    for pk, familia, negrito, italico in Campo.objects.values_list(
        "pk", "font_family", "bold", "italic"
    ):
        nome = COMPOSICAO.get(
            (familia, bool(negrito), bool(italico)), "Helvetica"
        )
        Campo.objects.filter(pk=pk).update(font_family=nome)


class Migration(migrations.Migration):

    dependencies = [
        ("certificates", "0003_editor_de_modelos"),
        ("exams", "0009_gestao_operacional"),
    ]

    operations = [
        migrations.AddField(
            model_name="certificate",
            name="completed_at_snapshot",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="concluido em"
            ),
        ),
        migrations.RunPython(
            congelar_data_de_conclusao, migrations.RunPython.noop
        ),
        migrations.AddField(
            model_name="certificatetemplatefield",
            name="bold",
            field=models.BooleanField(default=False, verbose_name="negrito"),
        ),
        migrations.AddField(
            model_name="certificatetemplatefield",
            name="italic",
            field=models.BooleanField(default=False, verbose_name="italico"),
        ),
        migrations.AlterField(
            model_name="certificatetemplatefield",
            name="field_type",
            field=models.CharField(
                choices=[
                    ("STUDENT_NAME", "Nome do aluno"),
                    ("COMPLETION_DATE", "Data de conclusao"),
                    ("COURSE_NAME", "Nome do curso"),
                    ("MODULE_NAME", "Modulo"),
                    ("COURSE_DATES", "Data(s) do curso"),
                    ("COURSE_LOCATION", "Local"),
                    ("WORKLOAD", "Carga horaria"),
                    ("YEAR", "Ano"),
                    ("ISSUED_AT", "Data de emissao"),
                    ("INSTITUTION", "Instituicao"),
                    ("SIGNATORY_NAME", "Signatario"),
                    ("SIGNATORY_TITLE", "Cargo do signatario"),
                    ("VERIFICATION_CODE", "Codigo de validacao"),
                    ("QR_CODE", "QR Code"),
                    ("STATIC_IMAGE", "Imagem fixa"),
                ],
                max_length=24,
                verbose_name="campo",
            ),
        ),
        migrations.RunPython(separar_familia_e_estilo, recompor_nome_da_fonte),
        migrations.AlterField(
            model_name="certificatetemplatefield",
            name="font_family",
            field=models.CharField(
                choices=[
                    ("Helvetica", "Helvetica"),
                    ("Times", "Times"),
                    ("Courier", "Courier"),
                ],
                default="Helvetica",
                max_length=32,
                verbose_name="fonte",
            ),
        ),
        migrations.AddConstraint(
            model_name="certificatetemplatefield",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    font_family__in=("Helvetica", "Times", "Courier")
                ),
                name="campo_familia_de_fonte_conhecida",
            ),
        ),
    ]
