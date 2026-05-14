"""
Configuracoes centrais do projeto FinanPy.

Este arquivo concentra as definicoes que o Django usa para:
- saber quais apps fazem parte do projeto;
- onde estao templates e arquivos estaticos;
- qual banco de dados sera usado;
- como o login e o logout devem funcionar.
"""

import os
from pathlib import Path


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

# Em ambiente de estudos podemos manter uma chave fixa.
# Em producao, o ideal seria usar variavel de ambiente.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-finanpy-projeto-didatico")

# DEBUG=True deixa mensagens de erro mais detalhadas.
# Isso ajuda no aprendizado, mas nao deve ser usado em producao.
DEBUG = env_bool("DJANGO_DEBUG", True)

# Durante o desenvolvimento local, aceitamos hosts comuns.
# Em producao, a variavel DJANGO_ALLOWED_HOSTS deve ser definida.
hosts_env = os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
ALLOWED_HOSTS = [host.strip() for host in hosts_env.split(",") if host.strip()]


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


# Banco de dados SQLite, simples e ideal para iniciar o projeto.
DATABASES = {
    "default": {
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
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Chave primaria padrao usando BigAutoField.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Quando o usuario fizer login, ele ira para o dashboard.
LOGIN_REDIRECT_URL = "dashboard"

# Quando uma view protegida exigir autenticacao, o Django vai primeiro ao cadastro.
LOGIN_URL = "registrar_usuario"

# Quando fizer logout, voltamos para o fluxo inicial de cadastro.
LOGOUT_REDIRECT_URL = "registrar_usuario"

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
csrf_origins_env = os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [
    origem.strip() for origem in csrf_origins_env.split(",") if origem.strip()
]


# Credenciais da cobranca recorrente com Mercado Pago.
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "")
MERCADO_PAGO_PUBLIC_KEY = os.getenv("MERCADO_PAGO_PUBLIC_KEY", "")
MERCADO_PAGO_BASE_URL = os.getenv("MERCADO_PAGO_BASE_URL", "https://api.mercadopago.com")
FINANPY_SITE_URL = os.getenv("FINANPY_SITE_URL", "http://127.0.0.1:8000")
MERCADO_PAGO_WEBHOOK_SECRET = os.getenv("MERCADO_PAGO_WEBHOOK_SECRET", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "60"))
