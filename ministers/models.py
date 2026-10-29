"""
Cadastro de ministros e conta de acesso ao Portal do Ministro.

Os campos nasceram da relacao oficial ("Relatorio Ministros 2026.xlsx") e da
guia de mensalidade em PDF, e nao de uma lista imaginada. O README, secao
"Controle de Ministros", registra a analise e cada decisao. As principais:

    nº da relacao       e a posicao da linha na planilha, de 1 a N, e nada
                        mais. Fica em import_sequence, para rastrear a linha
                        de origem, e nao e matricula de coisa nenhuma.
    Inscricao Conamad   presente, unica e preenchida em todas as linhas.
                        E o identificador do ministro e o login do portal.
    Matricula do campo  aparece na guia em PDF, mas NAO existe na planilha.
                        Opcional, unica quando preenchida.
    Congregacao         idem: so na guia. Opcional.
    Titulo              EV, PR, PRª e Mis na planilha. Vira escolha fechada.
    Cargo               "Pastor Presidente" na guia e funcao, nao titulo.
                        Texto livre opcional em position.
    IDADE               formula (=2026-ano). Derivada, nao e gravada.

Nenhum modelo aqui conhece o dominio academico. Ministro nao e User, nao tem
papel, nao entra em /admin-panel/ nem em /aluno/.
"""

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from ministers.normalizacao import (
    TAMANHO_MAXIMO_REGISTRO,
    normalizar_nome,
    normalizar_registro,
)


class MinisterialTitle(models.TextChoices):
    """
    Titulo ministerial, como aparece na coluna "Titulo" da relacao.

    A planilha usa abreviacoes (EV, PR, PRª, Mis) com caixa variada; o mapa
    de abreviacao para escolha fica no importador. "Missionario(a)" porque a
    relacao nao distingue o genero na abreviacao.
    """

    EVANGELISTA = "EVANGELISTA", "Evangelista"
    PASTOR = "PASTOR", "Pastor"
    PASTORA = "PASTORA", "Pastora"
    MISSIONARIO = "MISSIONARIO", "Missionário(a)"


class MinisterSex(models.TextChoices):
    MASCULINO = "M", "Masculino"
    FEMININO = "F", "Feminino"


class MinisterSource(models.TextChoices):
    """Como o cadastro entrou no sistema."""

    MANUAL = "MANUAL", "Cadastro manual"
    IMPORT = "IMPORT", "Importação de planilha"


