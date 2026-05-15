"""Context processors do app financeiro."""

from .models import ConfiguracaoUsuario, Notificacao, PlanoUsuario


def plano_usuario_context(request):
    """Disponibiliza o plano atual nos templates autenticados."""
    plano_usuario = None
    configuracao_usuario = None
    notificacoes_recentes = []
    notificacoes_nao_lidas = 0

    if request.user.is_authenticated:
        plano_usuario, _ = PlanoUsuario.objects.get_or_create(usuario=request.user)
        configuracao_usuario, _ = ConfiguracaoUsuario.objects.get_or_create(usuario=request.user)
        notificacoes_queryset = Notificacao.objects.filter(usuario=request.user)
        notificacoes_recentes = notificacoes_queryset[:8]
        notificacoes_nao_lidas = notificacoes_queryset.filter(lida=False).count()

    return {
        "plano_usuario_atual": plano_usuario,
        "configuracao_usuario_atual": configuracao_usuario,
        "notificacoes_recentes": notificacoes_recentes,
        "notificacoes_nao_lidas": notificacoes_nao_lidas,
    }
