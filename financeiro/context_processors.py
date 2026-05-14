"""Context processors do app financeiro."""

from .models import PlanoUsuario


def plano_usuario_context(request):
    """Disponibiliza o plano atual nos templates autenticados."""
    plano_usuario = None

    if request.user.is_authenticated:
        plano_usuario, _ = PlanoUsuario.objects.get_or_create(usuario=request.user)

    return {"plano_usuario_atual": plano_usuario}
