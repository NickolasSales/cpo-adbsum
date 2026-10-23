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
