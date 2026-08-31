"""
A prova sendo realizada pelo aluno.

Cinco modelos que formam o registro historico de uma tentativa:

    ExamAttempt       a tentativa: quem, qual prova, quando comeca e acaba
    AttemptQuestion   uma questao como ela foi apresentada nesta tentativa
    AttemptOption     uma alternativa como ela foi apresentada nesta tentativa
    Answer            o que o aluno respondeu numa questao
    AnswerOption      quais alternativas ele marcou

Por que a tentativa copia a apresentacao, e nao o conteudo
----------------------------------------------------------
AttemptQuestion e AttemptOption existem para guardar duas coisas que sao
proprias de cada aluno: a ordem em que os itens foram exibidos e o token
publico pelo qual aquele aluno se refere a eles. O enunciado, o texto da
alternativa e o valor da questao continuam vivendo em Question e
QuestionOption, alcancados por chave estrangeira — a prova publicada e
imutavel, entao nao ha o que duplicar.

O que a tentativa deliberadamente NAO copia e is_correct. Ela guarda a
referencia para a QuestionOption, e a leitura do gabarito acontece no
servidor, na correcao. Copiar o gabarito para dentro da tentativa criaria uma
segunda copia dele exatamente na tabela que a tela do aluno mais consulta.

Tokens publicos
---------------
Question.id e QuestionOption.id sao identificadores internos e nunca chegam
ao navegador. O aluno recebe UUID4 por tentativa: a mesma questao tem tokens
diferentes para alunos diferentes, o que impede que um combine com o outro
"marque a alternativa X" — X nao existe na tentativa do colega.

Os tokens nascem uma unica vez, na criacao da tentativa, e ficam gravados.
Gerar a cada request faria o F5 trocar todos eles e perder as respostas.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from exams.models.exam import QuestionType

# Limites de tamanho aplicados no servidor. O maxlength do HTML e conforto de
# interface, nao validacao: um POST montado a mao o ignora.
#
# Nao viram CheckConstraint porque o limite depende do tipo da questao, que
# mora em Question — duas tabelas de distancia de Answer. Uma check enxerga
# apenas a propria linha. A validacao real fica em exams.services.attempt, que
# e o unico caminho de escrita, e esta coberta por teste.
LIMITE_SHORT_TEXT = 2_000
LIMITE_ESSAY = 20_000


class AttemptStatus(models.TextChoices):
    """
    Situacao de uma tentativa.

        IN_PROGRESS   em andamento, aceita autosave
        SUBMITTED     o aluno enviou
        EXPIRED       o tempo acabou
        RESET         anulada por decisao administrativa

    SUBMITTED e EXPIRED sao finais para edicao: nenhuma resposta muda depois.

    RESET ja existe no enum, mas nao ha nada nesta etapa que produza esse
    estado — o reset administrativo entra em etapa futura. Esta aqui para que
    o valor nasca junto com a tabela, e nao numa migration posterior que
    precisaria reescrever o historico ja gravado.

    Quando o reset existir, a regra combinada sera:

        para max_attempts   contam todos os estados, EXCETO RESET
        para attempt_number nenhum numero e reaproveitado, nem o da anulada

    Ou seja, anular a tentativa 1 e refazer produz 1 RESET e 2 SUBMITTED, e
    nunca duas tentativas de numero 1. O numero identifica a tentativa no
    historico; o limite conta apenas o que valeu.
    """

    IN_PROGRESS = "IN_PROGRESS", "Em andamento"
    SUBMITTED = "SUBMITTED", "Enviada"
    EXPIRED = "EXPIRED", "Tempo encerrado"
    RESET = "RESET", "Anulada"


# Estados que nao aceitam mais escrita de resposta.
ESTADOS_ENCERRADOS = frozenset(
    {AttemptStatus.SUBMITTED, AttemptStatus.EXPIRED, AttemptStatus.RESET}
)

# Estados que devem ser corrigidos. Expirada tambem entra: o aluno teve o
# tempo dele, o que ficou em branco vale zero, e uma prova expirada com nota
# zero e um resultado — nao a ausencia de um.
ESTADOS_CORRIGIVEIS = frozenset({AttemptStatus.SUBMITTED, AttemptStatus.EXPIRED})


class GradingStatus(models.TextChoices):
    """
    Situacao da CORRECAO, que e outra dimensao que a situacao da tentativa.

    ExamAttempt.status responde "o aluno ainda esta fazendo?"; grading_status
    responde "ja sabemos a nota?". Sao perguntas independentes: uma tentativa
    SUBMITTED pode estar PENDING, AWAITING_REVIEW ou GRADED, e a resposta muda
    sem que o aluno faca nada.

    Misturar as duas num campo so — um status que fosse
    IN_PROGRESS/SUBMITTED/APPROVED — obrigaria a escolher qual das duas
    informacoes perder no momento em que a prova fosse aprovada.

        PENDING           encerrada, correcao ainda nao rodou
        AWAITING_REVIEW   objetivas corrigidas, falta avaliador
        GRADED            nota final fechada
    """

    PENDING = "PENDING", "Aguardando correcao"
    AWAITING_REVIEW = "AWAITING_REVIEW", "Aguardando avaliador"
    GRADED = "GRADED", "Corrigida"


class AttemptResult(models.TextChoices):
    """
    Aprovacao. Nulo enquanto a correcao nao fecha.

    Nunca chega do navegador: e sempre calculado a partir de
    obtained_points e passing_score_snapshot, no servidor.
    """

    APPROVED = "APPROVED", "Aprovado"
    FAILED = "FAILED", "Reprovado"


class QuestionGradingStatus(models.TextChoices):
    """
    Situacao da correcao de UMA questao.

        PENDING           textual sem nota ainda
        AUTO_GRADED       objetiva corrigida pela maquina
        MANUALLY_GRADED   textual avaliada por uma pessoa

    A diferenca entre AUTO_GRADED e MANUALLY_GRADED nao e decorativa: ela
    responde "quem deu esta nota" sem depender de graded_by, que fica nulo nas
    automaticas, e permite recontar objetivas sem tocar no trabalho do
    avaliador.
    """

    PENDING = "PENDING", "Pendente"
    AUTO_GRADED = "AUTO_GRADED", "Corrigida automaticamente"
    MANUALLY_GRADED = "MANUALLY_GRADED", "Corrigida pelo avaliador"


# Tipos que a maquina corrige sozinha. Os demais dependem de leitura humana.
TIPOS_AUTOCORRIGIVEIS = frozenset(
    {
        QuestionType.SINGLE_CHOICE,
        QuestionType.MULTIPLE_CHOICE,
        QuestionType.TRUE_FALSE,
    }
)

# Casas decimais do final_score.
#
# Seis, e nao duas. A nota exibida ao aluno tem duas casas, mas a comparacao
# com a nota minima acontece ANTES de qualquer arredondamento: guardar 8.00
# quando o valor real era 7.996 destruiria a informacao que separa aprovado de
# reprovado, e a tela mostraria "8,00 - Reprovado" sem nada que explicasse.
CASAS_DA_NOTA = 6


class ExamAttemptQuerySet(models.QuerySet):
    def em_andamento(self):
        return self.filter(status=AttemptStatus.IN_PROGRESS)

    def que_contam_para_o_limite(self):
        """
        Tentativas que consomem uma das max_attempts do aluno.

        Tudo menos as anuladas. Uma tentativa que expirou consumiu a chance:
        o aluno teve o tempo dele e nao enviou.
        """
        return self.exclude(status=AttemptStatus.RESET)

    def vencidas(self, agora):
        """Em andamento cujo prazo ja passou. Base do comando de expiracao."""
        return self.filter(
            status=AttemptStatus.IN_PROGRESS, expires_at__lte=agora
        )


class ExamAttempt(models.Model):
    """
    Uma tentativa de um aluno em uma prova.

    Registro historico proprio: guarda a escala vigente no momento em que
    comecou, e nao uma referencia a escala atual da prova. Hoje a prova
    publicada e imutavel e os dois valores coincidiriam, mas a tentativa e
    quem responde "sobre quantos pontos este aluno foi avaliado", e essa
    resposta nao pode depender de nada que aconteca depois.
    """

    # Identificador publico. Vai na URL e no HTML; o pk nunca sai daqui.
    public_id = models.UUIDField(
        "identificador publico", default=uuid.uuid4, unique=True, editable=False
    )

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="aluno",
        on_delete=models.PROTECT,
        related_name="exam_attempts",
        limit_choices_to={"role": "STUDENT"},
    )
    exam = models.ForeignKey(
        "exams.Exam",
        verbose_name="prova",
        on_delete=models.PROTECT,
        related_name="attempts",
    )

    attempt_number = models.PositiveIntegerField(
        "numero da tentativa",
        help_text="Sequencial por aluno e prova. Nunca reaproveitado.",
    )

    status = models.CharField(
        "situacao",
        max_length=12,
        choices=AttemptStatus.choices,
        default=AttemptStatus.IN_PROGRESS,
        db_index=True,
    )

    started_at = models.DateTimeField("iniciada em")
    # Calculado uma unica vez, no start. Nunca recalculado: nem quando o
    # administrador fecha a prova, nem quando muda a duracao, nem no primeiro
    # request depois de uma pausa. O prazo do aluno e o que foi combinado com
    # ele no momento em que a prova abriu na tela.
    expires_at = models.DateTimeField("expira em")

    submitted_at = models.DateTimeField("enviada em", null=True, blank=True)
    expired_at = models.DateTimeField("expirada em", null=True, blank=True)

    # Informacao operacional. Nao interfere no prazo: renovar o tempo a cada
    # clique transformaria a duracao em tempo de inatividade.
    last_activity_at = models.DateTimeField("ultima atividade", null=True, blank=True)

    total_points_snapshot = models.DecimalField(
        "total de pontos na largada", max_digits=8, decimal_places=2
    )
    passing_score_snapshot = models.DecimalField(
        "nota minima na largada", max_digits=4, decimal_places=2
    )

    # --- correcao (Etapa 5) ------------------------------------------------
    #
    # Dimensao separada de `status`. Ver GradingStatus para o porque.
    grading_status = models.CharField(
        "situacao da correcao",
        max_length=16,
        choices=GradingStatus.choices,
        default=GradingStatus.PENDING,
        db_index=True,
    )
    result = models.CharField(
        "resultado",
        max_length=8,
        choices=AttemptResult.choices,
        null=True,
        blank=True,
        db_index=True,
    )

    # Somas por origem, e nao apenas o total. Separar objetivas de manuais
    # permite recontar a parte automatica sem tocar no trabalho do avaliador,
    # e responde "quanto veio da maquina e quanto veio de uma pessoa" — que e
    # a primeira pergunta de qualquer recurso de nota.
    objective_points = models.DecimalField(
        "pontos das objetivas", max_digits=8, decimal_places=2, default=0
    )
    manual_points = models.DecimalField(
        "pontos das manuais", max_digits=8, decimal_places=2, default=0
    )
    obtained_points = models.DecimalField(
        "pontos obtidos", max_digits=8, decimal_places=2, default=0
    )

    # Seis casas decimais, de proposito.
    #
    # A nota exibida tem duas, mas a comparacao com a nota minima acontece
    # ANTES de arredondar. Guardar 8.00 quando o valor real era 7.996
    # destruiria justamente o digito que separa aprovado de reprovado, e a
    # tela mostraria "8,00 - Reprovado" sem nada que explicasse.
    final_score = models.DecimalField(
        "nota final",
        max_digits=9,
        decimal_places=CASAS_DA_NOTA,
        null=True,
        blank=True,
    )
    graded_at = models.DateTimeField("corrigida em", null=True, blank=True)

    # Evidencia para auditoria, nunca autenticacao. A tentativa nao e amarrada
    # a IP nem a dispositivo: o aluno pode legitimamente trocar de rede, sair
    # do wi-fi para o 4G ou continuar no celular.
    ip_address = models.GenericIPAddressField("endereco IP", null=True, blank=True)
    user_agent = models.TextField("user-agent", blank=True)

    created_at = models.DateTimeField("criada em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizada em", auto_now=True)

    objects = ExamAttemptQuerySet.as_manager()

    class Meta:
        verbose_name = "tentativa"
        verbose_name_plural = "tentativas"
        ordering = ["-started_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "exam", "attempt_number"],
                name="tentativa_numero_unico_por_aluno_e_prova",
            ),
            # A defesa final contra duas tentativas abertas ao mesmo tempo.
            # start_attempt ja serializa os starts do aluno com
            # select_for_update, mas essa trava depende de alguem lembrar de
            # usar a transacao certa; esta nao depende de ninguem.
            models.UniqueConstraint(
                fields=["student", "exam"],
                condition=Q(status=AttemptStatus.IN_PROGRESS),
                name="uniq_tentativa_em_andamento",
            ),
            models.CheckConstraint(
                condition=Q(attempt_number__gte=1),
                name="tentativa_numero_pelo_menos_um",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("started_at")),
                name="tentativa_prazo_posterior_ao_inicio",
            ),
            models.CheckConstraint(
                condition=Q(total_points_snapshot__gte=0),
                name="tentativa_total_de_pontos_nao_negativo",
            ),
            models.CheckConstraint(
                condition=(
                    Q(passing_score_snapshot__gte=0)
                    & Q(passing_score_snapshot__lte=10)
                ),
                name="tentativa_nota_minima_entre_0_e_10",
            ),
            # A situacao e os dois carimbos de encerramento contam a mesma
            # historia, ou a linha nao entra. O service ja garante isso, mas
            # ele garante apenas para quem passa por ele: um UPDATE direto,
            # um shell as pressas ou um script de correcao de dados escrevem
            # sem pedir licenca. Uma tentativa SUBMITTED sem submitted_at nao
            # e um registro ruim, e um registro que mente sobre quando o aluno
            # entregou.
            #
            # RESET fica de fora da exigencia de formato de proposito. O reset
            # administrativo ainda nao existe, e quando existir a decisao mais
            # provavel e preservar o submitted_at ou o expired_at da tentativa
            # anulada — apagar o carimbo destruiria justamente a informacao que
            # justifica a anulacao. Amarrar o formato agora seria escolher, sem
            # necessidade, a regra de uma etapa que ainda nao foi discutida.
            models.CheckConstraint(
                condition=(
                    Q(
                        status=AttemptStatus.IN_PROGRESS,
                        submitted_at__isnull=True,
                        expired_at__isnull=True,
                    )
                    | Q(
                        status=AttemptStatus.SUBMITTED,
                        submitted_at__isnull=False,
                        expired_at__isnull=True,
                    )
                    | Q(
                        status=AttemptStatus.EXPIRED,
                        submitted_at__isnull=True,
                        expired_at__isnull=False,
                    )
                    | Q(status=AttemptStatus.RESET)
                ),
                name="tentativa_status_e_timestamps_coerentes",
            ),
            # Nenhum encerramento acontece antes do comeco.
            #
            # Sao duas constraints separadas, e nao uma so com dois ramos,
            # porque a mensagem de recusa nomeia a constraint: assim o erro
            # diz qual dos dois carimbos esta fora de ordem.
            #
            # Nao existe aqui uma exigencia de submitted_at <= expires_at, e a
            # ausencia e deliberada. Uma requisicao pode entrar dentro do prazo
            # e so obter o lock da linha alguns milissegundos depois dele; quem
            # decide se aquilo foi envio ou expiracao e a service layer, com o
            # relogio do servidor, e ela ja transforma esse caso em EXPIRED.
            # Uma check nessa comparacao recusaria uma linha que o codigo
            # produz legitimamente e derrubaria o envio de um aluno por causa
            # de uma disputa de lock.
            models.CheckConstraint(
                condition=(
                    Q(submitted_at__isnull=True)
                    | Q(submitted_at__gte=F("started_at"))
                ),
                name="tentativa_envio_nao_anterior_ao_inicio",
            ),
            models.CheckConstraint(
                condition=(
                    Q(expired_at__isnull=True)
                    | Q(expired_at__gte=F("started_at"))
                ),
                name="tentativa_expiracao_nao_anterior_ao_inicio",
            ),
            # choices e validacao de formulario: o banco nunca ouviu falar
            # dela. Um UPDATE direto grava "HACKED" em status sem reclamar, e
            # a partir dai a linha escapa de toda regra escrita em cima do
            # enum — nao esta em andamento, nao esta encerrada, nao aparece na
            # busca do comando de expiracao e nao conta para o limite de
            # tentativas. Fica invisivel.
            #
            # A lista e literal na migration, o que e o comportamento
            # desejado: acrescentar uma situacao nova passa a exigir migration
            # nova, e essa migration e o lugar certo para decidir o que a
            # situacao nova faz com submitted_at e expired_at na constraint
            # acima.
            models.CheckConstraint(
                condition=Q(status__in=AttemptStatus.values),
                name="tentativa_situacao_conhecida",
            ),
            # --- correcao (Etapa 5) ---------------------------------------
            models.CheckConstraint(
                condition=Q(grading_status__in=GradingStatus.values),
                name="tentativa_correcao_situacao_conhecida",
            ),
            models.CheckConstraint(
                condition=(
                    Q(result__isnull=True) | Q(result__in=AttemptResult.values)
                ),
                name="tentativa_resultado_conhecido",
            ),
            # Resultado e nota existem se, e somente se, a correcao fechou.
            # Um resultado sem nota nao teria como ser conferido; uma nota sem
            # correcao fechada seria um numero que o aluno poderia ver antes
            # de a avaliacao terminar.
            models.CheckConstraint(
                condition=(
                    Q(
                        grading_status=GradingStatus.GRADED,
                        result__isnull=False,
                        final_score__isnull=False,
                        graded_at__isnull=False,
                    )
                    | (
                        ~Q(grading_status=GradingStatus.GRADED)
                        & Q(
                            result__isnull=True,
                            final_score__isnull=True,
                            graded_at__isnull=True,
                        )
                    )
                ),
                name="tentativa_nota_so_existe_se_corrigida",
            ),
            models.CheckConstraint(
                condition=(
                    Q(objective_points__gte=0)
                    & Q(manual_points__gte=0)
                    & Q(obtained_points__gte=0)
                ),
                name="tentativa_pontos_obtidos_nao_negativos",
            ),
            # A nota vive na mesma escala da nota minima. Um final_score de
            # 87 significaria que alguem confundiu porcentagem com escala.
            models.CheckConstraint(
                condition=(
                    Q(final_score__isnull=True)
                    | (Q(final_score__gte=0) & Q(final_score__lte=10))
                ),
                name="tentativa_nota_final_entre_0_e_10",
            ),
        ]
        indexes = [
            models.Index(
                fields=["student", "exam"], name="tentativa_aluno_prova_idx"
            ),
            # Serve ao comando de expiracao, que procura exatamente por
            # status em andamento com prazo vencido.
            models.Index(
                fields=["status", "expires_at"], name="tentativa_situacao_prazo_idx"
            ),
            # Serve as duas telas novas: a fila de correcao filtra por
            # AWAITING_REVIEW e a de notas por GRADED, ambas ordenando por
            # data de envio.
            models.Index(
                fields=["grading_status", "submitted_at"],
                name="tentativa_correcao_idx",
            ),
        ]

    def __str__(self):
        return "Tentativa {} de {} em {}".format(
            self.attempt_number, self.student_id, self.exam_id
        )

    @property
    def em_andamento(self):
        return self.status == AttemptStatus.IN_PROGRESS

    @property
    def encerrada(self):
        """Se a tentativa ja nao aceita mais escrita de resposta."""
        return self.status in ESTADOS_ENCERRADOS

    def prazo_vencido(self, agora):
        """
        Se o relogio do servidor ja passou do prazo gravado.

        Recebe `agora` em vez de chamar timezone.now() por conta propria: o
        servico decide o instante uma vez e usa o mesmo em todas as
        comparacoes da operacao, senao duas checagens na mesma requisicao
        poderiam discordar.
        """
        return agora >= self.expires_at

    def segundos_restantes(self, agora):
        """
        Quanto falta, nunca negativo.

        E este numero que vai para a tela. O navegador so faz a contagem
        regressiva a partir dele; quem decide se ainda da tempo de salvar e
        sempre o servidor, no request seguinte.
        """
        if self.encerrada:
            return 0
        return max(0, int((self.expires_at - agora).total_seconds()))


class AttemptQuestion(models.Model):
    """
    Uma questao como foi apresentada nesta tentativa.

    Guarda o que e proprio do aluno — a posicao na tela e o token publico — e
    aponta para a Question, que continua sendo a dona do enunciado.
    """

    attempt = models.ForeignKey(
        ExamAttempt,
        verbose_name="tentativa",
        on_delete=models.CASCADE,
        related_name="questions",
    )
    question = models.ForeignKey(
        "exams.Question",
        verbose_name="questao",
        on_delete=models.PROTECT,
        related_name="attempt_questions",
    )

    public_token = models.UUIDField(
        "token publico", default=uuid.uuid4, unique=True, editable=False
    )
    display_order = models.PositiveIntegerField("ordem de exibicao")

    # Valor da questao no momento em que a prova foi apresentada.
    #
    # A prova publicada e imutavel, entao hoje isto sempre coincide com
    # question.points. Ainda assim vale a copia: a nota de um aluno e um
    # registro historico, e a pergunta "sobre quantos pontos esta questao foi
    # avaliada" nao pode depender de nada que aconteca depois — nem de uma
    # futura edicao emergencial, nem de um script de correcao de dados.
    #
    # Nulo apenas para as tentativas que ja existiam antes desta etapa; a
    # migration de dados preenche todas a partir da Question relacionada.
    points_snapshot = models.DecimalField(
        "valor da questao na largada",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )

    # --- correcao (Etapa 5) ------------------------------------------------
    awarded_points = models.DecimalField(
        "pontos concedidos",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    grading_status = models.CharField(
        "situacao da correcao",
        max_length=16,
        choices=QuestionGradingStatus.choices,
        default=QuestionGradingStatus.PENDING,
        db_index=True,
    )
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="corrigida por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questoes_corrigidas",
    )
    graded_at = models.DateTimeField("corrigida em", null=True, blank=True)

    # Observacao do avaliador. Uso administrativo: nao chega ao aluno nesta
    # etapa, e mostrar feedback por questao sera decisao separada.
    grader_comment = models.TextField("comentario do avaliador", blank=True)

    created_at = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        verbose_name = "questao da tentativa"
        verbose_name_plural = "questoes da tentativa"
        ordering = ["display_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "question"],
                name="tentativa_questao_uma_vez",
            ),
            models.UniqueConstraint(
                fields=["attempt", "display_order"],
                name="tentativa_posicao_unica",
            ),
            models.CheckConstraint(
                condition=Q(display_order__gte=0),
                name="tentativa_questao_posicao_nao_negativa",
            ),
            models.CheckConstraint(
                condition=Q(grading_status__in=QuestionGradingStatus.values),
                name="tentativa_questao_correcao_conhecida",
            ),
            models.CheckConstraint(
                condition=(
                    Q(awarded_points__isnull=True) | Q(awarded_points__gte=0)
                ),
                name="tentativa_questao_pontos_nao_negativos",
            ),
            # O teto e o valor da propria questao. Uma dissertativa de 2,00
            # pontos que recebesse 5,00 elevaria a nota da prova acima do
            # total possivel — e nenhuma tela mostraria de onde veio.
            #
            # Compara com points_snapshot, e nao com question.points: uma
            # check enxerga apenas a propria linha, e e essa a razao pratica
            # de o snapshot existir.
            models.CheckConstraint(
                condition=(
                    Q(awarded_points__isnull=True)
                    | Q(points_snapshot__isnull=True)
                    | Q(awarded_points__lte=F("points_snapshot"))
                ),
                name="tentativa_questao_pontos_ate_o_valor",
            ),
            # Pontos e situacao contam a mesma historia: uma questao corrigida
            # tem nota, uma pendente nao tem.
            models.CheckConstraint(
                condition=(
                    Q(
                        grading_status=QuestionGradingStatus.PENDING,
                        awarded_points__isnull=True,
                    )
                    | (
                        ~Q(grading_status=QuestionGradingStatus.PENDING)
                        & Q(awarded_points__isnull=False)
                    )
                ),
                name="tentativa_questao_pontos_coerentes_com_situacao",
            ),
        ]

    def __str__(self):
        return "Q{} da tentativa {}".format(self.display_order, self.attempt_id)


class AttemptOption(models.Model):
    """Uma alternativa como foi apresentada nesta tentativa."""

    attempt_question = models.ForeignKey(
        AttemptQuestion,
        verbose_name="questao da tentativa",
        on_delete=models.CASCADE,
        related_name="options",
    )
    option = models.ForeignKey(
        "exams.QuestionOption",
        verbose_name="alternativa",
        on_delete=models.PROTECT,
        related_name="attempt_options",
    )

    public_token = models.UUIDField(
        "token publico", default=uuid.uuid4, unique=True, editable=False
    )
    display_order = models.PositiveIntegerField("ordem de exibicao")

    created_at = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        verbose_name = "alternativa da tentativa"
        verbose_name_plural = "alternativas da tentativa"
        ordering = ["display_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["attempt_question", "option"],
                name="tentativa_alternativa_uma_vez",
            ),
            models.UniqueConstraint(
                fields=["attempt_question", "display_order"],
                name="tentativa_alternativa_posicao_unica",
            ),
            models.CheckConstraint(
                condition=Q(display_order__gte=0),
                name="tentativa_alternativa_posicao_nao_negativa",
            ),
        ]

    def __str__(self):
        return "Alternativa {} de {}".format(
            self.display_order, self.attempt_question_id
        )


class Answer(models.Model):
    """
    A resposta do aluno para uma questao da tentativa.

    OneToOne com AttemptQuestion: uma questao exibida tem no maximo uma
    resposta. A linha so nasce no primeiro autosave — questao sem Answer
    significa nao respondida, e essa e a leitura mais honesta que o banco
    consegue dar. Criar Answer vazia para todas as questoes no start
    obrigaria todo codigo posterior a distinguir "vazia" de "ausente".

    Nao guarda pontuacao nem acerto. A correcao entra na Etapa 5, e ate la
    nao existe nenhum campo aqui que revele gabarito.
    """

    attempt_question = models.OneToOneField(
        AttemptQuestion,
        verbose_name="questao da tentativa",
        on_delete=models.CASCADE,
        related_name="answer",
    )

    # Usado por SHORT_TEXT e ESSAY. Nas objetivas a resposta esta em
    # AnswerOption, e este campo fica vazio.
    text_answer = models.TextField("resposta em texto", blank=True)

    saved_at = models.DateTimeField("salva em")

    created_at = models.DateTimeField("criada em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizada em", auto_now=True)

    class Meta:
        verbose_name = "resposta"
        verbose_name_plural = "respostas"
        ordering = ["attempt_question__display_order", "id"]

    def __str__(self):
        return "Resposta de {}".format(self.attempt_question_id)


class AnswerOption(models.Model):
    """
    Uma alternativa marcada pelo aluno.

    Tabela de ligacao entre Answer e AttemptOption. Guarda a selecao como
    referencia, nunca como texto: copiar o texto da alternativa faria a
    resposta parar de acompanhar a alternativa que ela aponta.

    A regra que o banco nao alcanca: a alternativa marcada precisa pertencer
    a mesma questao da resposta, ou seja

        attempt_option.attempt_question_id == answer.attempt_question_id

    Isso e comparacao entre linhas de tabelas diferentes, fora do que uma
    CheckConstraint consegue expressar. A garantia fica em
    exams.services.attempt, que resolve todo token dentro da questao a que
    ele pertence, e esta coberta por teste com token forjado.
    """

    answer = models.ForeignKey(
        Answer,
        verbose_name="resposta",
        on_delete=models.CASCADE,
        related_name="selected_options",
    )
    attempt_option = models.ForeignKey(
        AttemptOption,
        verbose_name="alternativa da tentativa",
        on_delete=models.CASCADE,
        related_name="selections",
    )

    created_at = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        verbose_name = "alternativa marcada"
        verbose_name_plural = "alternativas marcadas"
        ordering = ["attempt_option__display_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["answer", "attempt_option"],
                name="resposta_alternativa_uma_vez",
            ),
        ]

    def __str__(self):
        return "{} marcou {}".format(self.answer_id, self.attempt_option_id)
