"""
Formularios dos comprovantes (Fase 2E).

O envio declara UM campo: o arquivo. Valor, data do pagamento, situacao,
ministro e mensalidade nao existem no formulario, entao um POST forjado nao
os alcanca — a mensalidade vem da URL (competencia) filtrada pelo ministro
da sessao. A conferencia do arquivo fica em ministers.arquivo_comprovante.
"""

from django import forms

from common.texto import LIMITE_DO_MOTIVO
from ministers.forms_mensalidades import CLASSE, campo_de_data, campo_de_motivo

ACEITOS_NO_NAVEGADOR = "application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png"


class EnviarComprovanteForm(forms.Form):
    arquivo = forms.FileField(
        label="Comprovante",
        error_messages={
            "required": "Selecione o comprovante.",
            "empty": "O arquivo enviado está vazio.",
            "invalid": "Selecione o comprovante.",
        },
        # O template desenha o input (botao grande "Selecionar comprovante");
        # accept e o limite vao para o JS avisar antes de enviar. O servidor
        # confere tudo de novo.
        widget=forms.FileInput(attrs={"accept": ACEITOS_NO_NAVEGADOR}),
    )


class AprovarComprovanteForm(forms.Form):
    paid_at = campo_de_data("Data do pagamento")


class RecusarComprovanteForm(forms.Form):
    motivo = campo_de_motivo("Motivo da recusa")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        campo = self.fields["motivo"]
        campo.max_length = LIMITE_DO_MOTIVO
        campo.widget.attrs.update(
            {
                "class": CLASSE,
                "rows": 3,
                "maxlength": str(LIMITE_DO_MOTIVO),
                "placeholder": "Ex.: Imagem ilegível.",
            }
        )
