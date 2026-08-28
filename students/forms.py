"""Formularios administrativos de alunos."""

from django import forms

from students.importers import EXTENSOES_ACEITAS, TAMANHO_MAXIMO_BYTES

CLASSE_CAMPO = "form-control"


class StudentForm(forms.Form):
    """
    Criacao e edicao de aluno.

    Nao e um ModelForm de propósito. O aluno e composto por dois modelos
    (User e StudentProfile) e a montagem cabe ao servico. Alem disso, um Form
    simples nao tem como expor acidentalmente role, is_staff ou is_superuser:
    os campos que existem sao exatamente estes tres.

    A validacao de unicidade de e-mail e das regras de negocio fica no
    servico, que e o unico ponto por onde toda criacao passa.
    """

    full_name = forms.CharField(
        label="Nome completo",
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": CLASSE_CAMPO,
                "placeholder": "Joao da Silva",
                "autofocus": True,
            }
        ),
    )
    email = forms.EmailField(
        label="E-mail",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "class": CLASSE_CAMPO,
                "placeholder": "joao@exemplo.com",
                "inputmode": "email",
            }
        ),
    )
    notes = forms.CharField(
        label="Observacoes",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": CLASSE_CAMPO,
                "rows": 3,
                "placeholder": "Uso administrativo. O aluno nao ve este campo.",
            }
        ),
    )

    def clean_full_name(self):
        nome = (self.cleaned_data.get("full_name") or "").strip()
        if not nome:
            raise forms.ValidationError("O nome completo e obrigatorio.")
        return nome


class ImportUploadForm(forms.Form):
    """Upload da planilha de alunos."""

    arquivo = forms.FileField(
        label="Arquivo CSV ou XLSX",
        widget=forms.ClearableFileInput(
            attrs={"class": CLASSE_CAMPO, "accept": ".csv,.xlsx"}
        ),
        help_text="Colunas obrigatorias: nome, email, modulo. Limite de {} MB.".format(
            TAMANHO_MAXIMO_BYTES // (1024 * 1024)
        ),
    )

    def clean_arquivo(self):
        """
        Primeira barreira do upload.

        O atributo accept do input e apenas conveniencia de interface e nao
        vale como controle. A extensao e o tamanho sao conferidos aqui, e a
        estrutura real do arquivo e conferida na leitura.
        """
        arquivo = self.cleaned_data.get("arquivo")
        if arquivo is None:
            raise forms.ValidationError("Selecione um arquivo.")

        nome = (arquivo.name or "").lower()
        if not nome.endswith(EXTENSOES_ACEITAS):
            raise forms.ValidationError(
                "Formato nao suportado. Envie um arquivo .csv ou .xlsx."
            )
        if arquivo.size > TAMANHO_MAXIMO_BYTES:
            raise forms.ValidationError(
                "O arquivo excede o limite de {} MB.".format(
                    TAMANHO_MAXIMO_BYTES // (1024 * 1024)
                )
            )
        if arquivo.size == 0:
            raise forms.ValidationError("O arquivo esta vazio.")
        return arquivo
