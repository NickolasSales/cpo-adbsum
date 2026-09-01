"""
Formularios de contas administrativas.

O que estes formularios NAO tem
-------------------------------
Campo de papel, de is_staff, de is_superuser, de is_active ou de
must_change_password. Nao e questao de esconder: eles simplesmente nao
existem, entao um POST forjado com `is_superuser=true` chega a uma view que
nunca le esse nome e a um servico que grava o valor literal `False`.

Um formulario que declarasse esses campos e depois os removesse no clean()
seria uma defesa que depende de ninguem mexer no clean(). Nao declarar e uma
defesa que depende de ninguem ADICIONAR o campo — bem mais dificil de fazer
por descuido.
"""

from django import forms

CLASSE_CAMPO = "form-control"


def campo_de_senha(rotulo):
    """
    Campo de senha administrativo.

    PasswordInput sem render_value: depois de um erro de validacao o campo
    volta VAZIO. Reexibir o valor colocaria a senha no HTML da resposta, que
    passa por proxy, cache do navegador e historico de navegacao.
    """
    return forms.CharField(
        label=rotulo,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": CLASSE_CAMPO, "autocomplete": "new-password"}
        ),
    )


def conferir_confirmacao(form, dados):
    senha = dados.get("password1")
    confirmacao = dados.get("password2")
    if senha and confirmacao and senha != confirmacao:
        form.add_error("password2", "As senhas nao conferem.")


class AdminUserForm(forms.Form):
    """
    Criacao e edicao de administrador.

    Os campos de senha existem apenas na criacao. Na edicao, trocar a senha e
    outra tela, com auditoria propria: misturar as duas coisas faria um
    administrador que so queria corrigir um nome invalidar sem querer as
    sessoes do colega.
    """

    full_name = forms.CharField(
        label="Nome completo",
        max_length=150,
        widget=forms.TextInput(
            attrs={"class": CLASSE_CAMPO, "autocomplete": "name"}
        ),
    )
    email = forms.EmailField(
        label="E-mail",
        max_length=254,
        widget=forms.EmailInput(
            attrs={"class": CLASSE_CAMPO, "autocomplete": "email"}
        ),
    )

    def __init__(self, *args, criando=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.criando = criando
        if criando:
            self.fields["password1"] = campo_de_senha("Senha")
            self.fields["password2"] = campo_de_senha("Confirmar senha")

    def clean(self):
        dados = super().clean()
        if self.criando:
            conferir_confirmacao(self, dados)
        return dados

    @property
    def senha(self):
        """A senha escolhida, ou None quando o formulario e de edicao."""
        return self.cleaned_data.get("password1") if self.criando else None


class ResetAdminPasswordForm(forms.Form):
    """
    Redefinicao da senha de outro administrador.

    Nao pede a senha atual: quem executa nao a conhece, e nao deve conhecer.
    """

    password1 = campo_de_senha("Nova senha")
    password2 = campo_de_senha("Confirmar nova senha")

    def clean(self):
        dados = super().clean()
        conferir_confirmacao(self, dados)
        return dados
