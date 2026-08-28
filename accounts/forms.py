"""Formularios de autenticacao."""

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

from accounts.managers import normalizar_email

MENSAGEM_CREDENCIAL_INVALIDA = "E-mail ou senha invalidos."


class EmailAuthenticationForm(AuthenticationForm):
    """
    Login por e-mail.

    O campo mantem o nome "username" porque e o que AuthenticationForm.clean
    espera; apenas o rotulo e o tipo mudam. Assim herdamos de graca toda a
    logica testada do Django, inclusive a comparacao de senha em tempo
    constante quando o usuario nao existe.
    """

    username = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(
            attrs={
                "autofocus": True,
                "autocomplete": "email",
                "class": "form-control form-control-lg",
                "placeholder": "seu@email.com",
                "inputmode": "email",
            }
        ),
    )
    password = forms.CharField(
        label="Senha",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "class": "form-control form-control-lg",
                "placeholder": "Sua senha",
            }
        ),
    )

    # Mensagem unica e deliberadamente vaga. Distinguir "e-mail nao existe" de
    # "senha errada" de "conta bloqueada" entregaria a um atacante a
    # confirmacao de quais e-mails estao cadastrados.
    error_messages = {
        "invalid_login": MENSAGEM_CREDENCIAL_INVALIDA,
        "inactive": MENSAGEM_CREDENCIAL_INVALIDA,
    }

    def clean_username(self):
        return normalizar_email(self.cleaned_data.get("username"))

    def confirm_login_allowed(self, user):
        """
        Rede de seguranca.

        O ModelBackend ja recusa usuarios inativos antes de chegar aqui, mas
        deixamos a checagem explicita para que a regra sobreviva caso outro
        backend de autenticacao seja adicionado no futuro.
        """
        if not user.is_active:
            raise forms.ValidationError(
                self.error_messages["inactive"], code="inactive"
            )


class TrocarSenhaForm(PasswordChangeForm):
    """
    Troca de senha do usuario logado.

    Exige a senha atual de proposito: sem isso, uma sessao deixada aberta em
    um computador compartilhado permitiria a qualquer pessoa assumir a conta
    trocando a senha. O aluno acabou de digitar a senha atual no login, entao
    o custo para ele e proximo de zero.

    A validacao de forca vem dos AUTH_PASSWORD_VALIDATORS e o hashing do
    set_password, ambos herdados do Django.
    """

    error_messages = {
        **PasswordChangeForm.error_messages,
        "password_incorrect": "A senha atual esta incorreta.",
        "password_mismatch": "As duas senhas nao coincidem.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        rotulos = {
            "old_password": ("Senha atual", "current-password"),
            "new_password1": ("Nova senha", "new-password"),
            "new_password2": ("Confirme a nova senha", "new-password"),
        }
        for nome, (rotulo, autocomplete) in rotulos.items():
            campo = self.fields[nome]
            campo.label = rotulo
            campo.widget.attrs.update(
                {"class": "form-control form-control-lg", "autocomplete": autocomplete}
            )
