"""
Formularios administrativos de provas e questoes.

Todos sao forms.Form, nunca ModelForm, e nenhum usa fields = "__all__".

O motivo e direto: o formulario nao pode ser um caminho de escrita. Quem
grava e exams.services. Um ModelForm com instance faz atribuicoes no objeto
durante a validacao, e bastaria alguem chamar save() por engano para uma
regra de dominio ser contornada. Com forms.Form o formulario devolve dados
limpos e nada mais.

Campos que jamais aparecem aqui, por decisao: status, total_points, version,
parent_exam, root_exam, created_by, published_at, closed_at e
access_password_hash. Nenhum deles pode ser decidido pelo navegador.
"""

from decimal import Decimal

from django import forms
from django.db.models import Q

from courses.models import Module
from exams.models import (
    MENSAGEM_REPROVACAO_PADRAO,
    QuestionType,
    TEXTO_FALSO,
    TEXTO_VERDADEIRO,
)

CLASSE_CAMPO = "form-control"
CLASSE_SELECT = "form-select"
CLASSE_CHECK = "form-check-input"

# datetime-local nao esta nos formatos de entrada do pt-br, entao precisa ser
# declarado no campo e no widget. Sem isso o navegador manda um valor que o
# Django recusa, e o administrador ve "informe uma data valida" ao salvar um
# formulario que ele preencheu pelo proprio seletor de data do navegador.
FORMATO_DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


def _widget_datetime():
    return forms.DateTimeInput(
        attrs={"class": CLASSE_CAMPO, "type": "datetime-local"},
        format=FORMATO_DATETIME_LOCAL,
    )


