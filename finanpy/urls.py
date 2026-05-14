"""Rotas principais do projeto FinanPy."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from financeiro import views as financeiro_views


urlpatterns = [
    # Endpoint publico usado por monitoramento e health checks de deploy.
    path("healthz/", financeiro_views.healthcheck, name="healthcheck"),
    # Painel administrativo nativo do Django.
    path("admin/", admin.site.urls),
    # Pagina inicial decide entre cadastro ou painel.
    path("", financeiro_views.pagina_inicial, name="pagina_inicial"),
    # Rotas do app financeiro.
    path("", include("financeiro.urls")),
    # Tela de login usando a view nativa do Django.
    path(
        "login/",
        financeiro_views.EntrarUsuarioView.as_view(),
        name="login",
    ),
    # Esta rota replica o caminho padrao do Django e evita erro 404 em redirecionamentos antigos.
    path(
        "accounts/login/",
        financeiro_views.EntrarUsuarioView.as_view(),
    ),
    # Logout usando uma view propria para garantir fluxo consistente.
    path("logout/", financeiro_views.sair_usuario, name="logout"),
    # Cadastro de novos usuarios em uma view nossa.
    path("cadastro/", financeiro_views.registrar_usuario, name="registrar_usuario"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
