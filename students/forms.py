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

    def __init__(self, *args, criando=False, **kwargs):
        """
        Os campos de senha existem apenas na criacao.

        Na edicao eles nem sao construidos, em vez de ficarem opcionais: um
        campo de senha vazio num formulario de edicao e um convite a apagar a
        senha por engano, e a troca de senha tem tela propria — a de reset,
        que audita a operacao. Aqui os campos simplesmente nao existem, entao
        nem um POST forjado alcanca esse caminho.
        """
        super().__init__(*args, **kwargs)
        self.criando = criando
        if criando:
            self.fields["password1"] = campo_de_senha("Senha")
            self.fields["password2"] = campo_de_senha("Confirmar senha")

    def clean_full_name(self):
        nome = (self.cleaned_data.get("full_name") or "").strip()
        if not nome:
            raise forms.ValidationError("O nome completo e obrigatorio.")
        return nome

    def clean(self):
        dados = super().clean()
        if self.criando:
            conferir_confirmacao(self, dados)
        return dados

    @property
    def senha(self):
        """A senha digitada, ou None na edicao."""
        return self.cleaned_data.get("password1") if self.criando else None


def campo_de_senha(rotulo):
    """
    Campo de senha administrativo.

    PasswordInput sem render_value: depois de um erro de validacao o campo
    volta VAZIO. Reexibir o valor colocaria a senha no HTML de resposta, que
    passa por proxy, cache do navegador e historico.
    """
    return forms.CharField(
        label=rotulo,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": CLASSE_CAMPO,
                "autocomplete": "new-password",
            }
        ),
    )


def conferir_confirmacao(form, dados):
    """
    Exige que as duas senhas coincidam.

    A validacao de forca fica no servico, que e por onde toda criacao passa —
    inclusive a importacao em lote. Repeti-la aqui criaria duas listas de
    regras que um dia divergiriam.

    A mensagem de erro nunca cita nenhuma das duas senhas.
    """
    senha1 = dados.get("password1")
    senha2 = dados.get("password2")
    if senha1 and senha2 and senha1 != senha2:
        form.add_error("password2", "As duas senhas nao coincidem.")


class ResetPasswordForm(forms.Form):
    """
    Redefinicao da senha de um aluno pelo administrador.

    Nao pede a senha atual: o administrador nao a conhece e nao deve conhecer.
    A tela tambem nunca exibe a senha vigente — ela so existe como hash.
    """

    password1 = campo_de_senha("Nova senha")
    password2 = campo_de_senha("Confirmar nova senha")

    def clean(self):
        dados = super().clean()
        conferir_confirmacao(self, dados)
        return dados


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
