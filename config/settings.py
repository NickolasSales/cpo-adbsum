"""
Configuracao do projeto CPO Provas.

Estrategia: um unico modulo de settings, integralmente parametrizado por
variaveis de ambiente. Ver README para a justificativa da escolha.

Nenhum segredo pode ser escrito neste arquivo. Tudo que varia entre
desenvolvimento e producao vem de variaveis de ambiente, carregadas de um
arquivo .env em desenvolvimento e do ambiente do processo em producao.
"""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Carrega .env quando existir. Em producao as variaveis vem do systemd
# (EnvironmentFile), e a ausencia do arquivo e o comportamento esperado.
load_dotenv(BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# Helpers de leitura de ambiente
# ---------------------------------------------------------------------------

def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(
            "A variavel de ambiente obrigatoria {} nao esta definida. "
            "Copie .env.example para .env e preencha os valores.".format(name)
        )
    return value


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def env_list(name, default=""):
    raw = os.environ.get(name, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Seguranca basica
# ---------------------------------------------------------------------------

SECRET_KEY = env("SECRET_KEY", required=True)
DEBUG = env_bool("DEBUG", default=False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")


# ---------------------------------------------------------------------------
# Configuracao institucional da aplicacao
# ---------------------------------------------------------------------------

SITE_URL = env("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
INSTITUTION_NAME = env("INSTITUTION_NAME", "CPO")
DEFAULT_STUDENT_PASSWORD = env("DEFAULT_STUDENT_PASSWORD", "")

# Quando True, o IP do cliente e lido de X-Forwarded-For. Deve permanecer
# False ate que exista um proxy reverso confiavel (Nginx) na frente da
# aplicacao. Sem proxy, esse cabecalho e falsificavel por qualquer cliente.
TRUST_PROXY_HEADERS = env_bool("TRUST_PROXY_HEADERS", default=False)


# ---------------------------------------------------------------------------
# Aplicacoes
# ---------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

LOCAL_APPS = [
    "common",
    "accounts",
    "audit",
    "students",
    "courses",
    "exams",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Precisa vir depois de AuthenticationMiddleware: depende de request.user.
    "accounts.middleware.MustChangePasswordMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "common.context_processors.institution",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# ---------------------------------------------------------------------------
# Banco de dados
#
# Exclusivamente PostgreSQL, exclusivamente via DATABASE_URL. Nao existe
# fallback para SQLite: a ausencia da variavel deve falhar de forma ruidosa,
# nunca degradar em silencio para um banco diferente do de producao.
# ---------------------------------------------------------------------------

DATABASE_URL = env("DATABASE_URL", required=True)

DATABASES = {
    "default": dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=int(env("DB_CONN_MAX_AGE", "60")),
        conn_health_checks=True,
    )
}

if not DATABASES["default"].get("ENGINE", "").endswith("postgresql"):
    raise ImproperlyConfigured(
        "DATABASE_URL deve apontar para um PostgreSQL. "
        "O projeto nao suporta outro banco em nenhum ambiente."
    )

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Autenticacao
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "common:root"
LOGOUT_REDIRECT_URL = "accounts:login"


# ---------------------------------------------------------------------------
# Sessao
#
# A duracao da sessao precisa ser confortavelmente maior que a maior prova
# possivel. Uma sessao expirando no meio de uma avaliacao seria perda de
# trabalho do aluno. 12 horas cobre qualquer prova realista com folga.
# ---------------------------------------------------------------------------

SESSION_COOKIE_AGE = int(env("SESSION_COOKIE_AGE", str(12 * 60 * 60)))
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_SAMESITE = "Lax"


# ---------------------------------------------------------------------------
# Internacionalizacao
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Arquivos estaticos
#
# Em desenvolvimento o proprio Django serve static/. Em producao o Nginx
# servira STATIC_ROOT diretamente do disco, apos collectstatic. Por isso o
# projeto nao usa WhiteNoise: ele seria uma camada Python desnecessaria no
# caminho de cada arquivo, havendo um servidor web na frente.
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
        )
    },
}

# messages.ERROR usa a classe "danger" no Bootstrap.
MESSAGE_TAGS = {40: "danger"}


# ---------------------------------------------------------------------------
# Endurecimento aplicado somente fora de desenvolvimento
#
# Nada aqui pode quebrar o ambiente local. Todos os ajustes ficam sob
# DEBUG=False, que e o estado de producao.
# ---------------------------------------------------------------------------

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    # Os quatro ajustes abaixo tem padrao seguro e so podem ser afrouxados
    # por declaracao explicita no ambiente.
    #
    # Motivo de serem parametrizaveis: num deploy de fumaca acessado por IP,
    # sem dominio e sem certificado, um cookie marcado como Secure nunca e
    # devolvido pelo navegador e o login se torna impossivel. Sem esta valvula
    # a alternativa seria rodar o smoke test com DEBUG=True, que e pior:
    # ligaria a pagina de erro detalhada num host publico.
    #
    # A excecao pertence ao .env daquele ambiente, nunca ao codigo. Qualquer
    # ambiente que nao declare nada continua recebendo o comportamento seguro.
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", default=True)
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    # O TLS termina no Nginx; o Django precisa deste cabecalho para saber
    # que a requisicao original chegou por HTTPS.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# ---------------------------------------------------------------------------
# Logging
#
# Saida em console. Em producao o systemd captura stdout/stderr e entrega ao
# journald, entao nao ha necessidade de handler de arquivo.
#
# Nenhum logger deve receber senha, cookie, token, cabecalho Authorization,
# SECRET_KEY ou DATABASE_URL. O registro de auditoria passa por
# audit.services.record(), que sanitiza a metadata antes de persistir.
# ---------------------------------------------------------------------------

LOG_LEVEL = env("LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "padrao": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "padrao",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "cpo": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}