class Minister(models.Model):
    """
    Dados ministeriais.

    Nunca e apagado pela aplicacao: a partir da etapa de mensalidades ele
    passa a ter historico financeiro, e apagar a linha quebraria esse
    historico. Sair da relacao e is_active=False.
    """

    full_name = models.CharField("nome completo", max_length=200)

    conamad_registration = models.CharField(
        "inscrição CONAMAD",
        max_length=TAMANHO_MAXIMO_REGISTRO,
        unique=True,
        error_messages={"unique": "Já existe um ministro com esta inscrição CONAMAD."},
        help_text="Identificador do ministro e login do Portal do Ministro.",
    )
    field_registration = models.CharField(
        "matrícula do campo",
        max_length=TAMANHO_MAXIMO_REGISTRO,
        blank=True,
        help_text="Opcional. Única quando preenchida.",
    )

    ministerial_title = models.CharField(
        "título ministerial",
        max_length=20,
        choices=MinisterialTitle.choices,
        db_index=True,
    )
    position = models.CharField(
        "cargo / função",
        max_length=100,
        blank=True,
        help_text=(
            "Opcional. Ex.: Pastor Presidente, Dirigente. Quando vazio, o "
            "portal exibe o título ministerial."
        ),
    )
    congregation = models.CharField(
        "congregação", max_length=120, blank=True, db_index=True
    )

    consecration_year = models.PositiveSmallIntegerField(
        "ano de consagração", null=True, blank=True
    )
    birth_date = models.DateField("data de nascimento", null=True, blank=True)
    sex = models.CharField(
        "sexo", max_length=1, choices=MinisterSex.choices, blank=True
    )

    notes = models.TextField(
        "observações",
        blank=True,
        help_text="Uso administrativo. O ministro não vê este campo.",
    )

    is_active = models.BooleanField(
        "ativo",
        default=True,
        db_index=True,
        help_text="Ministro inativo não acessa o portal, mesmo com acesso liberado.",
    )
    source = models.CharField(
        "origem",
        max_length=10,
        choices=MinisterSource.choices,
        default=MinisterSource.MANUAL,
        db_index=True,
    )
    import_sequence = models.PositiveIntegerField(
        "nº na relação importada",
        null=True,
        blank=True,
        help_text=(
            "Posição da linha na planilha de origem. Não é matrícula: serve "
            "só para localizar o registro no arquivo."
        ),
    )

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "ministro"
        verbose_name_plural = "ministros"
        ordering = ["full_name", "id"]
        constraints = [
            # A matricula do campo e opcional, e varios ministros sem ela
            # nao podem colidir entre si. O indice unico parcial so olha
            # quem a tem preenchida.
            models.UniqueConstraint(
                fields=["field_registration"],
                condition=~Q(field_registration=""),
                name="minister_field_registration_unique",
                violation_error_message="Já existe um ministro com esta matrícula do campo.",
            ),
            models.CheckConstraint(
                condition=~Q(conamad_registration=""),
                name="minister_conamad_registration_required",
            ),
            models.CheckConstraint(
                condition=~Q(full_name=""),
                name="minister_full_name_required",
            ),
            models.CheckConstraint(
                condition=Q(consecration_year__isnull=True)
                | Q(consecration_year__gte=1900, consecration_year__lte=2100),
                name="minister_consecration_year_range",
            ),
        ]
        indexes = [
            models.Index(fields=["full_name"], name="minister_nome_idx"),
            models.Index(fields=["is_active", "full_name"], name="minister_ativo_nome_idx"),
        ]

    def __str__(self):
        return "{} ({})".format(self.full_name, self.conamad_registration)

    def save(self, *args, **kwargs):
        # Normaliza tambem quando o objeto e gravado fora dos servicos
        # (shell, fixtures). Os servicos ja entregam o valor limpo.
        self.full_name = normalizar_nome(self.full_name)
        self.conamad_registration = normalizar_registro(self.conamad_registration)
        self.field_registration = normalizar_registro(self.field_registration)
        self.position = normalizar_nome(self.position)
        self.congregation = normalizar_nome(self.congregation)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "Ministro nao e excluido pela aplicacao. Use a inativacao, que "
            "preserva o historico."
        )

    @property
    def cargo_exibido(self):
        """O que o portal e a guia chamam de "Cargo"."""
        return self.position or self.get_ministerial_title_display()

    @property
    def primeiro_nome(self):
        return self.full_name.split(" ")[0] if self.full_name else ""


class MinisterAccount(models.Model):
    """
    Credencial do Portal do Ministro.

    Separada de accounts.User de proposito. Um User carrega papel, permissao
    e o caminho para /admin-panel/ e /django-admin/; nada disso pode existir
    para um ministro, e a forma mais segura de garantir isso e ele nunca ter
    um User.

    A senha so existe como hash, gerado pelos mesmos hashers do Django
    (make_password/check_password). O login_identifier e a inscricao CONAMAD
    do ministro, mantida em sincronia pelo servico quando ela e editada.
    """

    minister = models.OneToOneField(
        Minister,
        verbose_name="ministro",
        on_delete=models.PROTECT,
        related_name="account",
    )
    login_identifier = models.CharField(
        "identificador de login",
        max_length=TAMANHO_MAXIMO_REGISTRO,
        unique=True,
    )
    password_hash = models.CharField("hash da senha", max_length=128)

    is_active = models.BooleanField(
        "acesso liberado",
        default=True,
        db_index=True,
        help_text="Desligado, o ministro não entra no portal. O cadastro continua ativo.",
    )
    must_change_password = models.BooleanField(
        "precisa trocar a senha",
        default=False,
        help_text="Ligado para contas criadas com a senha padrão da importação.",
    )

    last_login = models.DateTimeField("último acesso", null=True, blank=True)
    password_changed_at = models.DateTimeField(
        "senha alterada em", null=True, blank=True
    )

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "acesso de ministro"
        verbose_name_plural = "acessos de ministros"
        ordering = ["login_identifier"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(login_identifier=""),
                name="minister_account_login_required",
            ),
            models.CheckConstraint(
                condition=~Q(password_hash=""),
                name="minister_account_password_required",
            ),
        ]

    def __str__(self):
        return "Acesso de {}".format(self.login_identifier)

    def save(self, *args, **kwargs):
        self.login_identifier = normalizar_registro(self.login_identifier)
        return super().save(*args, **kwargs)

    def set_password(self, senha):
        self.password_hash = make_password(senha)
        self.password_changed_at = timezone.now()

    def set_password_hash(self, hash_pronto):
        """
        Usa um hash ja calculado.

        Existe para a importacao: todas as contas novas recebem a mesma senha
        padrao, e calcular PBKDF2 com um milhao de iteracoes para cada uma
        transformaria uma carga de oitenta ministros numa requisicao de um
        minuto. Um salt por conta nao protege nada quando a senha e a mesma
        para todas e conhecida do grupo; a troca obrigatoria no primeiro
        acesso e que da a cada conta o seu proprio hash.
        """
        self.password_hash = hash_pronto
        self.password_changed_at = timezone.now()

    def check_password(self, senha):
        def atualizar_hash(senha_correta):
            # Chamado pelo Django quando o hasher ou o numero de iteracoes
            # muda: regrava o hash no formato atual sem mexer na data de
            # troca, que registra uma decisao do ministro.
            self.password_hash = make_password(senha_correta)
            self.save(update_fields=["password_hash", "updated_at"])

        return check_password(senha, self.password_hash, setter=atualizar_hash)

    def session_auth_hash(self):
        """
        Assinatura da senha vigente, guardada na sessao do portal.

        Mesma ideia do get_session_auth_hash do Django: quando a senha muda,
        a assinatura muda, e toda sessao aberta com a senha anterior deixa de
        valer no request seguinte.
        """
        return salted_hmac(
            "ministers.MinisterAccount.session_auth_hash",
            self.password_hash,
            algorithm="sha256",
        ).hexdigest()

    @property
    def pode_entrar(self):
        return self.is_active and self.minister.is_active


