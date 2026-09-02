"""Formularios administrativos de modulos e matriculas."""

from django import forms

from accounts.models import User, UserRole
from courses.models import (
    ANO_MAXIMO_DO_CERTIFICADO,
    ANO_MINIMO_DO_CERTIFICADO,
    Enrollment,
    Module,
)

CLASSE_CAMPO = "form-control"
CLASSE_SELECT = "form-select"

# Campos que so existem por causa do certificado. A lista e usada pelo
# formulario, pela view e pelo servico, para que os tres nunca discordem sobre
# o que compoe a secao "Dados do certificado".
CAMPOS_DO_CERTIFICADO = (
    "certificate_display_name",
    "certificate_course_dates_text",
    "certificate_location",
    "certificate_workload_hours",
    "certificate_year",
)


class ModuleForm(forms.ModelForm):
    """
    Criacao e edicao de modulo.

    Os campos sao declarados um a um, nunca com fields = "__all__": uma
    listagem explicita garante que um campo novo no modelo nao vire
    automaticamente editavel pela web sem ninguem decidir isso.
    """

    class Meta:
        model = Module
        fields = [
            "name",
            "code",
            "description",
            "order",
            "is_active",
            *CAMPOS_DO_CERTIFICADO,
            "certificate_template",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": CLASSE_CAMPO, "placeholder": "Modulo 1", "autofocus": True}
            ),
            "code": forms.TextInput(
                attrs={"class": CLASSE_CAMPO, "placeholder": "MOD1"}
            ),
            "description": forms.Textarea(attrs={"class": CLASSE_CAMPO, "rows": 3}),
            "order": forms.NumberInput(attrs={"class": CLASSE_CAMPO, "min": 0}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "certificate_display_name": forms.TextInput(
                attrs={
                    "class": CLASSE_CAMPO,
                    "placeholder": "Modulo I - Cooperadores e Diaconos",
                }
            ),
            "certificate_course_dates_text": forms.TextInput(
                attrs={
                    "class": CLASSE_CAMPO,
                    "placeholder": "10 e 17 de outubro de 2026",
                }
            ),
            "certificate_location": forms.TextInput(
                attrs={"class": CLASSE_CAMPO, "placeholder": "Igreja Sede"}
            ),
            # Os atributos min e max ajudam quem digita, e o navegador os
            # respeita. Eles nao sao a validacao: ela esta no clean abaixo, nos
            # validators do modelo e numa CheckConstraint. Um POST direto nao
            # ve nenhum atributo de HTML.
            "certificate_workload_hours": forms.NumberInput(
                attrs={"class": CLASSE_CAMPO, "min": 1, "placeholder": "8"}
            ),
            "certificate_year": forms.NumberInput(
                attrs={
                    "class": CLASSE_CAMPO,
                    "min": ANO_MINIMO_DO_CERTIFICADO,
                    "max": ANO_MAXIMO_DO_CERTIFICADO,
                    "placeholder": "2026",
                }
            ),
            "certificate_template": forms.Select(attrs={"class": CLASSE_CAMPO}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Somente modelos ATIVOS aparecem no select.
        #
        # Um rascunho ainda esta sendo montado e pode nem ter arte; um
        # arquivado foi aposentado de proposito. Oferecer os dois transformaria
        # a escolha do modelo num campo onde e possivel selecionar algo que a
        # emissao vai recusar depois, no pior momento possivel.
        #
        # A consulta e feita aqui, e nao no modelo, porque limit_choices_to e
        # avaliado uma vez na definicao da classe: um modelo ativado depois de
        # o processo subir nao apareceria.
        from certificates.models import CertificateTemplate, TemplateStatus

        campo = self.fields["certificate_template"]
        campo.queryset = CertificateTemplate.objects.filter(
            status=TemplateStatus.ACTIVE
        ).order_by("name", "-version")
        campo.required = False
        campo.empty_label = "Usar o modelo padrao"

    def clean_code(self):
        codigo = (self.cleaned_data.get("code") or "").strip().upper()
        if not codigo:
            raise forms.ValidationError("O codigo do modulo e obrigatorio.")
        return codigo

    def clean_order(self):
        ordem = self.cleaned_data.get("order")
        if ordem is None:
            return 0
        if ordem < 0:
            raise forms.ValidationError("A ordem nao pode ser negativa.")
        return ordem

    def clean_certificate_workload_hours(self):
        horas = self.cleaned_data.get("certificate_workload_hours")
        if horas is None:
            return None
        if horas < 1:
            raise forms.ValidationError(
                "A carga horaria precisa ser maior que zero."
            )
        return horas

    def clean_certificate_year(self):
        ano = self.cleaned_data.get("certificate_year")
        if ano is None:
            return None
        if not ANO_MINIMO_DO_CERTIFICADO <= ano <= ANO_MAXIMO_DO_CERTIFICADO:
            raise forms.ValidationError(
                "Informe um ano entre {} e {}.".format(
                    ANO_MINIMO_DO_CERTIFICADO, ANO_MAXIMO_DO_CERTIFICADO
                )
            )
        return ano


class EnrollmentForm(forms.Form):
    """
    Matricula de um aluno em um modulo.

    O queryset de alunos ja exclui administradores, de modo que a lista nem
    oferece a opcao invalida. A garantia real continua na camada de servico:
    um POST forjado com o id de um administrador e recusado la.
    """

    student = forms.ModelChoiceField(
        label="Aluno",
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": CLASSE_SELECT}),
        empty_label="Selecione o aluno",
    )
    module = forms.ModelChoiceField(
        label="Modulo",
        queryset=Module.objects.none(),
        widget=forms.Select(attrs={"class": CLASSE_SELECT}),
        empty_label="Selecione o modulo",
    )
    notes = forms.CharField(
        label="Observacoes",
        required=False,
        widget=forms.Textarea(attrs={"class": CLASSE_CAMPO, "rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = User.objects.filter(
            role=UserRole.STUDENT
        ).order_by("full_name")
        # Somente modulos ativos aceitam nova matricula.
        self.fields["module"].queryset = Module.objects.filter(
            is_active=True
        ).order_by("order", "name")
