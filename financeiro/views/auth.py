"""Views de auth do app financeiro."""

from .common import *


def pagina_inicial(request):
    """
    Decide o primeiro destino do usuário.

    A página inicial pública do produto sempre apresenta o fluxo de entrada.
    Assim, quem abrir o site cai primeiro no cadastro.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")
    return redirect("registrar_usuario")

class EntrarUsuarioView(LoginView):
    """Tela de login com formulário customizado, bloqueio temporário e layout do projeto."""

    template_name = "registration/login.html"
    authentication_form = LoginUsuarioForm
    redirect_authenticated_user = True
    limite_tentativas = 5
    tempo_bloqueio_segundos = 30 * 60

    def dispatch(self, request, *args, **kwargs):
        """Bloqueia novas tentativas quando o usuário erra cinco vezes."""
        if request.method == "POST":
            chave = chave_login_tentativas(request)
            if cache.get(f"{chave}:bloqueado"):
                messages.error(
                    request,
                    "Muitas tentativas incorretas. Redefina sua senha ou tente novamente em alguns minutos.",
                )
                return redirect("password_reset")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        """Limpa a contagem de erros quando o login é concluído com sucesso."""
        chave = chave_login_tentativas(self.request)
        cache.delete(chave)
        cache.delete(f"{chave}:bloqueado")
        return super().form_valid(form)

    def form_invalid(self, form):
        """Conta erros de login e direciona para recuperação de senha ao atingir o limite."""
        chave = chave_login_tentativas(self.request)
        tentativas = cache.get(chave, 0) + 1
        cache.set(chave, tentativas, self.tempo_bloqueio_segundos)
        restantes = max(self.limite_tentativas - tentativas, 0)

        if tentativas >= self.limite_tentativas:
            cache.set(f"{chave}:bloqueado", True, self.tempo_bloqueio_segundos)
            messages.error(
                self.request,
                "Você atingiu 5 tentativas incorretas. Use a recuperação de senha para liberar o acesso.",
            )
            return redirect("password_reset")

        messages.warning(
            self.request,
            f"Login não realizado. Você ainda tem {restantes} tentativa(s) antes da recuperação obrigatória.",
        )
        return super().form_invalid(form)

class ConfirmarRedefinicaoSenhaView(PasswordResetConfirmView):
    """Redefine a senha e envia confirmação por e-mail ao usuário."""

    template_name = "registration/password_reset_confirm.html"

    def form_valid(self, form):
        usuario = form.user
        resposta = super().form_valid(form)
        if usuario.email:
            send_mail(
                "Senha alterada no FinanPy",
                (
                    f"Olá, {usuario.first_name or usuario.username}!\n\n"
                    "Sua senha do FinanPy foi alterada com sucesso.\n"
                    "Se você não fez essa alteração, redefina sua senha imediatamente."
                ),
                settings.DEFAULT_FROM_EMAIL,
                [usuario.email],
                fail_silently=True,
            )
        return resposta

@require_POST
def sair_usuario(request):
    """
    Encerra a sessão do usuário com redirecionamento previsível.

    Usamos apenas POST para evitar logout acidental por link, crawler ou imagem
    externa. O token CSRF confirma que a ação saiu da interface do FinanPy.
    """
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, "Sua sessão foi encerrada com sucesso.")

    return redirect("login")

def registrar_usuario(request):
    """Cria uma conta inativa e envia confirmação por e-mail antes do primeiro acesso."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = RegistroUsuarioForm(request.POST)
        if form.is_valid():
            usuario = form.save(commit=False)
            usuario.is_active = False
            usuario.email = form.cleaned_data["email"].strip().lower()
            usuario.save()
            try:
                enviar_email_confirmacao_cadastro(request, usuario)
            except Exception as erro:
                mensagem_email = mensagem_erro_email_transacional(erro)
                logger.exception(
                    "Falha ao enviar e-mail de confirmacao para novo cadastro. "
                    "backend=%s host=%s port=%s tls=%s ssl=%s from=%s user_configurado=%s brevo_api_key=%s",
                    settings.EMAIL_BACKEND,
                    settings.EMAIL_HOST,
                    settings.EMAIL_PORT,
                    settings.EMAIL_USE_TLS,
                    settings.EMAIL_USE_SSL,
                    settings.DEFAULT_FROM_EMAIL,
                    bool(settings.EMAIL_HOST_USER),
                    mascarar_brevo_api_key(getattr(settings, "BREVO_API_KEY", "")),
                )
                usuario.delete()
                form.add_error("email", mensagem_email)
                messages.error(request, mensagem_email)
                return render(request, "registration/register.html", {"form": form})
            messages.success(
                request,
                "Cadastro criado com sucesso. Enviamos um e-mail de confirmação para ativar sua conta.",
            )
            return redirect("login")
        messages.error(request, "Revise os dados informados para concluir seu cadastro.")
    else:
        form = RegistroUsuarioForm()

    return render(request, "registration/register.html", {"form": form})

