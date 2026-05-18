"""Formularios do app financeiro."""

from datetime import date
from decimal import Decimal

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from .models import (
    CartaoCredito,
    Categoria,
    ConfiguracaoUsuario,
    Investimento,
    Lancamento,
    MetaFinanceira,
    OrcamentoCompartilhado,
    PlanoContencao,
)


class BootstrapFormMixin:
    """Adiciona classes Bootstrap aos campos automaticamente."""

    def aplicar_bootstrap(self):
        for _, campo in self.fields.items():
            classe = "form-control"

            if isinstance(campo.widget, forms.CheckboxInput):
                classe = "form-check-input"

            if isinstance(campo.widget, forms.Select):
                classe = "form-select"

            campo.widget.attrs.setdefault("class", classe)
            campo.widget.attrs.setdefault("placeholder", campo.label)


class RegistroUsuarioForm(UserCreationForm, BootstrapFormMixin):
    """Formulario de cadastro usando a base nativa do Django."""

    first_name = forms.CharField(label="Nome", max_length=150)
    email = forms.EmailField(label="E-mail")

    class Meta:
        model = User
        fields = ["username", "first_name", "email", "password1", "password2"]
        labels = {
            "username": "Nome de usuário",
            "password1": "Senha",
            "password2": "Confirmação da senha",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()

    def clean_email(self):
        """Impede cadastro duplicado com o mesmo e-mail."""
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Já existe uma conta cadastrada com este e-mail.")
        return email


class LoginUsuarioForm(AuthenticationForm, BootstrapFormMixin):
    """Formulario de login com visual consistente."""

    username = forms.CharField(label="Nome de usuário")
    password = forms.CharField(label="Senha", widget=forms.PasswordInput)

    error_messages = {
        "invalid_login": "Nome de usuário ou senha inválidos.",
        "inactive": "Esta conta ainda não foi ativada. Verifique seu e-mail de confirmação.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()


class PerfilUsuarioForm(forms.ModelForm, BootstrapFormMixin):
    """Formulario para atualizacao dos dados da conta."""

    class Meta:
        model = User
        fields = ["username", "first_name", "email"]
        labels = {
            "username": "Nome de usuário",
            "first_name": "Nome",
            "email": "E-mail",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()

    def clean_email(self):
        """Evita e-mails duplicados em outras contas."""
        email = self.cleaned_data["email"].strip().lower()
        queryset = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Já existe outra conta usando este e-mail.")
        return email


class ConfiguracaoUsuarioForm(forms.ModelForm, BootstrapFormMixin):
    """Formulário de preferências do painel."""

    class Meta:
        model = ConfiguracaoUsuario
        fields = [
            "moeda_padrao",
            "formato_data",
            "receber_alertas_email",
            "receber_alertas_vencimento",
            "exibir_saldo_dashboard",
        ]
        labels = {
            "moeda_padrao": "Moeda padrão",
            "formato_data": "Formato de data",
            "receber_alertas_email": "Receber alertas por e-mail",
            "receber_alertas_vencimento": "Receber alertas de vencimento",
            "exibir_saldo_dashboard": "Exibir saldo no dashboard",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()


class MoedaPerfilForm(forms.ModelForm, BootstrapFormMixin):
    """Formulário enxuto para alterar a moeda exibida no perfil."""

    class Meta:
        model = ConfiguracaoUsuario
        fields = ["moeda_padrao"]
        labels = {
            "moeda_padrao": "Moeda principal",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()


class FotoPerfilForm(forms.Form, BootstrapFormMixin):
    """Formulário para troca segura da foto do usuário."""

    foto_perfil = forms.FileField(
        label="Foto de perfil",
        required=True,
        widget=forms.FileInput(attrs={"accept": "image/*"}),
    )

    def __init__(self, *args, **kwargs):
        kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()

    def clean_foto_perfil(self):
        """Limita tamanho e extensão para evitar uploads inadequados."""
        arquivo = self.cleaned_data.get("foto_perfil")

        if not arquivo:
            return arquivo

        extensoes_permitidas = {".jpg", ".jpeg", ".png", ".webp", ".jfif", ".gif"}
        tipos_permitidos = {"image/jpeg", "image/png", "image/webp", "image/gif"}
        nome_arquivo = arquivo.name.lower()
        content_type = getattr(arquivo, "content_type", "").lower()
        extensao_valida = any(nome_arquivo.endswith(extensao) for extensao in extensoes_permitidas)
        tipo_valido = content_type in tipos_permitidos

        if not extensao_valida and not tipo_valido:
            raise forms.ValidationError("Envie uma imagem válida nos formatos JPG, PNG, WEBP ou GIF.")

        if content_type and not tipo_valido:
            raise forms.ValidationError("Esse tipo de imagem não é aceito. Use JPG, PNG, WEBP ou GIF.")

        limite_bytes = 5 * 1024 * 1024
        if arquivo.size > limite_bytes:
            raise forms.ValidationError("A foto precisa ter no máximo 5 MB.")

        return arquivo

    def save(self, configuracao):
        """Grava a imagem no banco para ela carregar corretamente em producao."""
        configuracao.atualizar_foto_perfil(self.cleaned_data["foto_perfil"])
        configuracao.save(
            update_fields=[
                "foto_perfil",
                "foto_perfil_binario",
                "foto_perfil_content_type",
                "foto_perfil_nome",
                "atualizada_em",
            ]
        )
        return configuracao


class AlterarSenhaPerfilForm(BootstrapFormMixin, forms.Form):
    """Formulário simples para troca de senha dentro do painel."""

    senha_atual = forms.CharField(label="Senha atual", widget=forms.PasswordInput)
    nova_senha = forms.CharField(
        label="Nova senha",
        widget=forms.PasswordInput,
        help_text=password_validation.password_validators_help_text_html(),
    )
    confirmar_nova_senha = forms.CharField(label="Confirmar nova senha", widget=forms.PasswordInput)

    def __init__(self, usuario, *args, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()

    def clean_senha_atual(self):
        """Confere se a senha atual foi digitada corretamente."""
        senha_atual = self.cleaned_data["senha_atual"]
        if not self.usuario.check_password(senha_atual):
            raise forms.ValidationError("A senha atual informada está incorreta.")
        return senha_atual

    def clean(self):
        """Valida a nova senha e a confirmação."""
        cleaned_data = super().clean()
        nova_senha = cleaned_data.get("nova_senha")
        confirmar_nova_senha = cleaned_data.get("confirmar_nova_senha")

        if nova_senha and confirmar_nova_senha and nova_senha != confirmar_nova_senha:
            self.add_error("confirmar_nova_senha", "A confirmação da nova senha não confere.")

        if nova_senha:
            try:
                password_validation.validate_password(nova_senha, self.usuario)
            except ValidationError as erro:
                self.add_error("nova_senha", erro)

        return cleaned_data

    def save(self):
        """Aplica a nova senha ao usuário."""
        self.usuario.set_password(self.cleaned_data["nova_senha"])
        self.usuario.save(update_fields=["password"])
        return self.usuario


class ExcluirContaForm(BootstrapFormMixin, forms.Form):
    """Confirma a exclusão definitiva da conta do usuário."""

    confirmacao = forms.CharField(
        label="Digite EXCLUIR para confirmar",
        max_length=20,
    )
    senha = forms.CharField(label="Senha atual", widget=forms.PasswordInput)

    def __init__(self, usuario, *args, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()

    def clean_confirmacao(self):
        confirmacao = self.cleaned_data["confirmacao"].strip().upper()
        if confirmacao != "EXCLUIR":
            raise forms.ValidationError("Digite EXCLUIR para confirmar a exclusão da conta.")
        return confirmacao

    def clean_senha(self):
        senha = self.cleaned_data["senha"]
        if not self.usuario.check_password(senha):
            raise forms.ValidationError("A senha atual informada está incorreta.")
        return senha


class CategoriaForm(forms.ModelForm, BootstrapFormMixin):
    """Formulário de categoria."""

    class Meta:
        model = Categoria
        fields = ["nome", "tipo", "limite_mensal", "descricao"]
        labels = {
            "nome": "Nome da categoria",
            "tipo": "Tipo",
            "limite_mensal": "Limite mensal",
            "descricao": "Descrição",
        }
        widgets = {
            "limite_mensal": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "descricao": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()


class CartaoCreditoForm(forms.ModelForm, BootstrapFormMixin):
    """Formulário de cartão."""

    class Meta:
        model = CartaoCredito
        fields = ["nome", "limite", "dia_fechamento", "dia_vencimento", "ativo"]
        labels = {
            "nome": "Nome do cartão",
            "limite": "Limite",
            "dia_fechamento": "Dia de fechamento",
            "dia_vencimento": "Dia de vencimento",
            "ativo": "Cartão ativo",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()


class LancamentoForm(forms.ModelForm, BootstrapFormMixin):
    """Formulário principal de receitas e despesas."""

    class Meta:
        model = Lancamento
        fields = [
            "tipo",
            "escopo",
            "orcamento_compartilhado",
            "descricao",
            "valor",
            "categoria",
            "data_competencia",
            "data_vencimento",
            "data_pagamento",
            "status",
            "forma_pagamento",
            "cartao",
            "compra_parcelada",
            "total_parcelas",
            "observacao",
        ]
        labels = {
            "tipo": "Tipo do lançamento",
            "escopo": "Conta",
            "orcamento_compartilhado": "Orçamento conjunto",
            "descricao": "Descrição",
            "valor": "Valor",
            "categoria": "Categoria",
            "data_competencia": "Data de competência",
            "data_vencimento": "Data de vencimento",
            "data_pagamento": "Data do pagamento",
            "status": "Status do pagamento",
            "forma_pagamento": "Forma de pagamento",
            "cartao": "Cartão de crédito",
            "compra_parcelada": "Compra parcelada",
            "total_parcelas": "Quantidade de parcelas",
            "observacao": "Observações",
        }
        widgets = {
            "data_competencia": forms.DateInput(attrs={"type": "date"}),
            "data_vencimento": forms.DateInput(attrs={"type": "date"}),
            "data_pagamento": forms.DateInput(attrs={"type": "date"}),
            "observacao": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        usuario = kwargs.pop("usuario", None)
        familia_liberada = kwargs.pop("familia_liberada", False)
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        self.familia_liberada = familia_liberada
        self.aplicar_bootstrap()

        if usuario is not None:
            self.fields["categoria"].queryset = Categoria.objects.filter(usuario=usuario)
            self.fields["cartao"].queryset = CartaoCredito.objects.filter(usuario=usuario, ativo=True)
            self.fields["orcamento_compartilhado"].queryset = OrcamentoCompartilhado.objects.filter(
                ativo=True,
                membros__usuario=usuario,
            ).distinct()

        self.fields["cartao"].required = False
        self.fields["data_pagamento"].required = False
        self.fields["orcamento_compartilhado"].required = False

        if not familia_liberada or not self.fields["orcamento_compartilhado"].queryset.exists():
            self.fields["escopo"].choices = [(Lancamento.ESCOPO_INDIVIDUAL, "Individual")]
            self.fields["orcamento_compartilhado"].widget = forms.HiddenInput()

    def clean(self):
        """Valida regras de negócio simples antes de salvar."""
        cleaned_data = super().clean()
        tipo = cleaned_data.get("tipo")
        escopo = cleaned_data.get("escopo")
        orcamento_compartilhado = cleaned_data.get("orcamento_compartilhado")
        categoria = cleaned_data.get("categoria")
        forma_pagamento = cleaned_data.get("forma_pagamento")
        cartao = cleaned_data.get("cartao")
        compra_parcelada = cleaned_data.get("compra_parcelada")
        total_parcelas = cleaned_data.get("total_parcelas") or 1

        if categoria and tipo and categoria.tipo != tipo:
            self.add_error("categoria", "Escolha uma categoria do mesmo tipo do lançamento.")

        if escopo == Lancamento.ESCOPO_COMPARTILHADO:
            if not self.familia_liberada:
                self.add_error("escopo", "Contas conjuntas fazem parte do plano Premium.")
            if not orcamento_compartilhado:
                self.add_error("orcamento_compartilhado", "Escolha o orçamento conjunto.")
            elif self.usuario and not orcamento_compartilhado.membros.filter(usuario=self.usuario).exists():
                self.add_error("orcamento_compartilhado", "Você não faz parte deste orçamento.")
        else:
            cleaned_data["orcamento_compartilhado"] = None

        if forma_pagamento == Lancamento.FORMA_CREDITO and not cartao and tipo == Lancamento.TIPO_DESPESA:
            self.add_error("cartao", "Selecione um cartão para despesas no crédito.")

        if compra_parcelada and total_parcelas < 2:
            self.add_error("total_parcelas", "Uma compra parcelada precisa ter pelo menos 2 parcelas.")

        if tipo == Lancamento.TIPO_RECEITA:
            cleaned_data["compra_parcelada"] = False
            cleaned_data["total_parcelas"] = 1
            cleaned_data["cartao"] = None

        return cleaned_data


class OrcamentoCompartilhadoForm(forms.ModelForm, BootstrapFormMixin):
    """Formulario para criar um orçamento compartilhado premium."""

    class Meta:
        model = OrcamentoCompartilhado
        fields = ["nome", "tipo"]
        labels = {
            "nome": "Nome do orçamento",
            "tipo": "Modo de uso",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()


class ConviteOrcamentoForm(BootstrapFormMixin, forms.Form):
    """Formulario para entrar em um orçamento usando código de convite."""

    codigo_convite = forms.CharField(label="Código de convite", max_length=12)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()
        self.fields["codigo_convite"].widget.attrs["placeholder"] = "Exemplo: A1B2C3D4E5"

    def clean_codigo_convite(self):
        """Padroniza o código digitado para facilitar a busca."""
        return self.cleaned_data["codigo_convite"].strip().upper()


class MetaFinanceiraForm(forms.ModelForm, BootstrapFormMixin):
    """Formulário do módulo de metas financeiras."""

    class Meta:
        model = MetaFinanceira
        fields = [
            "titulo",
            "descricao",
            "valor_alvo",
            "valor_atual",
            "valor_semanal_planejado",
            "data_inicio",
            "data_limite",
            "estrategia",
            "prioridade",
            "status",
        ]
        labels = {
            "titulo": "Nome da meta",
            "descricao": "Descrição",
            "valor_alvo": "Valor alvo",
            "valor_atual": "Valor já acumulado",
            "valor_semanal_planejado": "Quanto posso guardar por semana",
            "data_inicio": "Data de início",
            "data_limite": "Prazo final",
            "estrategia": "Estratégia da meta",
            "prioridade": "Prioridade",
            "status": "Status",
        }
        widgets = {
            "descricao": forms.Textarea(attrs={"rows": 4}),
            "data_inicio": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "data_limite": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()
        self.fields["data_inicio"].input_formats = ["%Y-%m-%d"]
        self.fields["data_limite"].input_formats = ["%Y-%m-%d"]

    def clean(self):
        """Valida as regras essenciais da meta."""
        cleaned_data = super().clean()
        valor_alvo = cleaned_data.get("valor_alvo")
        valor_atual = cleaned_data.get("valor_atual")
        valor_semanal_planejado = cleaned_data.get("valor_semanal_planejado")
        data_inicio = cleaned_data.get("data_inicio")
        data_limite = cleaned_data.get("data_limite")

        if valor_alvo is not None and valor_alvo <= 0:
            self.add_error("valor_alvo", "O valor alvo precisa ser maior que zero.")

        if valor_atual is not None and valor_atual < 0:
            self.add_error("valor_atual", "O valor atual não pode ser negativo.")

        if valor_semanal_planejado is not None and valor_semanal_planejado < 0:
            self.add_error("valor_semanal_planejado", "O valor semanal não pode ser negativo.")

        if data_inicio and data_limite and data_limite < data_inicio:
            self.add_error("data_limite", "O prazo final não pode ser anterior à data de início.")

        if (
            valor_alvo
            and valor_atual is not None
            and valor_semanal_planejado
            and valor_semanal_planejado > 0
            and data_limite
        ):
            hoje = data_inicio or date.today()
            restante = max(valor_alvo - valor_atual, Decimal("0.00"))
            dias_restantes = max((data_limite - hoje).days, 1)
            semanas_restantes = max(Decimal(dias_restantes) / Decimal("7"), Decimal("1"))
            semanal_necessario = restante / semanas_restantes if restante > 0 else Decimal("0.00")

            if semanal_necessario > 0 and valor_semanal_planejado >= semanal_necessario:
                cleaned_data["estrategia"] = MetaFinanceira.ESTRATEGIA_AGRESSIVA
            elif semanal_necessario > 0 and valor_semanal_planejado >= semanal_necessario * Decimal("0.60"):
                cleaned_data["estrategia"] = MetaFinanceira.ESTRATEGIA_SUAVE
            else:
                cleaned_data["estrategia"] = MetaFinanceira.ESTRATEGIA_CONSERVADORA

        return cleaned_data


class InvestimentoForm(forms.ModelForm, BootstrapFormMixin):
    """Formulário para cadastro e edição dos investimentos."""

    class Meta:
        model = Investimento
        fields = [
            "nome",
            "tipo",
            "instituicao",
            "valor_aplicado",
            "valor_atual",
            "data_aplicacao",
            "data_vencimento",
            "objetivo",
            "status",
            "observacao",
        ]
        labels = {
            "nome": "Nome do investimento",
            "tipo": "Tipo",
            "instituicao": "Instituição",
            "valor_aplicado": "Valor aplicado",
            "valor_atual": "Valor atual",
            "data_aplicacao": "Data da aplicação",
            "data_vencimento": "Data de vencimento",
            "objetivo": "Objetivo",
            "status": "Status",
            "observacao": "Observações",
        }
        widgets = {
            "data_aplicacao": forms.DateInput(attrs={"type": "date"}),
            "data_vencimento": forms.DateInput(attrs={"type": "date"}),
            "observacao": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()
        self.fields["data_vencimento"].required = False
        self.fields["objetivo"].required = False

    def clean(self):
        """Valida o intervalo de datas e os valores monetários."""
        cleaned_data = super().clean()
        valor_aplicado = cleaned_data.get("valor_aplicado")
        valor_atual = cleaned_data.get("valor_atual")
        data_aplicacao = cleaned_data.get("data_aplicacao")
        data_vencimento = cleaned_data.get("data_vencimento")

        if valor_aplicado is not None and valor_aplicado <= 0:
            self.add_error("valor_aplicado", "O valor aplicado precisa ser maior que zero.")

        if valor_atual is not None and valor_atual < 0:
            self.add_error("valor_atual", "O valor atual não pode ser negativo.")

        if data_aplicacao and data_vencimento and data_vencimento < data_aplicacao:
            self.add_error("data_vencimento", "O vencimento não pode ser anterior à data da aplicação.")

        return cleaned_data


class AnaliseFinanceiraIAForm(BootstrapFormMixin, forms.Form):
    """Formulário simples para escolher o período da análise premium."""

    mes_referencia = forms.DateField(
        label="Mês de referência",
        widget=forms.DateInput(attrs={"type": "month"}),
        input_formats=["%Y-%m"],
    )
    forcar_regeneracao = forms.BooleanField(
        label="Gerar nova análise mesmo que já exista uma salva para este mês",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()


class SimuladorDecisaoForm(BootstrapFormMixin, forms.Form):
    """Formulário premium para simular uma decisão financeira antes da compra."""

    TIPO_COMPRA_UNICA = "COMPRA_UNICA"
    TIPO_PARCELADA = "PARCELADA"
    TIPO_CHOICES = [
        (TIPO_COMPRA_UNICA, "Compra única"),
        (TIPO_PARCELADA, "Compra parcelada"),
    ]

    PRIORIDADE_NECESSARIA = "NECESSARIA"
    PRIORIDADE_IMPORTANTE = "IMPORTANTE"
    PRIORIDADE_DESEJO = "DESEJO"
    PRIORIDADE_CHOICES = [
        (PRIORIDADE_NECESSARIA, "Necessária"),
        (PRIORIDADE_IMPORTANTE, "Importante"),
        (PRIORIDADE_DESEJO, "Desejo"),
    ]

    descricao = forms.CharField(
        label="Nome da decisão",
        max_length=120,
        help_text="Exemplo: celular novo, curso, viagem ou compra no cartão.",
    )
    tipo = forms.ChoiceField(label="Tipo da decisão", choices=TIPO_CHOICES)
    prioridade = forms.ChoiceField(label="Prioridade", choices=PRIORIDADE_CHOICES)
    valor_total = forms.DecimalField(
        label="Valor total",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    quantidade_parcelas = forms.IntegerField(
        label="Quantidade de parcelas",
        min_value=1,
        max_value=48,
        initial=1,
    )
    mes_inicio = forms.DateField(
        label="Mês de início",
        widget=forms.DateInput(attrs={"type": "month"}),
        input_formats=["%Y-%m"],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aplicar_bootstrap()

    def clean(self):
        """Ajusta parcelas para compra única e valida combinações simples."""
        cleaned_data = super().clean()
        tipo = cleaned_data.get("tipo")

        if tipo == self.TIPO_COMPRA_UNICA:
            cleaned_data["quantidade_parcelas"] = 1

        return cleaned_data


class PlanoContencaoForm(BootstrapFormMixin, forms.Form):
    """Formulario premium para ativar o modo anti-descontrole."""

    titulo = forms.CharField(
        label="Nome do plano",
        max_length=120,
        initial="Modo anti-descontrole",
    )
    duracao_dias = forms.ChoiceField(
        label="Duração",
        choices=PlanoContencao.DURACAO_CHOICES,
    )
    orcamento_total = forms.DecimalField(
        label="Orçamento total do período",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    categorias = forms.ModelMultipleChoiceField(
        label="Categorias monitoradas",
        queryset=Categoria.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        usuario = kwargs.pop("usuario", None)
        super().__init__(*args, **kwargs)

        if usuario is not None:
            self.fields["categorias"].queryset = Categoria.objects.filter(
                usuario=usuario,
                tipo=Categoria.TIPO_DESPESA,
            )

        self.aplicar_bootstrap()
        self.fields["categorias"].widget.attrs["class"] = "category-check-list"

    def clean_duracao_dias(self):
        return int(self.cleaned_data["duracao_dias"])

    def clean(self):
        cleaned_data = super().clean()
        categorias = cleaned_data.get("categorias")

        if categorias is not None and not categorias.exists():
            self.add_error("categorias", "Selecione pelo menos uma categoria de despesa.")

        return cleaned_data