# ---------------------------------------------------------------------------
# Mensalidades (Fase 2A)
# ---------------------------------------------------------------------------


class MonthlyFeeStatus(models.TextChoices):
    """
    Situacao GRAVADA da mensalidade.

    "Vencido" nao esta aqui de proposito: e uma situacao CALCULADA (pendente
    com vencimento no passado), e nao um estado gravado. Gravar exigiria um
    processo agendado virando PENDING em OVERDUE todo dia — e, no dia em que
    ele falhasse, o painel mostraria como em dia quem esta atrasado. Calculado
    na leitura, o vencido nunca fica desatualizado, e uma mensalidade PAGA
    nunca aparece como vencida, porque so PENDING pode vencer.
    """

    PENDING = "PENDING", "Pendente"
    PAID = "PAID", "Pago"
    EXEMPT = "EXEMPT", "Isento"
    CANCELLED = "CANCELLED", "Cancelado"


class FeeSituation(models.TextChoices):
    """Situacao EXIBIDA: as gravadas mais o vencido calculado."""

    PENDING = "PENDING", "Pendente"
    OVERDUE = "OVERDUE", "Vencido"
    PAID = "PAID", "Pago"
    EXEMPT = "EXEMPT", "Isento"
    CANCELLED = "CANCELLED", "Cancelado"


class PaymentMethod(models.TextChoices):
    """Formas de pagamento. Uma forma nova e uma linha aqui e uma migration
    que so altera choices — nenhuma coluna muda."""

    PIX = "PIX", "Pix"
    CASH = "CASH", "Dinheiro"
    TRANSFER = "TRANSFER", "Transferência"
    OTHER = "OTHER", "Outro"


def primeiro_dia(data):
    return data.replace(day=1)