def ativar_conta(request, uidb64, token):
    """Ativa a conta depois que o usuário clica no link enviado por e-mail."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        usuario = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        usuario = None

    if usuario and default_token_generator.check_token(usuario, token):
        usuario.is_active = True
        usuario.save(update_fields=["is_active"])
        PlanoUsuario.objects.get_or_create(usuario=usuario)
        ConfiguracaoUsuario.objects.get_or_create(usuario=usuario)
        messages.success(request, "E-mail confirmado com sucesso. Agora você já pode entrar no FinanPy.")
        return redirect("login")

    messages.error(request, "Link de confirmação inválido ou expirado. Solicite um novo cadastro ou redefinição.")
    return redirect("registrar_usuario")

@login_required
def perfil_usuario(request):
    """Pagina de perfil com dados pessoais, moeda, senha e foto."""
    configuracao, _ = ConfiguracaoUsuario.objects.get_or_create(usuario=request.user)

    perfil_form = PerfilUsuarioForm(instance=request.user, prefix="perfil")
    moeda_form = MoedaPerfilForm(instance=configuracao, prefix="moeda")
    foto_form = FotoPerfilForm(instance=configuracao, prefix="foto")
    senha_form = AlterarSenhaPerfilForm(request.user, prefix="senha")

    if request.method == "POST":
        if "salvar_perfil" in request.POST:
            perfil_form = PerfilUsuarioForm(request.POST, instance=request.user, prefix="perfil")
            if perfil_form.is_valid():
                perfil_form.save()
                messages.success(request, "Perfil atualizado com sucesso.")
                return redirect("perfil_usuario")
            messages.error(request, "Revise os dados do perfil para continuar.")

        elif "salvar_moeda" in request.POST:
            moeda_form = MoedaPerfilForm(request.POST, instance=configuracao, prefix="moeda")
            if moeda_form.is_valid():
                moeda_form.save()
                messages.success(request, "Moeda principal atualizada com sucesso.")
                return redirect("perfil_usuario")
            messages.error(request, "Revise a moeda selecionada para continuar.")

        elif "salvar_foto" in request.POST:
            foto_form = FotoPerfilForm(request.POST, request.FILES, instance=configuracao, prefix="foto")
            if foto_form.is_valid():
                foto_form.save(configuracao)
                messages.success(request, "Foto de perfil atualizada com sucesso.")
                return redirect("perfil_usuario")
            messages.error(request, "Nao foi possivel atualizar a foto. Revise o arquivo enviado.")

        elif "alterar_senha" in request.POST:
            senha_form = AlterarSenhaPerfilForm(request.user, request.POST, prefix="senha")
            if senha_form.is_valid():
                usuario = senha_form.save()
                update_session_auth_hash(request, usuario)
                messages.success(request, "Senha alterada com sucesso.")
                return redirect("perfil_usuario")
            messages.error(request, "Nao foi possivel alterar a senha. Revise os campos informados.")

    contexto = {
        "perfil_form": perfil_form,
        "moeda_form": moeda_form,
        "foto_form": foto_form,
        "senha_form": senha_form,
        "configuracao_usuario": configuracao,
    }
    return render(request, "perfil/painel.html", contexto)

@login_required
@require_POST
def excluir_conta_usuario(request):
    """Exclui definitivamente a conta e todos os dados ligados ao usuário."""
    form = ExcluirContaForm(request.user, request.POST, prefix="excluir")

    if not form.is_valid():
        for erros in form.errors.values():
            for erro in erros:
                messages.error(request, erro)
        return redirect("configuracoes")

    usuario = request.user
    logout(request)
    usuario.delete()
    messages.success(request, "Sua conta e todos os dados foram excluídos definitivamente.")
    return redirect("login")

@login_required
@require_POST
def marcar_notificacoes_lidas(request):
    """Marca os alertas do sininho como lidos."""
    Notificacao.objects.filter(usuario=request.user, lida=False).update(lida=True)
    return redirect(request.POST.get("proximo") or "dashboard")
