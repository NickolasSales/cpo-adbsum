from django.apps import AppConfig


class MinistersConfig(AppConfig):
    """
    Controle de Ministros.

    Dominio separado do CPO academico. Compartilha com ele apenas o que e
    infraestrutura: o banco, o painel administrativo, a trilha de auditoria e
    a identidade visual. Nenhum modelo daqui aponta para Module, Enrollment,
    Exam, ExamAttempt ou Certificate, e nenhum modelo de la aponta para ca.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "ministers"
    verbose_name = "Controle de Ministros"

    def ready(self):
        # Mantem a sessao do portal e a sessao do CPO mutuamente exclusivas.
        from ministers import signals  # noqa: F401
