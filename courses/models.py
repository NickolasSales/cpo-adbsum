"""Modulos do curso e matriculas."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Count, F, Q
from django.db.models.functions import Upper

from common.texto import LIMITE_DO_MOTIVO

# Faixa aceita para o ano impresso no certificado. Nao e uma regra de negocio
# profunda: e um cerco contra o erro de digitacao que produziria um documento
# datado de 202 ou de 20226 e so seria notado depois de impresso.
ANO_MINIMO_DO_CERTIFICADO = 2000
ANO_MAXIMO_DO_CERTIFICADO = 2100


def normalizar_codigo(codigo):
    """
    Forma canonica do codigo de um modulo.

    " mod1 " vira "MOD1". Normalizar na escrita e o que torna MOD1, mod1 e
    Mod1 o mesmo modulo, sem depender de comparacao case-insensitive em toda
    consulta.
    """
    if not codigo:
        return codigo
    return codigo.strip().upper()


class ModuleQuerySet(models.QuerySet):
    def ativos(self):
        return self.filter(is_active=True)


class Module(models.Model):
    """Modulo do curso. Uma prova pertencera a um modulo (Etapa 3)."""

    name = models.CharField("nome", max_length=150)
    code = models.CharField(
        "codigo",
        max_length=30,
        unique=True,
        help_text="Identificador curto, como MOD1. Gravado sempre em maiusculas.",
        error_messages={"unique": "Ja existe um modulo com este codigo."},
    )
    description = models.TextField("descricao", blank=True)
    is_active = models.BooleanField(
        "ativo",
        default=True,
        db_index=True,
        help_text="Modulo inativo nao aparece para o aluno.",
    )
    order = models.PositiveIntegerField(
        "ordem",
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Define a ordem de exibicao. Menor aparece primeiro.",
    )

    # --- dados que vao impressos no certificado --------------------------
    #
    # Ficam no modulo, e nao em settings, porque cada turma tem os seus: o
    # Modulo I aconteceu numa data, na igreja Sede, com oito horas; o Modulo
    # II teve outras. Sao dados administrativos, preenchidos por quem sabe o
    # que aconteceu, e nao deduzidos pelo sistema.
    #
    # Todos nascem vazios de proposito. Modulos criados antes desta etapa nao
    # tem como adivinha-los, e inventar valores historicos produziria
    # certificado com data errada — que e pior do que certificado recusado.
    # A emissao recusa enquanto faltar algum, dizendo qual falta.
    certificate_display_name = models.CharField(
        "nome no certificado",
        max_length=150,
        blank=True,
        help_text=(
            "Como o modulo aparece no documento, por extenso. "
            "Exemplo: Modulo I - Cooperadores e Diaconos. "
            "Em branco, o certificado usa o nome do modulo."
        ),
    )
    certificate_course_dates_text = models.CharField(
        "data(s) do curso",
        max_length=120,
        blank=True,
        help_text=(
            "Texto livre, exatamente como deve sair impresso. "
            "Exemplo: 10 e 17 de outubro de 2026."
        ),
    )
    certificate_location = models.CharField(
        "local",
        max_length=120,
        blank=True,
        help_text="Onde o curso foi realizado. Exemplo: Igreja Sede.",
    )
    certificate_workload_hours = models.PositiveSmallIntegerField(
        "carga horaria",
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Em horas, numero inteiro maior que zero.",
    )
    certificate_year = models.PositiveSmallIntegerField(
        "ano",
        null=True,
        blank=True,
        validators=[
            MinValueValidator(ANO_MINIMO_DO_CERTIFICADO),
            MaxValueValidator(ANO_MAXIMO_DO_CERTIFICADO),
        ],
        help_text="Ano em destaque na lateral do certificado. Exemplo: 2026.",
    )

    # --- modelo de certificado (Etapa 10) ---------------------------------
    #
    # O vinculo mora aqui, e nao em CertificateTemplate.module, de proposito.
    #
    # O pedido da etapa sugeria um campo `module` no proprio modelo. Os dois
    # sentidos expressariam o mesmo fato, e dois lugares para o mesmo fato
    # divergem: bastaria alguem editar um deles. Alem disso o sentido daqui
    # permite o que a instituicao de fato tem — uma arte oficial servindo os
    # tres modulos —, enquanto o sentido inverso amarraria cada arte a um
    # modulo so.
    #
    # Vazio nao significa "sem certificado": significa "usa o modelo padrao".
    # A resolucao esta em certificates.services.resolver_template.
    certificate_template = models.ForeignKey(
        "certificates.CertificateTemplate",
        verbose_name="modelo de certificado",
        # SET_NULL: arquivar um modelo nao pode impedir o modulo de existir.
        # O modulo volta ao padrao global, e a emissao avisa se nao houver.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modules",
        help_text="Em branco, o modulo usa o modelo padrao ativo.",
    )

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    objects = ModuleQuerySet.as_manager()

    class Meta:
        verbose_name = "modulo"
        verbose_name_plural = "modulos"
        ordering = ["order", "name"]
        constraints = [
            # PositiveIntegerField ja recusa negativos no PostgreSQL, mas a
            # constraint explicita documenta a regra e sobrevive a uma
            # eventual troca do tipo do campo.
            models.CheckConstraint(
                condition=models.Q(order__gte=0),
                name="module_ordem_nao_negativa",
            ),
            # unique=True no campo ja garante a unicidade porque save()
            # sempre grava em maiusculas. Este indice funcional cobre os
            # caminhos que nao passam por save(), como bulk_create e SQL
            # direto, e e a garantia real de que MOD1 e mod1 nao coexistem.
            models.UniqueConstraint(
                Upper("code"),
                name="module_codigo_unico_ignorando_caixa",
            ),
            # Os validators cobrem formulario e full_clean(); estas duas
            # cobrem o resto — update() em queryset, shell, SQL direto. Um ano
            # de 202 num certificado so aparece depois de impresso, e ai nao
            # ha correcao possivel no papel que ja foi entregue.
            models.CheckConstraint(
                condition=(
                    models.Q(certificate_workload_hours__isnull=True)
                    | models.Q(certificate_workload_hours__gt=0)
                ),
                name="module_carga_horaria_positiva",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(certificate_year__isnull=True)
                    | models.Q(
                        certificate_year__gte=ANO_MINIMO_DO_CERTIFICADO,
                        certificate_year__lte=ANO_MAXIMO_DO_CERTIFICADO,
                    )
                ),
                name="module_ano_do_certificado_plausivel",
            ),
        ]

    def __str__(self):
        return "{} - {}".format(self.code, self.name)

    def save(self, *args, **kwargs):
        self.code = normalizar_codigo(self.code)
        self.name = (self.name or "").strip()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.code = normalizar_codigo(self.code)
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "O nome do modulo e obrigatorio."})
        if not self.code:
            raise ValidationError({"code": "O codigo do modulo e obrigatorio."})

    # --- certificado ------------------------------------------------------

    @property
    def nome_no_certificado(self):
        """
        Como o modulo sai impresso.

        O nome interno costuma ser curto e operacional ("Modulo 1"); o
        documento pede a forma por extenso ("Modulo I - Cooperadores e
        Diaconos"). Quando ninguem preencheu a segunda, a primeira serve — e
        um certificado com nome curto e melhor do que nenhum certificado.
        """
        return (self.certificate_display_name or "").strip() or self.name

    def dados_do_certificado_ausentes(self):
        """
        Rotulos dos campos obrigatorios que ainda estao vazios.

        Devolve lista vazia quando o modulo esta pronto para emitir. Os quatro
        exigidos aparecem no corpo do documento — sem eles a frase de
        conclusao fica com buracos, e um certificado com buraco e pior do que
        a recusa de emitir.

        certificate_display_name fica de fora porque tem substituto natural.
        """
        faltando = []
        if not (self.certificate_course_dates_text or "").strip():
            faltando.append("data(s) do curso")
        if not (self.certificate_location or "").strip():
            faltando.append("local")
        if not self.certificate_workload_hours:
            faltando.append("carga horaria")
        if not self.certificate_year:
            faltando.append("ano")
        return faltando

    @property
    def pronto_para_certificar(self):
        return not self.dados_do_certificado_ausentes()


class EnrollmentStatus(models.TextChoices):
    """
    Situacao academica da matricula.

    Nao confundir com access_enabled, que e uma chave operacional
    independente. Ver a docstring de Enrollment.
    """

    ACTIVE = "ACTIVE", "Ativa"
    INACTIVE = "INACTIVE", "Inativa"
    COMPLETED = "COMPLETED", "Concluida"
    # Etapa 9. Nao confundir com INACTIVE.
    #
    #   INACTIVE  pausa operacional, reversivel com um clique, sem motivo
    #             escrito. "Este aluno nao esta cursando agora."
    #
    #   REVOKED   ato administrativo que encerra o vinculo academico, exige
    #             motivo, grava quem e quando, e sai da lista padrao. "A
    #             instituicao revogou esta matricula."
    #
    # Dois valores porque sao duas decisoes de peso diferente, e apagar essa
    # diferenca faria a trilha nao conseguir distinguir um remanejamento de
    # turma de uma revogacao disciplinar.
    REVOKED = "REVOKED", "Revogada"


class EnrollmentQuerySet(models.QuerySet):
    def liberadas(self):
        """
        Matriculas que efetivamente dao acesso ao modulo.

        As tres condicoes precisam valer ao mesmo tempo: situacao academica
        ativa, acesso operacional liberado e modulo ativo. Este e o unico
        criterio que a area do aluno usa.
        """
        return self.filter(
            status=EnrollmentStatus.ACTIVE,
            access_enabled=True,
            module__is_active=True,
        )

    def do_aluno(self, user):
        return self.filter(student=user)

    def operacionais(self):
        """
        Matriculas que a lista administrativa mostra por padrao.

        Tudo menos as revogadas. Revogar e o ato que retira a matricula da
        visao do dia a dia; ela continua existindo e continua consultavel pelo
        filtro "Revogadas", que e onde se responde "quem foi revogado, quando
        e por que".
        """
        return self.exclude(status=EnrollmentStatus.REVOKED)

    def com_contagem_de_historico(self):
        """
        Anota total_tentativas: as tentativas deste aluno no modulo desta
        matricula.

        Existe para que a lista consiga decidir entre oferecer "Excluir" e
        oferecer "Revogar" sem uma consulta por linha.

        Uma contagem so basta para os dois criterios. Certificate.attempt e
        OneToOne com PROTECT, entao nao existe certificado sem tentativa: zero
        tentativas implica zero certificados. can_delete_enrollment ainda
        confere os dois separadamente — a tela otimiza, o servico decide.
        """
        return self.annotate(
            total_tentativas=Count(
                "student__exam_attempts",
                filter=Q(student__exam_attempts__exam__module=F("module")),
                distinct=True,
            )
        )


class Enrollment(models.Model):
    """
    Vinculo entre um aluno e um modulo.

    Dois conceitos deliberadamente separados:

        status          situacao academica (ativa, inativa, concluida)
        access_enabled  chave operacional de acesso

    A combinacao status=ACTIVE com access_enabled=False significa "continua
    matriculado, mas o acesso esta suspenso agora". Bloquear um aluno as
    vesperas da prova nao pode alterar a situacao academica dele.
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="aluno",
        on_delete=models.CASCADE,
        related_name="enrollments",
        limit_choices_to={"role": "STUDENT"},
    )
    module = models.ForeignKey(
        Module,
        verbose_name="modulo",
        on_delete=models.PROTECT,
        related_name="enrollments",
    )

    status = models.CharField(
        "situacao",
        max_length=12,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ACTIVE,
        db_index=True,
    )
    access_enabled = models.BooleanField(
        "acesso liberado",
        default=True,
        help_text="Bloqueio operacional. Nao altera a situacao academica.",
    )

    enrolled_at = models.DateTimeField("matriculado em", auto_now_add=True)
    notes = models.TextField("observacoes", blank=True)

    # --- revogacao administrativa (Etapa 9) --------------------------------
    #
    # Revogar NAO apaga nada e NAO mexe em certificado. A matricula passa a
    # REVOKED, perde o acesso e sai da lista operacional; tentativas, notas e
    # certificados continuam exatamente onde estavam.
    #
    # Um certificado ACTIVE com a matricula REVOKED e um estado legitimo: o
    # aluno concluiu o modulo e tem o documento, e a instituicao encerrou o
    # vinculo depois. Revogar o documento e outro ato, com outro fluxo e
    # outro evento na trilha.
    revoked_at = models.DateTimeField("revogada em", null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="revogada por",
        # SET_NULL: o registro de que houve revogacao, quando e por que
        # precisa sobreviver a remocao da conta administrativa que a executou.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollments_revogadas",
    )
    revocation_reason = models.TextField(
        "motivo da revogacao",
        blank=True,
        max_length=LIMITE_DO_MOTIVO,
        help_text="Obrigatorio ao revogar. Fica na propria matricula.",
    )

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    objects = EnrollmentQuerySet.as_manager()

    class Meta:
        verbose_name = "matricula"
        verbose_name_plural = "matriculas"
        ordering = ["module__order", "module__name", "student__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "module"],
                name="matricula_unica_por_aluno_e_modulo",
            ),
            # Revogada tem data; nao revogada nao tem. Impede as duas
            # mentiras simetricas: uma matricula ativa carregando "revogada em
            # 12/03", e uma matricula REVOKED sem dizer quando.
            #
            # revoked_by fica fora da regra porque e SET_NULL, e a linha
            # precisa continuar valida quando aquela conta for removida.
            models.CheckConstraint(
                condition=(
                    Q(status=EnrollmentStatus.REVOKED, revoked_at__isnull=False)
                    | (
                        ~Q(status=EnrollmentStatus.REVOKED)
                        & Q(revoked_at__isnull=True)
                    )
                ),
                name="matricula_revogacao_coerente",
            ),
            # Uma matricula revogada nunca da acesso. A regra ja esta em
            # liberadas() e em revoke_enrollment, mas essas sao camadas de
            # aplicacao: esta e a que sobrevive a um UPDATE direto no banco.
            models.CheckConstraint(
                condition=(
                    ~Q(status=EnrollmentStatus.REVOKED) | Q(access_enabled=False)
                ),
                name="matricula_revogada_sem_acesso",
            ),
        ]
        indexes = [
            models.Index(
                fields=["student", "status"], name="matricula_aluno_situacao_idx"
            ),
        ]

    def __str__(self):
        return "{} em {}".format(self.student.full_name, self.module.code)

    def clean(self):
        """
        Impede que um ADMIN seja matriculado como aluno.

        O PostgreSQL nao consegue expressar esta regra em constraint, porque
        ela depende de uma coluna de outra tabela. A garantia real fica na
        camada de servico; esta validacao cobre formularios e Django Admin.
        """
        super().clean()
        if self.student_id and getattr(self.student, "role", None) != "STUDENT":
            raise ValidationError(
                {"student": "Somente usuarios com papel ALUNO podem ser matriculados."}
            )

    @property
    def libera_acesso(self):
        """
        Se esta matricula, hoje, da acesso ao modulo.

        REVOKED nao precisa de clausula propria: a regra exige ACTIVE, e
        revogada nao e ativa. Foi por isso que a Etapa 2 separou situacao
        academica de chave de acesso — o status novo perdeu o acesso sem que
        nenhuma consulta de disponibilidade precisasse ser reescrita.
        """
        return (
            self.status == EnrollmentStatus.ACTIVE
            and self.access_enabled
            and self.module.is_active
        )

    @property
    def e_revogada(self):
        return self.status == EnrollmentStatus.REVOKED

    @property
    def sem_historico_academico(self):
        """
        Se a lista trouxe a contagem de historico e ela e zero.

        Depende da anotacao de com_contagem_de_historico(). Sem ela devolve
        False e a tela nao oferece a exclusao — errar para o lado de esconder
        um botao e inofensivo, e quem decide de fato e can_delete_enrollment,
        que reconta com a linha travada.
        """
        return getattr(self, "total_tentativas", None) == 0
