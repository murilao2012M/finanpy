"""
Configuracoes centrais do projeto FinanPy.

Este arquivo concentra as definicoes que o Django usa para:
- saber quais apps fazem parte do projeto;
- onde estao templates e arquivos estaticos;
- qual banco de dados sera usado;
- como o login e o logout devem funcionar.
"""

import importlib.util
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured


# BASE_DIR aponta para a pasta raiz do projeto e facilita montar caminhos absolutos.
BASE_DIR = Path(__file__).resolve().parent.parent


def carregar_arquivo_env(caminho_env):
    """Carrega variaveis de um arquivo .env simples sem depender de bibliotecas externas."""
    if not caminho_env.exists():
        return

    for linha in caminho_env.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()

        if not linha or linha.startswith("#") or "=" not in linha:
            continue

        chave, valor = linha.split("=", 1)
        chave = chave.strip()
        valor = valor.strip().strip('"').strip("'")

        os.environ.setdefault(chave, valor)


carregar_arquivo_env(BASE_DIR / ".env")


def env_bool(nome_variavel, padrao=False):
    """Converte variaveis de ambiente em booleanos de forma simples."""
    valor = os.getenv(nome_variavel)
    if valor is None:
        return padrao
    return valor.strip().lower() in {"1", "true", "yes", "on"}


def env_list(nome_variavel, padrao=""):
    """Converte variaveis separadas por virgula em lista limpa."""
    valor = os.getenv(nome_variavel, padrao)
    return [item.strip() for item in valor.split(",") if item.strip()]


def pacote_instalado(nome_pacote):
    """Verifica se uma dependencia opcional esta disponivel no ambiente."""
    return importlib.util.find_spec(nome_pacote) is not None


def extrair_host_de_url(url):
    """Extrai o host de uma URL completa para reaproveitar em ALLOWED_HOSTS."""
    if not url:
        return ""

    return urlparse(url).hostname or ""


def database_config_from_url(database_url):
    """Monta a configuracao do banco a partir de DATABASE_URL."""
    url = urlparse(database_url)

    if url.scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(url.path.lstrip("/")),
            "USER": unquote(url.username or ""),
            "PASSWORD": unquote(url.password or ""),
            "HOST": url.hostname or "",
            "PORT": str(url.port or ""),
        }

    if url.scheme == "sqlite":
        caminho = unquote(url.path or "")
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": caminho or BASE_DIR / "db.sqlite3",
        }

    raise ImproperlyConfigured("DATABASE_URL precisa usar postgres://, postgresql:// ou sqlite://.")


# DEBUG=True deixa mensagens de erro mais detalhadas.
# Isso ajuda no aprendizado, mas nao deve ser usado em producao.
DEBUG = env_bool("DJANGO_DEBUG", True)

# Em desenvolvimento usamos uma chave didatica.
# Em producao, a chave precisa vir obrigatoriamente do ambiente.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-finanpy-projeto-didatico"
    else:
        raise ImproperlyConfigured("Defina DJANGO_SECRET_KEY em producao.")

# Durante o desenvolvimento local, aceitamos hosts comuns.
# Em producao, a variavel DJANGO_ALLOWED_HOSTS deve ser definida.
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")

render_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
if render_hostname and render_hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(render_hostname)

site_hostname = extrair_host_de_url(os.getenv("FINANPY_SITE_URL", ""))
if site_hostname and site_hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(site_hostname)


# Apps internos do Django e nosso app principal.
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "financeiro",
]


# Middlewares sao camadas que processam requisicoes e respostas.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if pacote_instalado("whitenoise"):
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

# Arquivo principal de rotas do projeto.
ROOT_URLCONF = "finanpy.urls"


# Configuracao do mecanismo de templates do Django.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Aqui registramos a pasta global "templates" da raiz do projeto.
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "financeiro.context_processors.plano_usuario_context",
            ],
        },
    },
]

# Interface entre Django e servidores WSGI.
WSGI_APPLICATION = "finanpy.wsgi.application"


# Banco de dados.
# Localmente, o FinanPy usa SQLite.
# Em producao, basta definir DATABASE_URL com a URL interna do PostgreSQL.
DATABASE_URL = os.getenv("DATABASE_URL", "")
DATABASES = {
    "default": database_config_from_url(DATABASE_URL)
    if DATABASE_URL
    else {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# Validacoes basicas de senha fornecidas pelo proprio Django.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Idioma e fuso configurados para uso em portugues do Brasil.
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True


# Arquivos estaticos, como CSS proprio do projeto.
STATIC_URL = os.getenv("DJANGO_STATIC_URL", "/static/")
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

if pacote_instalado("whitenoise") and not DEBUG:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

# Arquivos enviados pelo usuario, como a foto do perfil.
MEDIA_URL = os.getenv("DJANGO_MEDIA_URL", "/media/")
MEDIA_ROOT = Path(os.getenv("DJANGO_MEDIA_ROOT", BASE_DIR / "media"))

# Chave primaria padrao usando BigAutoField.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Quando o usuario fizer login, ele ira para o dashboard.
LOGIN_REDIRECT_URL = "dashboard"

# Quando uma view protegida exigir autenticacao, o Django vai primeiro ao cadastro.
LOGIN_URL = "login"

# Quando fizer logout, voltamos para a tela de login.
LOGOUT_REDIRECT_URL = "login"

# Regras simples de seguranca para o fluxo autenticado.
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Configuracoes de HTTPS/SSL.
# Importante:
# - O certificado SSL real precisa ser configurado no provedor, proxy ou servidor web.
# - Estas opcoes deixam o Django pronto para trabalhar atras desse HTTPS.
ENABLE_HTTPS = env_bool("DJANGO_ENABLE_HTTPS", not DEBUG)

if ENABLE_HTTPS:
    # Se o projeto estiver atras de um proxy como Nginx, Render ou Railway,
    # esta cabecalho informa ao Django que a requisicao original chegou via HTTPS.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # Redireciona automaticamente qualquer acesso HTTP para HTTPS.
    SECURE_SSL_REDIRECT = True

    # Cookies so trafegam em conexoes HTTPS.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # HSTS avisa o navegador para preferir HTTPS nos proximos acessos.
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", True)
else:
    # Mantemos a navegacao local simples durante o desenvolvimento.
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False


# Hosts confiaveis para formularios e CSRF em producao HTTPS.
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

site_url = os.getenv("FINANPY_SITE_URL", "")
if site_url.startswith("https://") and site_url not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append(site_url)


# Credenciais da cobranca recorrente com Mercado Pago.
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "")
MERCADO_PAGO_PUBLIC_KEY = os.getenv("MERCADO_PAGO_PUBLIC_KEY", "")
MERCADO_PAGO_BASE_URL = os.getenv("MERCADO_PAGO_BASE_URL", "https://api.mercadopago.com")
FINANPY_SITE_URL = os.getenv("FINANPY_SITE_URL", "http://127.0.0.1:8000")
MERCADO_PAGO_WEBHOOK_SECRET = os.getenv("MERCADO_PAGO_WEBHOOK_SECRET", "")

# E-mail transacional: confirmação de cadastro e recuperação de senha.
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "20"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "FinanPy <no-reply@finanpy.com>")


# Logs estruturados para acompanhar erros reais no terminal local e no Render.
LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": True,
        },
        "financeiro": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}
