"""Formularios administrativos de modulos e matriculas."""

from django import forms

from accounts.models import User, UserRole
from courses.models import Enrollment, Module

CLASSE_CAMPO = "form-control"
CLASSE_SELECT = "form-select"


class ModuleForm(forms.ModelForm):
    """
    Criacao e edicao de modulo.

    Os campos sao declarados um a um, nunca com fields = "__all__": uma
    listagem explicita garante que um campo novo no modelo nao vire
    automaticamente editavel pela web sem ninguem decidir isso.
    """

    class Meta:
        model = Module
        fields = ["name", "code", "description", "order", "is_active"]
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
        }

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