class MinisterMonthlyFee(models.Model):
    """
    Uma mensalidade de um ministro numa competencia.

    Entidade propria: Minister nao carrega nenhum campo financeiro.

    Competencia
    -----------
    Gravada como DATA, sempre no dia 1 do mes (2026-10-01 e a competencia
    10/2026). Data, e nao texto: ordena, filtra por intervalo e nao admite
    "Out/26" ou "10-2026". O dia 1 e garantido por constraint no banco, e a
    unicidade ministro + competencia tambem.

    Valor
    -----
    Decimal com duas casas, nunca float. Cada mensalidade guarda o proprio
    valor: ministros e competencias podem ter valores diferentes, e mudar o
    valor de amanha nao reescreve o de ontem. Nao ha valor padrao no codigo.

    Nunca e excluida: cancelar e um estado, com motivo, e o historico de cada
    mudanca fica na trilha de auditoria.
    """

    minister = models.ForeignKey(
        Minister,
        verbose_name="ministro",
        on_delete=models.PROTECT,
        related_name="monthly_fees",
    )
    reference_month = models.DateField(
        "competência", help_text="Sempre o dia 1 do mês da competência."
    )
    due_date = models.DateField("vencimento")
    amount = models.DecimalField("valor", max_digits=10, decimal_places=2)

    status = models.CharField(
        "situação",
        max_length=10,
        choices=MonthlyFeeStatus.choices,
        default=MonthlyFeeStatus.PENDING,
        db_index=True,
    )
    status_reason = models.TextField(
        "motivo da situação",
        blank=True,
        help_text="Motivo do último cancelamento, isenção, estorno ou reativação.",
    )

    paid_at = models.DateField("data do pagamento", null=True, blank=True)
    payment_method = models.CharField(
        "forma de pagamento", max_length=10, choices=PaymentMethod.choices, blank=True
    )
    payment_reference = models.CharField(
        "referência / transação", max_length=120, blank=True
    )
    notes = models.TextField("observações", blank=True)

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "mensalidade de ministro"
        verbose_name_plural = "mensalidades de ministros"
        ordering = ["-reference_month", "minister__full_name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["minister", "reference_month"],
                name="minister_fee_unica_por_competencia",
                violation_error_message="Este ministro já tem mensalidade nesta competência.",
            ),
            models.CheckConstraint(
                condition=Q(reference_month__day=1),
                name="minister_fee_competencia_no_dia_1",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="minister_fee_valor_positivo",
            ),
            # Pago tem data e forma; o que nao e pago nao carrega dado de
            # pagamento. Um estorno que esquecesse de limpar a data deixaria
            # uma mensalidade pendente "paga em 05/10" — o banco recusa.
            models.CheckConstraint(
                condition=(
                    Q(status="PAID", paid_at__isnull=False) & ~Q(payment_method="")
                )
                | (
                    ~Q(status="PAID")
                    & Q(paid_at__isnull=True, payment_method="", payment_reference="")
                ),
                name="minister_fee_pagamento_coerente",
            ),
        ]
        indexes = [
            models.Index(
                fields=["reference_month", "status"], name="minister_fee_comp_status_idx"
            ),
            models.Index(fields=["status", "due_date"], name="minister_fee_status_venc_idx"),
        ]

    def __str__(self):
        return "Mensalidade {} de {}".format(self.competencia_rotulo, self.minister_id)

    def save(self, *args, **kwargs):
        if self.reference_month:
            self.reference_month = primeiro_dia(self.reference_month)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "Mensalidade nao e excluida pela aplicacao. Use o cancelamento, "
            "que preserva o historico."
        )

    @property
    def competencia_rotulo(self):
        """10/2026."""
        return self.reference_month.strftime("%m/%Y") if self.reference_month else ""

    @property
    def competencia_chave(self):
        """2026-10: o formato da competencia em URL e filtro."""
        return self.reference_month.strftime("%Y-%m") if self.reference_month else ""

    def situacao_em(self, hoje):
        if self.status == MonthlyFeeStatus.PENDING and self.due_date < hoje:
            return FeeSituation.OVERDUE
        return FeeSituation(self.status)

    @property
    def situacao(self):
        return self.situacao_em(timezone.localdate())


