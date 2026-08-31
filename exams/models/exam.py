"""
Provas, questoes e alternativas: o que o administrador monta.

Tres modelos que sempre andam juntos. O que e regra de dominio — publicar,
fechar, duplicar, validar gabarito — nao esta aqui: vive em exams.services, e
este modulo guarda apenas a forma dos dados e as garantias que o banco
consegue impor sozinho.

O lado do aluno fica em attempt.py: uma tentativa referencia estes modelos
sem copia-los, porque a prova publicada e imutavel.

Um cuidado atravessa o arquivo inteiro: QuestionOption.is_correct e a
resposta certa. Nenhum caminho que termine no navegador do aluno pode passar
por este campo. A defesa esta em exams.selectors, que monta estruturas onde o
campo simplesmente nao existe.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum

MENSAGEM_REPROVACAO_PADRAO = (
    "Infelizmente voce nao atingiu a nota minima necessaria para aprovacao.\n\n"
    "Procure a coordenacao para mais informacoes."
)

# Textos fixos das alternativas de uma questao Verdadeiro ou Falso. Sao
# constantes, e nao texto administravel, porque a tela do aluno precisa
# renderizar esse tipo de questao sempre da mesma forma. Deixar o
# administrador trocar por "Opcao A" e "Opcao B" quebraria essa promessa.
TEXTO_VERDADEIRO = "Verdadeiro"
TEXTO_FALSO = "Falso"

NOTA_MAXIMA = Decimal("10.00")


class ExamStatus(models.TextChoices):
    """
    Ciclo de vida da prova.

    DRAFT -> PUBLISHED -> CLOSED, sempre nessa direcao. A transicao nunca
    acontece por atribuicao direta: quem muda status e exams.services, que
    valida antes. Por isso status nunca aparece como campo de formulario.
    """

    DRAFT = "DRAFT", "Rascunho"
    PUBLISHED = "PUBLISHED", "Publicada"
    CLOSED = "CLOSED", "Fechada"


class QuestionType(models.TextChoices):
    """
    Tipos de questao.

    O codigo gravado no banco fica em ingles, como o resto do schema; o
    rotulo exibido fica em portugues.
    """

    SINGLE_CHOICE = "SINGLE_CHOICE", "Escolha unica"
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE", "Multiplas respostas"
    TRUE_FALSE = "TRUE_FALSE", "Verdadeiro ou falso"
    SHORT_TEXT = "SHORT_TEXT", "Resposta curta"
    ESSAY = "ESSAY", "Dissertativa"


# Tipos que usam alternativas. Um tipo fora desta lista com QuestionOption
# vinculada e estrutura invalida, e a publicacao recusa.
TIPOS_COM_ALTERNATIVAS = frozenset(
    {
        QuestionType.SINGLE_CHOICE,
        QuestionType.MULTIPLE_CHOICE,
        QuestionType.TRUE_FALSE,
    }
)

# Tipos que so podem ser corrigidos por uma pessoa. Nesta etapa nao existe
# resposta esperada cadastravel para eles, de proposito: um campo de
# "resposta correta" em texto livre daria a falsa impressao de correcao
# automatica confiavel.
TIPOS_DE_CORRECAO_MANUAL = frozenset(
    {
        QuestionType.SHORT_TEXT,
        QuestionType.ESSAY,
    }
)


class ExamQuerySet(models.QuerySet):
    def publicadas(self):
        return self.filter(status=ExamStatus.PUBLISHED)

    def rascunhos(self):
        return self.filter(status=ExamStatus.DRAFT)

    def da_linhagem_de(self, exam):
        """
        Todas as versoes que pertencem a mesma linhagem da prova indicada.

        Ver a docstring de Exam para o desenho de root_exam e parent_exam.
        """
        raiz_id = exam.root_exam_id or exam.pk
        return self.filter(Q(pk=raiz_id) | Q(root_exam_id=raiz_id))


class Exam(models.Model):
    """
    Uma prova, em uma versao especifica.

    Versionamento
    -------------
    Duas referencias distintas, com papeis diferentes:

        parent_exam   de qual prova esta foi duplicada. Procedencia.
        root_exam     a raiz da linhagem. Identidade do conjunto de versoes.

    A raiz tem root_exam nulo e version=1. Toda copia aponta root_exam para a
    mesma raiz e recebe version = maior versao da linhagem + 1. Duplicar a v1
    quando ja existem v2 e v3 produz a v4, nunca uma segunda v2.

    Guardar a raiz explicitamente, em vez de subir a cadeia de parent_exam a
    cada consulta, e o que permite duas coisas: listar a linhagem inteira com
    uma consulta, e deixar o banco impedir versoes repetidas por constraint
    unica em (root_exam, version). Subir a cadeia daria O(profundidade)
    consultas e nao seria expressavel como constraint.

    O banco tambem recusa linhas incoerentes por si so: raiz e versao andam
    juntas (exam_raiz_e_versao_coerentes) e raiz e origem existem ou faltam
    juntas (exam_linhagem_parent_coerente). O que fica fora do alcance de uma
    CheckConstraint e a relacao entre linhas — que parent.root_exam seja esta
    mesma raiz —, e isso continua sendo garantido por duplicate_exam.

    Imutabilidade
    -------------
    Fora de DRAFT a estrutura e congelada: questoes, alternativas, gabarito,
    pontuacao e modulo. Precisa mudar? Duplica-se a prova. O bloqueio vive na
    camada de servico, nao no template: esconder o botao nao e protecao.
    """

    module = models.ForeignKey(
        "courses.Module",
        verbose_name="modulo",
        on_delete=models.PROTECT,
        related_name="exams",
    )

    title = models.CharField("titulo", max_length=200)
    description = models.TextField("descricao", blank=True)
    instructions = models.TextField(
        "instrucoes",
        blank=True,
        help_text="Texto exibido ao aluno antes de iniciar a prova.",
    )

    status = models.CharField(
        "situacao",
        max_length=12,
        choices=ExamStatus.choices,
        default=ExamStatus.DRAFT,
        db_index=True,
    )

    open_at = models.DateTimeField(
        "abertura",
        null=True,
        blank=True,
        help_text="Obrigatorio para publicar.",
    )
    close_at = models.DateTimeField(
        "encerramento",
        null=True,
        blank=True,
        help_text="Obrigatorio para publicar. Precisa ser posterior a abertura.",
    )
    duration_minutes = models.PositiveIntegerField(
        "duracao em minutos",
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Tempo de prova em minutos. Obrigatorio para publicar.",
    )

    passing_score = models.DecimalField(
        "nota minima",
        max_digits=4,
        decimal_places=2,
        default=Decimal("8.00"),
        help_text="Na escala de 0 a 10.",
    )
    total_points = models.DecimalField(
        "total de pontos",
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Congelado na publicacao. Enquanto a prova e rascunho, a tela "
            "mostra a soma corrente das questoes ativas."
        ),
    )

    failure_message = models.TextField(
        "mensagem de reprovacao",
        blank=True,
        default=MENSAGEM_REPROVACAO_PADRAO,
    )

    max_attempts = models.PositiveSmallIntegerField(
        "tentativas permitidas",
        default=1,
        validators=[MinValueValidator(1)],
    )

    randomize_questions = models.BooleanField("sortear ordem das questoes", default=False)
    randomize_options = models.BooleanField(
        "sortear ordem das alternativas", default=False
    )
    show_score_after_submission = models.BooleanField(
        "mostrar nota apos o envio", default=True
    )

    # Somente o hash, nunca a senha. Vazio significa prova sem senha.
    access_password_hash = models.CharField(
        "hash da senha de acesso", max_length=128, blank=True
    )

    version = models.PositiveIntegerField("versao", default=1)
    # PROTECT, e nao SET_NULL. Uma versao que tem descendentes faz parte de
    # historico: apaga-la zeraria as referencias de quem veio depois e
    # deixaria a linhagem sem comeco. Com SET_NULL o DELETE ate era tentado, e
    # so falhava mais adiante, ao esbarrar nas constraints de coerencia — um
    # IntegrityError obscuro, disparado por um UPDATE que o proprio Django
    # emitia. PROTECT recusa antes de tocar em qualquer linha, com um erro que
    # diz o que aconteceu.
    parent_exam = models.ForeignKey(
        "self",
        verbose_name="prova de origem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="derivadas",
        help_text="De qual prova esta versao foi duplicada.",
    )
    root_exam = models.ForeignKey(
        "self",
        verbose_name="raiz da linhagem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="versoes",
        help_text="Nulo na primeira versao, que e a propria raiz.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="criada por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exams_criadas",
    )

    created_at = models.DateTimeField("criada em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizada em", auto_now=True)
    published_at = models.DateTimeField("publicada em", null=True, blank=True)
    closed_at = models.DateTimeField("fechada em", null=True, blank=True)

    objects = ExamQuerySet.as_manager()

    class Meta:
        verbose_name = "prova"
        verbose_name_plural = "provas"
        ordering = ["module__order", "module__name", "title", "-version"]
        constraints = [
            models.CheckConstraint(
                condition=Q(passing_score__gte=0) & Q(passing_score__lte=NOTA_MAXIMA),
                name="exam_nota_minima_entre_0_e_10",
            ),
            models.CheckConstraint(
                condition=Q(total_points__gte=0),
                name="exam_total_de_pontos_nao_negativo",
            ),
            models.CheckConstraint(
                condition=Q(max_attempts__gte=1),
                name="exam_tentativas_pelo_menos_uma",
            ),
            models.CheckConstraint(
                condition=Q(duration_minutes__isnull=True) | Q(duration_minutes__gte=1),
                name="exam_duracao_positiva_quando_definida",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="exam_versao_pelo_menos_um",
            ),
            # A janela precisa fazer sentido sempre que os dois lados
            # existirem. Enquanto a prova e rascunho os campos podem estar
            # vazios; a publicacao e que exige os dois preenchidos.
            models.CheckConstraint(
                condition=(
                    Q(open_at__isnull=True)
                    | Q(close_at__isnull=True)
                    | Q(open_at__lt=F("close_at"))
                ),
                name="exam_janela_coerente",
            ),
            # Duas duplicacoes simultaneas nao podem produzir duas v4. O
            # servico ainda trava a raiz com select_for_update antes de
            # calcular a proxima versao; esta constraint e a garantia final,
            # a que nao depende de ninguem lembrar de usar a transacao certa.
            models.UniqueConstraint(
                fields=["root_exam", "version"],
                name="exam_versao_unica_na_linhagem",
            ),
            # Ser raiz e ter raiz sao estados exclusivos. Ou a prova comeca a
            # linhagem, nao aponta para ninguem e e a versao 1, ou ela deriva
            # de alguma versao anterior e por isso e no minimo a 2.
            #
            # Sem isto o banco aceitaria uma copia se dizendo versao 1, e a
            # linhagem passaria a ter duas provas reivindicando ser o comeco;
            # ou uma raiz se dizendo versao 2, e a numeracao passaria a mentir
            # sobre quantas versoes existiram.
            models.CheckConstraint(
                condition=(
                    Q(root_exam__isnull=True, version=1)
                    | Q(root_exam__isnull=False, version__gte=2)
                ),
                name="exam_raiz_e_versao_coerentes",
            ),
            # As duas referencias tem papeis diferentes — raiz e identidade da
            # linhagem, origem e procedencia — mas existem ou faltam juntas.
            # Uma copia sem origem perde justamente o historico que o
            # versionamento existe para preservar; uma raiz com origem nao e
            # raiz.
            #
            # O que uma CheckConstraint nao alcanca: exigir que
            # parent.root_exam seja esta mesma raiz e comparacao entre linhas,
            # fora do que o banco consegue impor sozinho. Essa consistencia
            # continua sendo responsabilidade de duplicate_exam.
            models.CheckConstraint(
                condition=(
                    Q(root_exam__isnull=True, parent_exam__isnull=True)
                    | Q(root_exam__isnull=False, parent_exam__isnull=False)
                ),
                name="exam_linhagem_parent_coerente",
            ),
        ]
        indexes = [
            models.Index(fields=["module", "status"], name="exam_modulo_situacao_idx"),
        ]

    def __str__(self):
        return "{} (v{})".format(self.title, self.version)

    def save(self, *args, **kwargs):
        self.title = (self.title or "").strip()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.title = (self.title or "").strip()
        if not self.title:
            raise ValidationError({"title": "O titulo da prova e obrigatorio."})
        if self.open_at and self.close_at and self.open_at >= self.close_at:
            raise ValidationError(
                {"close_at": "O encerramento precisa ser posterior a abertura."}
            )

    # -- Estado -------------------------------------------------------------

    @property
    def e_rascunho(self):
        return self.status == ExamStatus.DRAFT

    @property
    def e_publicada(self):
        return self.status == ExamStatus.PUBLISHED

    @property
    def e_fechada(self):
        return self.status == ExamStatus.CLOSED

    @property
    def estrutura_editavel(self):
        """
        Se questoes, alternativas, gabarito, pontos e modulo podem mudar.

        Uma unica definicao, consultada pelos servicos e pelos templates, de
        modo que a tela nunca ofereca um botao que o backend vai recusar.
        """
        return self.status == ExamStatus.DRAFT

    @property
    def tem_senha(self):
        return bool(self.access_password_hash)

    # -- Pontuacao ----------------------------------------------------------

    @property
    def pontos_das_questoes(self):
        """
        Soma corrente dos pontos das questoes ativas.

        Enquanto a prova e rascunho, e este o numero que a interface mostra.
        Na publicacao ele e calculado e gravado em total_points, e a partir
        dai a prova carrega a sua propria escala historica, mesmo que alguem
        edite algo depois em outra versao.
        """
        total = self.questions.filter(active=True).aggregate(soma=Sum("points"))["soma"]
        return total if total is not None else Decimal("0.00")

    @property
    def pontos_vigentes(self):
        """O que a tela deve exibir: soma corrente em rascunho, snapshot depois."""
        return self.pontos_das_questoes if self.e_rascunho else self.total_points

    @property
    def linhagem_id(self):
        return self.root_exam_id or self.pk


class QuestionQuerySet(models.QuerySet):
    def ativas(self):
        return self.filter(active=True)


class Question(models.Model):
    """
    Uma questao de uma prova.

    Questoes nunca sao compartilhadas entre versoes de prova. Duplicar uma
    prova cria questoes novas, com PKs proprias: se a v2 fosse editada e
    apontasse para as mesmas linhas da v1, o historico da v1 mudaria junto, e
    uma prova ja aplicada deixaria de descrever o que o aluno respondeu.
    """

    exam = models.ForeignKey(
        Exam, verbose_name="prova", on_delete=models.CASCADE, related_name="questions"
    )

    type = models.CharField(
        "tipo",
        max_length=20,
        choices=QuestionType.choices,
        default=QuestionType.SINGLE_CHOICE,
    )
    text = models.TextField("enunciado")
    points = models.DecimalField(
        "valor",
        max_digits=6,
        decimal_places=2,
        default=Decimal("1.00"),
        help_text="Precisa ser maior que zero.",
    )
    required = models.BooleanField(
        "obrigatoria",
        default=True,
        help_text="Questao nao respondida valera zero na correcao.",
    )
    order = models.PositiveIntegerField("ordem", default=0)

    internal_explanation = models.TextField(
        "explicacao interna",
        blank=True,
        help_text=(
            "Visivel apenas para a equipe administrativa. Nunca e enviada ao "
            "aluno."
        ),
    )
    active = models.BooleanField(
        "ativa",
        default=True,
        help_text="Questao inativa nao conta na prova nem na soma de pontos.",
    )

    created_at = models.DateTimeField("criada em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizada em", auto_now=True)

    objects = QuestionQuerySet.as_manager()

    class Meta:
        verbose_name = "questao"
        verbose_name_plural = "questoes"
        # A ordem funcional e o campo order; o id so desempata. Usar id como
        # ordem faria a sequencia depender de quando a questao foi criada.
        ordering = ["order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(points__gt=0),
                name="question_valor_positivo",
            ),
        ]
        indexes = [
            models.Index(fields=["exam", "order"], name="question_prova_ordem_idx"),
        ]

    def __str__(self):
        return "Q{} - {}".format(self.order, self.text[:40])

    @property
    def usa_alternativas(self):
        return self.type in TIPOS_COM_ALTERNATIVAS

    @property
    def correcao_manual(self):
        return self.type in TIPOS_DE_CORRECAO_MANUAL


class QuestionOptionQuerySet(models.QuerySet):
    def corretas(self):
        return self.filter(is_correct=True)

    def sem_gabarito(self):
        """
        Projecao segura para qualquer caminho que termine no navegador.

        Devolve dicionarios com id, texto e ordem, e nada mais. E .values(),
        e nao .only() nem .defer(), de proposito: um objeto com campo adiado
        continua tendo o atributo, e ler .is_correct nele dispara uma consulta
        nova e devolve a resposta certa em silencio. Com .values() o campo
        nao existe na estrutura, entao o vazamento deixa de ser uma questao
        de disciplina de quem escreve o template.
        """
        return self.values("id", "question_id", "text", "order").order_by("order", "id")


class QuestionOption(models.Model):
    """
    Alternativa de uma questao.

    is_correct e o gabarito. So pode ser lido por tela administrativa, por
    validacao de estrutura e, futuramente, pelo motor de correcao. Ver
    exams.selectors para o caminho que a tela do aluno usa.
    """

    question = models.ForeignKey(
        Question,
        verbose_name="questao",
        on_delete=models.CASCADE,
        related_name="options",
    )
    text = models.CharField("texto", max_length=300)
    is_correct = models.BooleanField(
        "correta",
        default=False,
        help_text="Gabarito. Nunca e enviado ao aluno.",
    )
    order = models.PositiveIntegerField("ordem", default=0)

    objects = QuestionOptionQuerySet.as_manager()

    class Meta:
        verbose_name = "alternativa"
        verbose_name_plural = "alternativas"
        ordering = ["order", "id"]
        indexes = [
            models.Index(fields=["question", "order"], name="opcao_questao_ordem_idx"),
        ]

    def __str__(self):
        return self.text[:60]
