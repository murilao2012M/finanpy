"""Views de configuracoes do app financeiro."""

from .common import *


@login_required
def configuracoes(request):
    """Centraliza perfil, preferências e segurança da conta."""
    configuracao, _ = ConfiguracaoUsuario.objects.get_or_create(usuario=request.user)
    plano_usuario = obter_plano_usuario(request.user)
    ultima_analise_ia = AnaliseFinanceiraIA.objects.filter(usuario=request.user).first()
    eventos_assinatura = EventoAssinatura.objects.filter(usuario=request.user)[:8]

    perfil_form = PerfilUsuarioForm(instance=request.user, prefix="perfil")
    preferencias_form = ConfiguracaoUsuarioForm(instance=configuracao, prefix="preferencias")
    senha_form = AlterarSenhaPerfilForm(request.user, prefix="senha")
    excluir_conta_form = ExcluirContaForm(request.user, prefix="excluir")

    if request.method == "POST":
        if "salvar_perfil" in request.POST:
            perfil_form = PerfilUsuarioForm(request.POST, instance=request.user, prefix="perfil")
            if perfil_form.is_valid():
                perfil_form.save()
                messages.success(request, "Seus dados de perfil foram atualizados com sucesso.")
                return redirect("configuracoes")
            messages.error(request, "Revise os dados do perfil para continuar.")

        elif "salvar_preferencias" in request.POST:
            preferencias_form = ConfiguracaoUsuarioForm(
                request.POST,
                instance=configuracao,
                prefix="preferencias",
            )
            if preferencias_form.is_valid():
                preferencias_form.save()
                messages.success(request, "Suas preferências foram salvas com sucesso.")
                return redirect("configuracoes")
            messages.error(request, "Revise as preferências informadas para continuar.")

        elif "alterar_senha" in request.POST:
            senha_form = AlterarSenhaPerfilForm(request.user, request.POST, prefix="senha")
            if senha_form.is_valid():
                usuario = senha_form.save()
                update_session_auth_hash(request, usuario)
                messages.success(request, "Sua senha foi alterada com sucesso.")
                return redirect("configuracoes")
            messages.error(request, "Não foi possível alterar a senha. Revise os campos informados.")

    contexto = {
        "perfil_form": perfil_form,
        "preferencias_form": preferencias_form,
        "senha_form": senha_form,
        "excluir_conta_form": excluir_conta_form,
        "configuracao_usuario": configuracao,
        "plano_usuario": plano_usuario,
        "ultima_analise_ia": ultima_analise_ia,
        "eventos_assinatura": eventos_assinatura,
    }
    contexto.update(montar_contexto_configuracoes(request.user, configuracao, plano_usuario, ultima_analise_ia))
    return render(request, "configuracoes/painel.html", contexto)
