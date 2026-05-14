"""Context processors do app financeiro."""

from .models import ConfiguracaoUsuario, PlanoUsuario


def plano_usuario_context(request):
    """Disponibiliza o plano atual nos templates autenticados."""
    plano_usuario = None
    configuracao_usuario = None

    if request.user.is_authenticated:
        plano_usuario, _ = PlanoUsuario.objects.get_or_create(usuario=request.user)
        configuracao_usuario, _ = ConfiguracaoUsuario.objects.get_or_create(usuario=request.user)

    return {
        "plano_usuario_atual": plano_usuario,
        "configuracao_usuario_atual": configuracao_usuario,
    }
