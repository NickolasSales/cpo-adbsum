"""
Formularios do Controle de Ministros.

Forms simples, e nao ModelForms, pela mesma razao das telas de alunos: o que
o formulario nao declara, o navegador nao consegue enviar. Hash de senha,
ultimo acesso, origem e situacao nao existem aqui, entao nem um POST forjado
os alcanca. A validacao de negocio (unicidade, formato da inscricao, forca
da senha) fica no servico, que e por onde toda escrita passa.
"""

from django import forms

from ministers.importers import EXTENSOES_ACEITAS, TAMANHO_MAXIMO_BYTES
from ministers.models import MinisterialTitle, MinisterSex

CLASSE = "form-control"


def campo_de_senha(rotulo, autocomplete="new-password", grande=False):
    """
    PasswordInput sem render_value: depois de um erro o campo volta vazio. A
    senha nunca e devolvida no HTML da resposta.
    """
    return forms.CharField(
        label=rotulo,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": CLASSE + (" form-control-lg" if grande else ""),
                "autocomplete": autocomplete,
            }
        ),
    )


def conferir_confirmacao(form, dados, primeiro="password1", segundo="password2"):
    if dados.get(primeiro) and dados.get(segundo) and dados[primeiro] != dados[segundo]:
        form.add_error(segundo, "As duas senhas não coincidem.")


class MinisterForm(forms.Form):
    """Cadastro e edicao. Na criacao, opcionalmente, o acesso ao portal."""

    full_name = forms.CharField(
        label="Nome completo",
        max_length=200,
        widget=forms.TextInput(attrs={"class": CLASSE, "autofocus": True}),
    )
    conamad_registration = forms.CharField(
        label="Inscrição CONAMAD",
        max_length=30,
        help_text="É também o login do ministro no portal.",
        widget=forms.TextInput(attrs={"class": CLASSE, "inputmode": "numeric"}),
    )
    field_registration = forms.CharField(
        label="Matrícula do campo",
        max_length=30,
        required=False,
        help_text="Opcional. Não consta na relação de 2026.",
        widget=forms.TextInput(attrs={"class": CLASSE, "inputmode": "numeric"}),
    )
    ministerial_title = forms.ChoiceField(
        label="Título ministerial",
        choices=[("", "Selecione")] + list(MinisterialTitle.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    position = forms.CharField(
        label="Cargo / função",
        max_length=100,
        required=False,
        help_text="Opcional. Ex.: Pastor Presidente. Vazio, o portal mostra o título.",
        widget=forms.TextInput(attrs={"class": CLASSE}),
    )
    congregation = forms.CharField(
        label="Congregação",
        max_length=120,
        required=False,
        widget=forms.TextInput(
            attrs={"class": CLASSE, "list": "congregacoes", "autocomplete": "off"}
        ),
    )
    consecration_year = forms.IntegerField(
        label="Ano de consagração",
        required=False,
        min_value=1900,
        max_value=2100,
        widget=forms.NumberInput(attrs={"class": CLASSE, "inputmode": "numeric"}),
    )
    birth_date = forms.DateField(
        label="Data de nascimento",
        required=False,
        widget=forms.DateInput(attrs={"class": CLASSE, "type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
    )
    sex = forms.ChoiceField(
        label="Sexo",
        required=False,
        choices=[("", "Não informado")] + list(MinisterSex.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    notes = forms.CharField(
        label="Observações",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": CLASSE,
                "rows": 3,
                "placeholder": "Uso administrativo. O ministro não vê este campo.",
            }
        ),
    )

    def __init__(self, *args, criando=False, **kwargs):
        """
        Os campos de acesso so existem na criacao. Na edicao a senha tem tela
        propria (resetar senha), que audita a operacao.
        """
        super().__init__(*args, **kwargs)
        self.criando = criando
        if criando:
            self.fields["create_access"] = forms.BooleanField(
                label="Criar acesso ao portal",
                required=False,
                initial=True,
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            )
            self.fields["password1"] = campo_de_senha("Senha inicial")
            self.fields["password1"].required = False
            self.fields["password2"] = campo_de_senha("Confirmar senha")
            self.fields["password2"].required = False

    def clean(self):
        dados = super().clean()
        if self.criando and dados.get("create_access"):
            if not dados.get("password1"):
                self.add_error("password1", "Informe a senha inicial.")
            if not dados.get("password2"):
                self.add_error("password2", "Confirme a senha.")
            conferir_confirmacao(self, dados)
        return dados

    def dados_ministeriais(self):
        campos = (
            "full_name",
            "conamad_registration",
            "field_registration",
            "ministerial_title",
            "position",
            "congregation",
            "consecration_year",
            "birth_date",
            "sex",
            "notes",
        )
        return {campo: self.cleaned_data.get(campo) for campo in campos}

    @property
    def senha(self):
        if self.criando and self.cleaned_data.get("create_access"):
            return self.cleaned_data.get("password1")
        return None


class DefinirSenhaForm(forms.Form):
    """Criar acesso ou resetar senha: o ADMIN digita duas vezes."""

    password1 = campo_de_senha("Nova senha")
    password2 = campo_de_senha("Confirmar nova senha")

    def clean(self):
        dados = super().clean()
        conferir_confirmacao(self, dados)
        return dados


class ImportUploadForm(forms.Form):
    arquivo = forms.FileField(
        label="Arquivo XLSX ou CSV",
        widget=forms.ClearableFileInput(attrs={"class": CLASSE, "accept": ".xlsx,.csv"}),
        help_text="A relação oficial pode ser enviada como está. Limite de {} MB.".format(
            TAMANHO_MAXIMO_BYTES // (1024 * 1024)
        ),
    )

    def clean_arquivo(self):
        arquivo = self.cleaned_data.get("arquivo")
        if arquivo is None:
            raise forms.ValidationError("Selecione um arquivo.")
        nome = (arquivo.name or "").lower()
        if not nome.endswith(EXTENSOES_ACEITAS):
            raise forms.ValidationError(
                "Formato não suportado. Envie um arquivo .xlsx ou .csv."
            )
        if arquivo.size > TAMANHO_MAXIMO_BYTES:
            raise forms.ValidationError(
                "O arquivo excede o limite de {} MB.".format(
                    TAMANHO_MAXIMO_BYTES // (1024 * 1024)
                )
            )
        if arquivo.size == 0:
            raise forms.ValidationError("O arquivo está vazio.")
        return arquivo


# ---------------------------------------------------------------------------
# Portal
# ---------------------------------------------------------------------------


class LoginMinistroForm(forms.Form):
    identificador = forms.CharField(
        label="Matrícula / Inscrição",
        max_length=60,
        widget=forms.TextInput(
            attrs={
                "class": CLASSE + " form-control-lg",
                "autofocus": True,
                "autocomplete": "username",
                "inputmode": "numeric",
                "autocapitalize": "none",
                "spellcheck": "false",
            }
        ),
    )
    senha = campo_de_senha("Senha", autocomplete="current-password", grande=True)


class TrocarSenhaMinistroForm(forms.Form):
    senha_atual = campo_de_senha("Senha atual", autocomplete="current-password", grande=True)
    nova_senha1 = campo_de_senha("Nova senha", grande=True)
    nova_senha2 = campo_de_senha("Confirmar nova senha", grande=True)

    def clean(self):
        dados = super().clean()
        conferir_confirmacao(self, dados, "nova_senha1", "nova_senha2")
        return dados