class ExamForm(forms.Form):
    """Criacao e edicao da configuracao de uma prova."""

    module = forms.ModelChoiceField(
        label="Modulo",
        queryset=Module.objects.none(),
        widget=forms.Select(attrs={"class": CLASSE_SELECT}),
        empty_label="Selecione o modulo",
    )
    title = forms.CharField(
        label="Titulo",
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": CLASSE_CAMPO,
                "placeholder": "Avaliacao Modulo 1",
                "autofocus": True,
            }
        ),
    )
    description = forms.CharField(
        label="Descricao",
        required=False,
        widget=forms.Textarea(attrs={"class": CLASSE_CAMPO, "rows": 2}),
    )
    instructions = forms.CharField(
        label="Instrucoes para o aluno",
        required=False,
        widget=forms.Textarea(attrs={"class": CLASSE_CAMPO, "rows": 3}),
    )

    open_at = forms.DateTimeField(
        label="Abertura",
        required=False,
        input_formats=[FORMATO_DATETIME_LOCAL],
        widget=_widget_datetime(),
        help_text="Obrigatoria para publicar.",
    )
    close_at = forms.DateTimeField(
        label="Encerramento",
        required=False,
        input_formats=[FORMATO_DATETIME_LOCAL],
        widget=_widget_datetime(),
        help_text="Obrigatorio para publicar.",
    )
    duration_minutes = forms.IntegerField(
        label="Duracao (minutos)",
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": CLASSE_CAMPO, "min": 1, "step": 1}),
        help_text="Obrigatoria para publicar.",
    )

    passing_score = forms.DecimalField(
        label="Nota minima",
        max_digits=4,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("10"),
        initial=Decimal("8.00"),
        widget=forms.NumberInput(
            attrs={"class": CLASSE_CAMPO, "min": 0, "max": 10, "step": "0.25"}
        ),
        help_text="Na escala de 0 a 10, independentemente do total de pontos.",
    )
    max_attempts = forms.IntegerField(
        label="Tentativas permitidas",
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={"class": CLASSE_CAMPO, "min": 1, "step": 1}),
    )
    failure_message = forms.CharField(
        label="Mensagem de reprovacao",
        required=False,
        initial=MENSAGEM_REPROVACAO_PADRAO,
        widget=forms.Textarea(attrs={"class": CLASSE_CAMPO, "rows": 3}),
    )

    randomize_questions = forms.BooleanField(
        label="Sortear a ordem das questoes",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": CLASSE_CHECK}),
    )
    randomize_options = forms.BooleanField(
        label="Sortear a ordem das alternativas",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": CLASSE_CHECK}),
    )
    show_score_after_submission = forms.BooleanField(
        label="Mostrar a nota logo apos o envio",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": CLASSE_CHECK}),
    )

    def __init__(self, *args, exam=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.exam = exam

        # Somente modulos ativos podem receber prova nova. Quando se edita uma
        # prova cujo modulo foi desativado depois, ele entra na lista para que
        # a edicao nao troque o vinculo em silencio; publicar continua barrado
        # pela validacao de publicacao.
        filtro = Q(is_active=True)
        if exam is not None and exam.module_id:
            filtro = filtro | Q(pk=exam.module_id)
        self.fields["module"].queryset = Module.objects.filter(filtro).order_by(
            "order", "name"
        )

    def clean_title(self):
        titulo = (self.cleaned_data.get("title") or "").strip()
        if not titulo:
            raise forms.ValidationError("O titulo da prova e obrigatorio.")
        return titulo

    def clean(self):
        dados = super().clean()
        abertura = dados.get("open_at")
        encerramento = dados.get("close_at")
        if abertura and encerramento and abertura >= encerramento:
            self.add_error(
                "close_at", "O encerramento precisa ser posterior a abertura."
            )
        return dados


class ExamPasswordForm(forms.Form):
    """
    Define ou troca a senha de acesso da prova.

    PasswordInput sem valor inicial: a senha atual nao existe em lugar nenhum
    para ser reexibida — o banco guarda apenas o hash — e o campo tambem nao
    e preenchido com o hash, que nao e senha e nao serve para nada na tela.
    """

    senha = forms.CharField(
        label="Nova senha da prova",
        min_length=4,
        max_length=128,
        strip=True,
        widget=forms.PasswordInput(
            attrs={"class": CLASSE_CAMPO, "autocomplete": "new-password"}
        ),
        help_text="Minimo de 4 caracteres. Nao sera exibida novamente.",
    )
    confirmacao = forms.CharField(
        label="Confirme a senha",
        max_length=128,
        strip=True,
        widget=forms.PasswordInput(
            attrs={"class": CLASSE_CAMPO, "autocomplete": "new-password"}
        ),
    )

    def clean(self):
        dados = super().clean()
        if dados.get("senha") and dados.get("senha") != dados.get("confirmacao"):
            self.add_error("confirmacao", "As duas senhas nao coincidem.")
        return dados


class QuestionForm(forms.Form):
    """
    Criacao e edicao de questao.

    A interface esconde campos conforme o tipo escolhido, mas quem decide o
    que e valido e o backend: exams.services.validation roda sobre o que foi
    gravado, nao sobre o que o navegador mandou.
    """

    type = forms.ChoiceField(
        label="Tipo",
        choices=QuestionType.choices,
        widget=forms.Select(attrs={"class": CLASSE_SELECT, "id": "id_type"}),
    )
    text = forms.CharField(
        label="Enunciado",
        widget=forms.Textarea(
            attrs={"class": CLASSE_CAMPO, "rows": 3, "autofocus": True}
        ),
    )
    points = forms.DecimalField(
        label="Valor",
        max_digits=6,
        decimal_places=2,
        min_value=Decimal("0.01"),
        initial=Decimal("1.00"),
        widget=forms.NumberInput(
            attrs={"class": CLASSE_CAMPO, "min": "0.01", "step": "0.25"}
        ),
        help_text="Precisa ser maior que zero.",
    )
    order = forms.IntegerField(
        label="Ordem",
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": CLASSE_CAMPO, "min": 0, "step": 1}),
        help_text="Deixe em branco para colocar no fim.",
    )
    required = forms.BooleanField(
        label="Obrigatoria",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": CLASSE_CHECK}),
    )
    active = forms.BooleanField(
        label="Ativa",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": CLASSE_CHECK}),
    )
    internal_explanation = forms.CharField(
        label="Explicacao interna",
        required=False,
        widget=forms.Textarea(attrs={"class": CLASSE_CAMPO, "rows": 2}),
        help_text="Visivel somente para a equipe. Nunca chega ao aluno.",
    )

    # Sem class="form-check-input" aqui, e o motivo importa.
    #
    # Essa classe do Bootstrap carrega margin-left: -1.5em, pensada para
    # cancelar o padding-left: 1.5em que o container .form-check fornece. O
    # RadioSelect do Django nao gera esse container: ele gera <div><label>
    # <input> Texto</label></div>. Sem o pai, a margem negativa puxava cada
    # radio 1.5em para fora da propria caixa — o circulo saia do lugar, o
    # rotulo escorregava por cima do anterior e o texto de ajuda atravessava
    # os campos. Era esse o bug relatado.
    #
    # A correcao nao e trocar a classe por outra: e nao usar a renderizacao
    # generica. O template desenha as duas opcoes com .cpo-vf, que e feito
    # para exatamente duas escolhas de texto fixo.
    resposta_verdadeira = forms.ChoiceField(
        label="Resposta correta",
        required=False,
        choices=[("true", TEXTO_VERDADEIRO), ("false", TEXTO_FALSO)],
        widget=forms.RadioSelect,
    )

    def clean(self):
        dados = super().clean()
        if dados.get("type") == QuestionType.TRUE_FALSE and not dados.get(
            "resposta_verdadeira"
        ):
            self.add_error(
                "resposta_verdadeira",
                "Escolha se a resposta correta e Verdadeiro ou Falso.",
            )
        return dados

    @property
    def resposta_verdadeira_bool(self):
        valor = self.cleaned_data.get("resposta_verdadeira")
        if valor == "true":
            return True
        if valor == "false":
            return False
        return None


class QuestionOptionForm(forms.Form):
    """Uma alternativa. Linhas em branco sao descartadas pelo servico."""

    text = forms.CharField(
        label="Alternativa",
        required=False,
        max_length=300,
        widget=forms.TextInput(
            attrs={"class": CLASSE_CAMPO, "placeholder": "Texto da alternativa"}
        ),
    )
    is_correct = forms.BooleanField(
        label="Correta",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": CLASSE_CHECK}),
    )


QuestionOptionFormSet = forms.formset_factory(
    QuestionOptionForm, extra=5, max_num=20, validate_max=True, can_delete=False
)