class MinisterFeeBatch(models.Model):
    """
    Uma geracao de mensalidades em massa (Fase 2B).

    Existe para que a geracao seja idempotente de verdade. O token nasce no
    preview e e UNICO aqui: reenviar a confirmacao (clique duplo, F5, POST
    repetido) encontra o lote ja gravado e devolve o MESMO resultado, em vez
    de rodar de novo e mostrar "0 criadas" por cima do resultado real.

    Guarda os parametros e os numeros do resultado; a lista de ministros nao,
    porque cada mensalidade criada tem o proprio evento na trilha, com o id
    deste lote.
    """

    token = models.CharField("identificador do lote", max_length=64, unique=True)
    reference_month = models.DateField("competência")
    due_date = models.DateField("vencimento")
    amount = models.DecimalField("valor unitário", max_digits=10, decimal_places=2)

    created_count = models.PositiveIntegerField("mensalidades criadas", default=0)
    existing_count = models.PositiveIntegerField("já existentes", default=0)
    inactive_count = models.PositiveIntegerField("inativos ignorados", default=0)
    charge_count = models.PositiveIntegerField(
        "cobranças geradas",
        default=0,
        help_text="Zero quando o pagamento ainda não estava configurado.",
    )
    total_amount = models.DecimalField(
        "valor total criado", max_digits=14, decimal_places=2, default=0
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="administrador",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField("gerado em", auto_now_add=True)

    class Meta:
        verbose_name = "geração de mensalidades"
        verbose_name_plural = "gerações de mensalidades"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(reference_month__day=1),
                name="minister_fee_batch_competencia_no_dia_1",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="minister_fee_batch_valor_positivo",
            ),
        ]

    def __str__(self):
        return "Geração {} ({})".format(self.reference_month.strftime("%m/%Y"), self.pk)

    def delete(self, *args, **kwargs):
        raise ValueError("Lote de geracao nao e excluido: ele e o recibo da operacao.")

    @property
    def competencia_chave(self):
        return self.reference_month.strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Cobranca Pix (Fase 2D)
# ---------------------------------------------------------------------------


class PixKeyType(models.TextChoices):
    """Tipo da chave: so para validar e normalizar o que o administrador digita."""

    CPF = "CPF", "CPF"
    CNPJ = "CNPJ", "CNPJ"
    EMAIL = "EMAIL", "E-mail"
    TELEFONE = "TELEFONE", "Telefone"
    ALEATORIA = "ALEATORIA", "Chave aleatória"


class MinisterBillingSettings(models.Model):
    """
    Dados de recebimento das mensalidades. Uma linha so (id 1).

    A chave Pix nao e segredo — ela vai impressa em cada guia —, mas tambem
    nao vive no codigo nem em migration: quem a informa e o administrador,
    por tela, e cada alteracao fica na trilha com antes e depois.

    Mudar esta configuracao vale para as cobrancas que forem geradas DEPOIS.
    As ja geradas copiaram os dados para o proprio snapshot
    (MinisterFeeCharge) e nao leem mais daqui.
    """

    pix_key_type = models.CharField(
        "tipo da chave Pix", max_length=10, choices=PixKeyType.choices
    )
    pix_key = models.CharField("chave Pix", max_length=77)
    pix_receiver_name = models.CharField(
        "favorecido",
        max_length=25,
        help_text="Como aparece no aplicativo do banco. Até 25 caracteres, sem acento.",
    )
    pix_receiver_city = models.CharField(
        "cidade",
        max_length=15,
        help_text="Cidade do favorecido. Até 15 caracteres, sem acento.",
    )
    payment_instructions = models.TextField(
        "orientações",
        blank=True,
        help_text="Aparecem na cobrança do portal e na guia em PDF.",
    )
    is_active = models.BooleanField(
        "cobrança por Pix ativa",
        default=True,
        help_text="Desligada, nenhuma cobrança nova é gerada. As já geradas continuam valendo.",
    )

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="atualizado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "configuração de pagamento"
        verbose_name_plural = "configuração de pagamento"
        constraints = [
            models.CheckConstraint(
                condition=Q(id=1), name="minister_billing_settings_linha_unica"
            ),
        ]

    def __str__(self):
        return "Configuração de pagamento"


