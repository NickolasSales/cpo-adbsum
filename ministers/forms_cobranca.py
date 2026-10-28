"""
Formulario da configuracao de pagamento (Fase 2D).

So declara os campos e limites de tamanho de entrada. As regras do Pix
(formato da chave por tipo, 25 caracteres do favorecido, 15 da cidade, o
payload que precisa fechar) ficam em ministers.cobranca, que normaliza e
devolve todos os problemas de uma vez.
"""

from django import forms

from ministers.models import PixKeyType

CLASSE = "form-control"


class ConfiguracaoPagamentoForm(forms.Form):
    pix_key_type = forms.ChoiceField(
        label="Tipo da chave",
        choices=[("", "Escolha...")] + list(PixKeyType.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    pix_key = forms.CharField(
        label="Chave Pix",
        max_length=120,
        widget=forms.TextInput(attrs={"class": CLASSE, "autocomplete": "off", "spellcheck": "false"}),
    )
    pix_receiver_name = forms.CharField(
        label="Favorecido",
        max_length=60,
        help_text="Até 25 caracteres. Acentos e sinais são removidos, como exige o Pix.",
        widget=forms.TextInput(attrs={"class": CLASSE, "autocomplete": "off"}),
    )
    pix_receiver_city = forms.CharField(
        label="Cidade",
        max_length=40,
        help_text="Até 15 caracteres.",
        widget=forms.TextInput(attrs={"class": CLASSE, "autocomplete": "off"}),
    )
    payment_instructions = forms.CharField(
        label="Orientações",
        required=False,
        max_length=600,
        help_text="Opcional, até 300 caracteres. Aparece na cobrança do portal e na guia em PDF.",
        widget=forms.Textarea(attrs={"class": CLASSE, "rows": 3}),
    )
    is_active = forms.BooleanField(
        label="Cobrança por Pix ativa",
        required=False,
        help_text="Desligada, nenhuma cobrança nova é gerada. As já geradas continuam valendo.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
