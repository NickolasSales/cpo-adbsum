"""Registro de auditoria."""

from django.conf import settings
from django.db import models


class AuditEvent(models.TextChoices):
    """
    Eventos auditaveis.

    A lista cresce a cada etapa; eventos existentes nunca sao removidos nem
    renomeados, porque isso invalidaria a trilha ja gravada.
    """

    # Autenticacao (Etapa 1)
    LOGIN_SUCCESS = "LOGIN_SUCCESS", "Login realizado"
    LOGIN_FAILED = "LOGIN_FAILED", "Falha de login"
    PASSWORD_CHANGED = "PASSWORD_CHANGED", "Senha alterada"

    # Alunos (Etapa 2)
    STUDENT_CREATED = "STUDENT_CREATED", "Aluno criado"
    STUDENT_UPDATED = "STUDENT_UPDATED", "Aluno atualizado"
    STUDENT_BLOCKED = "STUDENT_BLOCKED", "Aluno bloqueado"
    STUDENT_UNBLOCKED = "STUDENT_UNBLOCKED", "Aluno desbloqueado"
    STUDENT_IMPORT_COMPLETED = "STUDENT_IMPORT_COMPLETED", "Importacao concluida"

    # Modulos (Etapa 2)
    MODULE_CREATED = "MODULE_CREATED", "Modulo criado"
    MODULE_UPDATED = "MODULE_UPDATED", "Modulo atualizado"
    MODULE_ENABLED = "MODULE_ENABLED", "Modulo ativado"
    MODULE_DISABLED = "MODULE_DISABLED", "Modulo desativado"

    # Matriculas (Etapa 2)
    ENROLLMENT_CREATED = "ENROLLMENT_CREATED", "Matricula criada"
    ENROLLMENT_REMOVED = "ENROLLMENT_REMOVED", "Matricula desativada"
    ENROLLMENT_REACTIVATED = "ENROLLMENT_REACTIVATED", "Matricula reativada"
    ENROLLMENT_BLOCKED = "ENROLLMENT_BLOCKED", "Acesso da matricula bloqueado"
    ENROLLMENT_UNBLOCKED = "ENROLLMENT_UNBLOCKED", "Acesso da matricula liberado"
    ENROLLMENT_COMPLETED = "ENROLLMENT_COMPLETED", "Matricula concluida"

    # Provas (Etapa 3)
    EXAM_CREATED = "EXAM_CREATED", "Prova criada"
    EXAM_UPDATED = "EXAM_UPDATED", "Prova atualizada"
    EXAM_PUBLISHED = "EXAM_PUBLISHED", "Prova publicada"
    EXAM_CLOSED = "EXAM_CLOSED", "Prova fechada"
    EXAM_DUPLICATED = "EXAM_DUPLICATED", "Prova duplicada"
    # Registram apenas o fato de a senha ter mudado. Nem a senha, nem o hash,
    # nem o comprimento entram na metadata.
    EXAM_PASSWORD_CHANGED = "EXAM_PASSWORD_CHANGED", "Senha da prova alterada"
    EXAM_PASSWORD_REMOVED = "EXAM_PASSWORD_REMOVED", "Senha da prova removida"

    QUESTION_CREATED = "QUESTION_CREATED", "Questao criada"
    QUESTION_UPDATED = "QUESTION_UPDATED", "Questao atualizada"
    QUESTION_DELETED = "QUESTION_DELETED", "Questao excluida"

    # Tentativas (Etapa 4)
    #
    # Sao tres eventos por tentativa inteira, e nao um por resposta salva. O
    # autosave dispara a cada clique do aluno e produziria milhares de linhas
    # que nao contam nada que o banco de respostas ja nao conte melhor.
    #
    # A metadata nunca carrega resposta, texto de redacao, gabarito, senha da
    # prova nem token publico. Os tokens sao o que o aluno usa para escrever
    # na tentativa: guarda-los na trilha seria espalhar credencial de escrita
    # por uma tabela que existe para ser lida.
    ATTEMPT_STARTED = "ATTEMPT_STARTED", "Tentativa iniciada"
    ATTEMPT_SUBMITTED = "ATTEMPT_SUBMITTED", "Tentativa enviada"
    ATTEMPT_EXPIRED = "ATTEMPT_EXPIRED", "Tentativa expirada"

    # Senha definida pelo administrador (Etapa 5)
    #
    # A metadata registra que houve reset, e nada mais. Senha, hash e ate o
    # comprimento ficam de fora: o comprimento e informacao util para quem
    # tenta adivinhar a senha e inutil para quem investiga o que aconteceu.
    STUDENT_PASSWORD_RESET = "STUDENT_PASSWORD_RESET", "Senha do aluno redefinida"

    # Correcao (Etapa 5)
    #
    # Tres eventos por tentativa corrigida, e nao um por questao avaliada. O
    # detalhe de cada questao ja fica em AttemptQuestion, com autor e data.
    #
    # MANUAL_GRADE_SAVED existe porque salvar rascunho e uma acao real do
    # avaliador que altera pontuacao — mas a metadata guarda a questao e os
    # pontos, nunca o texto da resposta nem o comentario.
    #
    # A nota final PODE aparecer em GRADING_COMPLETED: ela nao e segredo, e a
    # trilha precisa responder "que nota foi fechada, por quem e quando".
    GRADING_STARTED = "GRADING_STARTED", "Correcao iniciada"
    MANUAL_GRADE_SAVED = "MANUAL_GRADE_SAVED", "Nota manual registrada"
    GRADING_COMPLETED = "GRADING_COMPLETED", "Correcao finalizada"

    # Certificados (Etapa 6)
    #
    # Emissao e revogacao sao atos academicos: mudam o que a instituicao
    # afirma sobre uma pessoa. Os dois sao obrigatorios na trilha.
    #
    # Download nao gera evento de proposito. Um certificado carrega QR Code e
    # pode ser aberto por qualquer leitor, robo ou pre-visualizador de link;
    # auditar cada acesso encheria a trilha de ruido e tornaria mais dificil
    # encontrar os eventos que importam.
    CERTIFICATE_ISSUED = "CERTIFICATE_ISSUED", "Certificado emitido"
    CERTIFICATE_REVOKED = "CERTIFICATE_REVOKED", "Certificado revogado"

    # Compartilhamento (Etapa 8)
    #
    # LEIA O NOME COM CUIDADO: "iniciado", e nada mais forte.
    #
    # O evento e gravado quando o aluno aperta o botao. A partir dali o
    # sistema perde a visao: quem entrega e o WhatsApp ou a folha de
    # compartilhamento do celular, nenhum dos dois devolve confirmacao, e o
    # aluno pode fechar tudo sem enviar para ninguem.
    #
    # Ou seja, este evento NAO significa mensagem enviada, entregue nem lida.
    # Significa que a intencao existiu. Interpretar como entrega — num
    # relatorio, numa conversa, numa cobranca — seria afirmar algo que este
    # sistema nao tem como saber.
    #
    # A metadata guarda so o canal. Nem a mensagem, nem a URL, nem o nome do
    # aluno: os tres ja estao em outros campos ou em outras tabelas, e
    # repeti-los aqui seria espalhar dado pessoal por uma tabela que so
    # cresce.
    CERTIFICATE_SHARE_INITIATED = (
        "CERTIFICATE_SHARE_INITIATED",
        "Compartilhamento de certificado iniciado",
    )

    # Contas administrativas (Etapa 7)
    #
    # Quem cria, edita, bloqueia ou redefine a senha de um administrador esta
    # mexendo em quem pode mexer em tudo. Sao os eventos de maior peso da
    # trilha, e o motivo de ela existir.
    ADMIN_USER_CREATED = "ADMIN_USER_CREATED", "Administrador criado"
    ADMIN_USER_UPDATED = "ADMIN_USER_UPDATED", "Administrador atualizado"
    ADMIN_USER_BLOCKED = "ADMIN_USER_BLOCKED", "Administrador bloqueado"
    ADMIN_USER_UNBLOCKED = "ADMIN_USER_UNBLOCKED", "Administrador desbloqueado"
    ADMIN_PASSWORD_RESET = "ADMIN_PASSWORD_RESET", "Senha de administrador redefinida"

    # Reset de tentativa (Etapa 7)
    #
    # Anula uma tentativa preservando o historico dela. Pode revogar
    # certificado e reativar matricula, entao a metadata registra o que de
    # fato aconteceu — sem repetir o motivo, que ja fica em
    # ExamAttempt.reset_reason.
    ATTEMPT_RESET = "ATTEMPT_RESET", "Tentativa anulada"

    # Gestao operacional (Etapa 9)
    #
    # EXAM_DELETED e o unico evento da trilha que descreve uma linha que nao
    # existe mais. Por isso ele e gravado ANTES do DELETE, dentro da mesma
    # transacao: se a exclusao falhar, o evento tambem desaparece no rollback,
    # e a trilha nunca afirma uma exclusao que nao aconteceu.
    #
    # A metadata leva titulo, versao e modulo — o suficiente para responder
    # "o que foi apagado". Questoes, alternativas e gabarito ficam de fora: a
    # trilha existe para registrar o ato, e nao para ser um backup obliquo do
    # conteudo da prova.
    EXAM_DELETED = "EXAM_DELETED", "Prova excluida"
    EXAM_ARCHIVED = "EXAM_ARCHIVED", "Prova arquivada"
    EXAM_UNARCHIVED = "EXAM_UNARCHIVED", "Prova desarquivada"

    # Matriculas revogadas, apagadas e restauradas.
    #
    # ENROLLMENT_REVOKED nao e o mesmo que ENROLLMENT_REMOVED, da Etapa 2.
    # Desativar e uma pausa operacional; revogar e um ato administrativo que
    # encerra o vinculo academico e exige motivo escrito.
    ENROLLMENT_REVOKED = "ENROLLMENT_REVOKED", "Matricula revogada"
    ENROLLMENT_DELETED = "ENROLLMENT_DELETED", "Matricula excluida"
    ENROLLMENT_RESTORED = "ENROLLMENT_RESTORED", "Matricula restaurada"

    # Limpeza de dados de homologacao (Etapa 9)
    #
    # Excecao declarada, e nao um caminho normal: apaga tentativas, respostas
    # e certificados de teste antes do piloto. Quem executa e um comando de
    # gestao, sem sessao web, entao o autor fica nulo — e por isso mesmo a
    # metadata precisa dizer o que foi removido e de quem.
    #
    # O evento e gravado dentro da mesma transacao da remocao. A propria
    # trilha nunca e tocada pela limpeza.
    HOMOLOGATION_DATA_PURGED = (
        "HOMOLOGATION_DATA_PURGED",
        "Dados de homologacao removidos",
    )

    # Modelos de certificado (Etapa 10)
    #
    # Quem mexe no modelo define a aparencia de todo documento oficial
    # emitido dali para a frente. Ativar um modelo novo tem alcance
    # institucional, e por isso e um evento proprio e nao um "atualizado".
    #
    # A metadata guarda nome, versao e o checksum da arte — o suficiente para
    # responder "que layout estava valendo naquele dia". O arquivo em si nao
    # entra: a trilha registra o ato, e nao guarda copia de imagem.
    CERTIFICATE_TEMPLATE_CREATED = (
        "CERTIFICATE_TEMPLATE_CREATED",
        "Modelo de certificado criado",
    )
    CERTIFICATE_TEMPLATE_UPDATED = (
        "CERTIFICATE_TEMPLATE_UPDATED",
        "Modelo de certificado atualizado",
    )
    CERTIFICATE_TEMPLATE_BACKGROUND_SET = (
        "CERTIFICATE_TEMPLATE_BACKGROUND_SET",
        "Arte do modelo enviada",
    )
    CERTIFICATE_TEMPLATE_ACTIVATED = (
        "CERTIFICATE_TEMPLATE_ACTIVATED",
        "Modelo de certificado ativado",
    )
    CERTIFICATE_TEMPLATE_ARCHIVED = (
        "CERTIFICATE_TEMPLATE_ARCHIVED",
        "Modelo de certificado arquivado",
    )
    CERTIFICATE_TEMPLATE_DUPLICATED = (
        "CERTIFICATE_TEMPLATE_DUPLICATED",
        "Modelo de certificado duplicado",
    )