class MinisterFeeCharge(models.Model):
    """
    A cobranca Pix de uma mensalidade: um SNAPSHOT imutavel.

    Tudo o que a guia e a tela de cobranca mostram esta copiado aqui no
    momento da geracao — valor, vencimento, chave, favorecido, cidade e os
    dados do ministro. Mudar a configuracao, o cadastro do ministro ou o
    valor padrao de amanha nao reescreve a cobranca de hoje: o PDF de
    Outubro reconstruido daqui a um ano e o mesmo PDF.

    O Pix Copia e Cola e gravado pronto (pix_payload). O QR Code e desenhado
    a partir dele, sempre, e nunca e montado de novo a partir dos outros
    campos.

    Imutavel na aplicacao: save() recusa atualizar e delete() recusa
    excluir. A unica mudanca possivel e marcar a cobranca como SUBSTITUIDA
    (superseded_at), por queryset.update em ministers.cobranca, quando o
    administrador altera o valor ou o vencimento de uma mensalidade
    pendente. A substituida continua no banco, com o mesmo TXID, como
    historico; a vigente e a unica sem superseded_at — o banco garante.
    """

    fee = models.ForeignKey(
        MinisterMonthlyFee,
        verbose_name="mensalidade",
        on_delete=models.PROTECT,
        related_name="charges",
    )
    txid = models.CharField("TXID", max_length=25, unique=True)

    reference_month_snapshot = models.DateField("competência")
    amount_snapshot = models.DecimalField("valor", max_digits=10, decimal_places=2)
    due_date_snapshot = models.DateField("vencimento")

    pix_key_snapshot = models.CharField("chave Pix", max_length=77)
    pix_receiver_name_snapshot = models.CharField("favorecido", max_length=25)
    pix_receiver_city_snapshot = models.CharField("cidade", max_length=15)
    payment_instructions_snapshot = models.TextField("orientações", blank=True)

    minister_name_snapshot = models.CharField("nome do ministro", max_length=200)
    conamad_registration_snapshot = models.CharField(
        "inscrição CONAMAD", max_length=TAMANHO_MAXIMO_REGISTRO
    )
    field_registration_snapshot = models.CharField(
        "matrícula do campo", max_length=TAMANHO_MAXIMO_REGISTRO, blank=True
    )
    congregation_snapshot = models.CharField("congregação", max_length=120, blank=True)
    position_snapshot = models.CharField("cargo", max_length=100, blank=True)

    pix_payload = models.TextField("Pix Copia e Cola")

    generated_at = models.DateTimeField("gerada em", auto_now_add=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="gerada por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    superseded_at = models.DateTimeField("substituída em", null=True, blank=True)

    class Meta:
        verbose_name = "cobrança de mensalidade"
        verbose_name_plural = "cobranças de mensalidades"
        ordering = ["-generated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["fee"],
                condition=Q(superseded_at__isnull=True),
                name="minister_charge_uma_vigente_por_mensalidade",
            ),
            models.CheckConstraint(
                condition=Q(amount_snapshot__gt=0),
                name="minister_charge_valor_positivo",
            ),
            models.CheckConstraint(
                condition=Q(reference_month_snapshot__day=1),
                name="minister_charge_competencia_no_dia_1",
            ),
            models.CheckConstraint(
                condition=~Q(pix_payload="") & ~Q(txid=""),
                name="minister_charge_payload_obrigatorio",
            ),
        ]

    def __str__(self):
        return "Cobrança {}".format(self.txid)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Cobranca e um snapshot: nao e alterada depois de gerada.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Cobranca nao e excluida: ela e o historico do que foi cobrado.")

    @property
    def vigente(self):
        return self.superseded_at is None


# ---------------------------------------------------------------------------
# Comprovantes de pagamento (Fase 2E)
# ---------------------------------------------------------------------------

# 5 MB. Um print de tela do aplicativo do banco tem menos de 1 MB; uma foto
# de celular de um comprovante em papel, 2 a 4 MB. O Nginx do host aceita
# 6 MB por requisicao, o que deixa folga para o envelope multipart.
TAMANHO_MAXIMO_DO_COMPROVANTE = 5 * 1024 * 1024

# O tipo gravado e o que a aplicacao ENCONTROU dentro do arquivo, nunca o que
# o navegador declarou. A extensao do nome interno sai daqui tambem.
EXTENSAO_DO_COMPROVANTE = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

PASTA_DOS_COMPROVANTES = "ministers/payment_proofs"


def armazenamento_dos_comprovantes():
    """
    O mesmo MEDIA_ROOT do resto da aplicacao, com permissoes mais fechadas.

    0640/0750: o dono (o usuario da aplicacao) le e grava, o grupo le — o
    backup de media roda como o mesmo usuario —, e mais ninguem ve nada.
    Nenhum servidor web serve este diretorio: o arquivo so sai por uma view
    autenticada (ver config/settings.py, "Arquivos enviados").
    """
    from django.core.files.storage import FileSystemStorage

    return FileSystemStorage(file_permissions_mode=0o640, directory_permissions_mode=0o750)


def caminho_do_comprovante(instancia, nome_enviado):
    """
    ministers/payment_proofs/<32 hex aleatorios>.<pdf|jpg|png>.

    O nome que veio do navegador nao entra no caminho — nem sanitizado. Ele
    fica so em original_filename, para a secretaria reconhecer o arquivo. A
    extensao vem do tipo detectado no conteudo.
    """
    import uuid

    return "{}/{}{}".format(
        PASTA_DOS_COMPROVANTES,
        uuid.uuid4().hex,
        EXTENSAO_DO_COMPROVANTE.get(instancia.mime_type, ""),
    )


