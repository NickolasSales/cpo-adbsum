"""
Formularios das mensalidades.

Como nos demais formularios do app: Forms simples, que declaram exatamente
os campos aceitos. Situacao, ministro de uma mensalidade existente e dados de
pagamento de quem nao pagou nao aparecem onde nao devem, entao um POST
forjado nao os alcanca. As regras (unicidade, transicoes, intervalos) ficam
em ministers.mensalidades.
"""

from decimal import Decimal

from django import forms

from common.exceptions import DomainError
from common.texto import LIMITE_DO_MOTIVO
from ministers.mensalidades import TAMANHO_REFERENCIA, competencia_de_texto
from ministers.models import Minister, PaymentMethod

CLASSE = "form-control"


def campo_de_valor():
    # localize: aceita "35,00", que e como se digita dinheiro no Brasil.
    return forms.DecimalField(
        label="Valor (R$)",
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        localize=True,
        widget=forms.TextInput(
            attrs={"class": CLASSE, "inputmode": "decimal", "placeholder": "35,00"}
        ),
    )


def campo_de_data(rotulo, required=True):
    return forms.DateField(
        label=rotulo,
        required=required,
        widget=forms.DateInput(attrs={"class": CLASSE, "type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
    )


def campo_de_observacoes(rotulo="Observações"):
    return forms.CharField(
        label=rotulo,
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"class": CLASSE, "rows": 3}),
    )


def campo_de_motivo(rotulo="Motivo"):
    return forms.CharField(
        label=rotulo,
        max_length=LIMITE_DO_MOTIVO,
        widget=forms.Textarea(
            attrs={
                "class": CLASSE,
                "rows": 3,
                "placeholder": "Por que esta alteração está sendo feita?",
            }
        ),
    )


class RotuloDoMinistro(forms.ModelChoiceField):
    def label_from_instance(self, ministro):
        rotulo = "{} — {}".format(ministro.full_name, ministro.conamad_registration)
        return rotulo if ministro.is_active else rotulo + " (inativo)"


class MensalidadeForm(forms.Form):
    """Cadastro manual."""

    minister = RotuloDoMinistro(
        label="Ministro",
        queryset=Minister.objects.order_by("-is_active", "full_name"),
        empty_label="Selecione",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    reference_month = forms.CharField(
        label="Competência",
        widget=forms.TextInput(attrs={"class": CLASSE, "type": "month", "placeholder": "AAAA-MM"}),
        help_text="Mês e ano a que a mensalidade se refere.",
    )
    due_date = campo_de_data("Vencimento")
    amount = campo_de_valor()
    notes = campo_de_observacoes()
    confirmar_inativo = forms.BooleanField(
        label="Confirmo a mensalidade para ministro inativo",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean_reference_month(self):
        try:
            return competencia_de_texto(self.cleaned_data.get("reference_month"))
        except DomainError as erro:
            raise forms.ValidationError(str(erro))


class MensalidadeEdicaoForm(forms.Form):
    """Edicao de mensalidade pendente: ministro e competencia nao mudam."""

    due_date = campo_de_data("Vencimento")
    amount = campo_de_valor()
    notes = campo_de_observacoes()


def campo_de_forma():
    return forms.ChoiceField(
        label="Forma de pagamento",
        choices=[("", "Selecione")] + list(PaymentMethod.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )


def campo_de_referencia():
    return forms.CharField(
        label="Referência / transação",
        required=False,
        max_length=TAMANHO_REFERENCIA,
        help_text="Opcional. Ex.: identificador do Pix ou nº do recibo.",
        widget=forms.TextInput(attrs={"class": CLASSE, "autocomplete": "off"}),
    )


class PagamentoForm(forms.Form):
    paid_at = campo_de_data("Data do pagamento")
    payment_method = campo_de_forma()
    payment_reference = campo_de_referencia()
    notes = campo_de_observacoes("Observação")


class CorrecaoPagamentoForm(forms.Form):
    paid_at = campo_de_data("Data do pagamento")
    payment_method = campo_de_forma()
    payment_reference = campo_de_referencia()
    reason = campo_de_motivo("Motivo da correção")


class MotivoForm(forms.Form):
    reason = campo_de_motivo()


class GeracaoForm(forms.Form):
    """Parametros da geracao em massa. Nenhum valor ou vencimento padrao."""

    reference_month = forms.CharField(
        label="Competência",
        widget=forms.TextInput(attrs={"class": CLASSE, "type": "month", "placeholder": "AAAA-MM"}),
    )
    due_date = campo_de_data("Vencimento")
    amount = campo_de_valor()

    def clean_reference_month(self):
        try:
            return competencia_de_texto(self.cleaned_data.get("reference_month"))
        except DomainError as erro:
            raise forms.ValidationError(str(erro))