class AuditLog(models.Model):
    """
    Trilha de auditoria, somente insercao.

    O modelo bloqueia atualizacao e exclusao na camada de aplicacao. Uma
    remocao por politica de retencao continua possivel no banco, de forma
    deliberada e fora do alcance da interface.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="autor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs_como_autor",
        help_text="Quem executou a acao. Nulo em falha de login.",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="aluno",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs_como_aluno",
        help_text="Aluno afetado, quando a acao recai sobre um aluno.",
    )

    event = models.CharField(
        "evento", max_length=64, choices=AuditEvent.choices, db_index=True
    )
    entity_type = models.CharField("tipo da entidade", max_length=64, blank=True)
    entity_id = models.CharField("id da entidade", max_length=64, blank=True)

    timestamp = models.DateTimeField("data e hora", auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField("endereco IP", null=True, blank=True)
    user_agent = models.TextField("user-agent", blank=True)
    metadata = models.JSONField("metadados", default=dict, blank=True)

    class Meta:
        verbose_name = "registro de auditoria"
        verbose_name_plural = "registros de auditoria"
        ordering = ["-timestamp", "-id"]
        indexes = [
            models.Index(fields=["event", "-timestamp"], name="audit_evento_data_idx"),
            models.Index(fields=["student", "-timestamp"], name="audit_aluno_data_idx"),
            models.Index(
                fields=["entity_type", "entity_id"], name="audit_entidade_idx"
            ),
        ]

    def __str__(self):
        return "{} em {:%d/%m/%Y %H:%M:%S}".format(self.event, self.timestamp)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError(
                "AuditLog e somente insercao: um registro existente nao pode "
                "ser alterado."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "AuditLog e somente insercao: um registro nao pode ser excluido "
            "pela aplicacao."
        )