class ProofStatus(models.TextChoices):
    """
    Situacao do COMPROVANTE. Nao se mistura com a da mensalidade.

    Enquanto ha um SUBMITTED, a mensalidade continua PENDING — financeiramente
    em aberto. "Aguardando analise" no portal e derivado daqui, e nao um
    status novo da mensalidade.
    """

    SUBMITTED = "SUBMITTED", "Aguardando análise"
    APPROVED = "APPROVED", "Aprovado"
    REJECTED = "REJECTED", "Recusado"


class MinisterPaymentProof(models.Model):
    """
    Um comprovante enviado pelo ministro para uma mensalidade.

    Cada envio e uma linha nova. Recusado nao e apagado nem sobrescrito: o
    reenvio cria outro comprovante, e o historico da mensalidade mostra os
    dois. delete() recusa.

    Um so SUBMITTED por mensalidade — o banco garante com constraint parcial;
    o servico (ministers.comprovantes) confere antes, com a mensalidade
    travada.

    O arquivo fica em MEDIA_ROOT com nome aleatorio e so sai por view
    autenticada. sha256, tamanho e tipo sao calculados no servidor no envio.
    """

    fee = models.ForeignKey(
        MinisterMonthlyFee,
        verbose_name="mensalidade",
        on_delete=models.PROTECT,
        related_name="payment_proofs",
    )

    file = models.FileField(
        "arquivo",
        upload_to=caminho_do_comprovante,
        storage=armazenamento_dos_comprovantes,
        max_length=200,
    )
    original_filename = models.CharField("nome original", max_length=120, blank=True)
    mime_type = models.CharField("tipo", max_length=30)
    file_size = models.PositiveIntegerField("tamanho (bytes)")
    sha256 = models.CharField("SHA-256", max_length=64, db_index=True)

    status = models.CharField(
        "situação",
        max_length=10,
        choices=ProofStatus.choices,
        default=ProofStatus.SUBMITTED,
        db_index=True,
    )
    submitted_at = models.DateTimeField("enviado em", default=timezone.now)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="analisado por",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField("analisado em", null=True, blank=True)
    rejection_reason = models.TextField("motivo da recusa", blank=True)

    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "comprovante de pagamento"
        verbose_name_plural = "comprovantes de pagamento"
        ordering = ["-submitted_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["fee"],
                condition=Q(status="SUBMITTED"),
                name="minister_proof_um_em_analise_por_mensalidade",
                violation_error_message="Esta mensalidade já tem um comprovante aguardando análise.",
            ),
            # Aguardando: sem analise. Aprovado: analisado, sem motivo.
            # Recusado: analisado E com motivo. Uma recusa sem motivo deixaria
            # o ministro sem saber o que corrigir.
            models.CheckConstraint(
                condition=(
                    Q(
                        status="SUBMITTED",
                        reviewed_at__isnull=True,
                        reviewed_by__isnull=True,
                        rejection_reason="",
                    )
                    | Q(status="APPROVED", reviewed_at__isnull=False, rejection_reason="")
                    | (Q(status="REJECTED", reviewed_at__isnull=False) & ~Q(rejection_reason=""))
                ),
                name="minister_proof_analise_coerente",
            ),
            models.CheckConstraint(
                condition=Q(file_size__gt=0, file_size__lte=TAMANHO_MAXIMO_DO_COMPROVANTE),
                name="minister_proof_tamanho_aceito",
            ),
            models.CheckConstraint(
                condition=Q(mime_type__in=list(EXTENSAO_DO_COMPROVANTE)),
                name="minister_proof_tipo_aceito",
            ),
            models.CheckConstraint(
                condition=Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="minister_proof_sha256_valido",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "submitted_at"], name="minister_proof_fila_idx"),
        ]

    def __str__(self):
        return "Comprovante {} ({})".format(self.pk, self.get_status_display())

    def delete(self, *args, **kwargs):
        raise ValueError(
            "Comprovante nao e excluido: recusado continua no historico da mensalidade."
        )

    @property
    def e_imagem(self):
        return self.mime_type.startswith("image/")
