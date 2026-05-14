"""Models do sistema financeiro."""

from datetime import date
from decimal import Decimal
import uuid

from django.contrib.auth.models import User
from django.db import models


class Categoria(models.Model):
    """Agrupa lancamentos por finalidade financeira."""

    TIPO_RECEITA = "RECEITA"
    TIPO_DESPESA = "DESPESA"
    TIPOS = [
        (TIPO_RECEITA, "Receita"),
        (TIPO_DESPESA, "Despesa"),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="categorias")
    nome = models.CharField(max_length=100)
    tipo = models.CharField(max_length=10, choices=TIPOS)
    descricao = models.TextField(blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["tipo", "nome"]
        unique_together = ("usuario", "nome", "tipo")
        verbose_name = "Categoria"
        verbose_name_plural = "Categorias"

    def __str__(self):
        return f"{self.nome} ({self.get_tipo_display()})"


class CartaoCredito(models.Model):
    """Representa um cartao de credito cadastrado pelo usuario."""

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="cartoes")
    nome = models.CharField(max_length=100)
    limite = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    dia_fechamento = models.PositiveSmallIntegerField(default=1)
    dia_vencimento = models.PositiveSmallIntegerField(default=10)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "Cartao de Credito"
        verbose_name_plural = "Cartoes de Credito"

    def __str__(self):
        return self.nome


class Lancamento(models.Model):
    """Model principal de movimentacoes financeiras."""

    TIPO_RECEITA = "RECEITA"
    TIPO_DESPESA = "DESPESA"
    TIPOS = [
        (TIPO_RECEITA, "Receita"),
        (TIPO_DESPESA, "Despesa"),
    ]

    STATUS_PAGO = "PAGO"
    STATUS_PENDENTE = "PENDENTE"
    STATUS_ATRASADO = "ATRASADO"
    STATUS_CHOICES = [
        (STATUS_PAGO, "Pago"),
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_ATRASADO, "Atrasado"),
    ]

    FORMA_DINHEIRO = "DINHEIRO"
    FORMA_DEBITO = "DEBITO"
    FORMA_CREDITO = "CREDITO"
    FORMA_PIX = "PIX"
    FORMA_TRANSFERENCIA = "TRANSFERENCIA"
    FORMA_BOLETO = "BOLETO"
    FORMAS_PAGAMENTO = [
        (FORMA_DINHEIRO, "Dinheiro"),
        (FORMA_DEBITO, "Cartao de Debito"),
        (FORMA_CREDITO, "Cartao de Credito"),
        (FORMA_PIX, "PIX"),
        (FORMA_TRANSFERENCIA, "Transferencia"),
        (FORMA_BOLETO, "Boleto"),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lancamentos")
    tipo = models.CharField(max_length=10, choices=TIPOS)
    descricao = models.CharField(max_length=150)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="lancamentos")
    data_competencia = models.DateField()
    data_vencimento = models.DateField()
    data_pagamento = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDENTE)
    forma_pagamento = models.CharField(max_length=20, choices=FORMAS_PAGAMENTO)
    observacao = models.TextField(blank=True)
    cartao = models.ForeignKey(
        CartaoCredito,
        on_delete=models.SET_NULL,
        related_name="lancamentos",
        blank=True,
        null=True,
    )
    compra_parcelada = models.BooleanField(default=False)
    parcela_atual = models.PositiveSmallIntegerField(default=1)
    total_parcelas = models.PositiveSmallIntegerField(default=1)
    grupo_parcelas = models.UUIDField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-data_competencia", "-id"]
        verbose_name = "Lancamento"
        verbose_name_plural = "Lancamentos"

    def __str__(self):
        return f"{self.descricao} - R$ {self.valor}"

    @property
    def descricao_completa(self):
        """Exibe a parcela junto da descricao quando existir parcelamento."""
        if self.total_parcelas > 1:
            return f"{self.descricao} ({self.parcela_atual}/{self.total_parcelas})"
        return self.descricao

    def atualizar_status_automaticamente(self):
        """Mantem o status coerente com datas de vencimento e pagamento."""
        if self.data_pagamento:
            self.status = self.STATUS_PAGO
        elif self.data_vencimento < date.today():
            self.status = self.STATUS_ATRASADO
        else:
            self.status = self.STATUS_PENDENTE

    def save(self, *args, **kwargs):
        """Atualiza o status antes de salvar."""
        self.atualizar_status_automaticamente()

        if self.total_parcelas > 1 and not self.grupo_parcelas:
            self.grupo_parcelas = uuid.uuid4()

        super().save(*args, **kwargs)


class MetaFinanceira(models.Model):
    """Representa um objetivo financeiro do usuario."""

    STATUS_EM_ANDAMENTO = "EM_ANDAMENTO"
    STATUS_CONCLUIDA = "CONCLUIDA"
    STATUS_PAUSADA = "PAUSADA"
    STATUS_ATRASADA = "ATRASADA"
    STATUS_CHOICES = [
        (STATUS_EM_ANDAMENTO, "Em andamento"),
        (STATUS_CONCLUIDA, "Concluida"),
        (STATUS_PAUSADA, "Pausada"),
        (STATUS_ATRASADA, "Atrasada"),
    ]

    PRIORIDADE_BAIXA = "BAIXA"
    PRIORIDADE_MEDIA = "MEDIA"
    PRIORIDADE_ALTA = "ALTA"
    PRIORIDADE_CHOICES = [
        (PRIORIDADE_BAIXA, "Baixa"),
        (PRIORIDADE_MEDIA, "Media"),
        (PRIORIDADE_ALTA, "Alta"),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="metas")
    titulo = models.CharField(max_length=120)
    descricao = models.TextField(blank=True)
    valor_alvo = models.DecimalField(max_digits=12, decimal_places=2)
    valor_atual = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    data_inicio = models.DateField(default=date.today)
    data_limite = models.DateField()
    prioridade = models.CharField(max_length=10, choices=PRIORIDADE_CHOICES, default=PRIORIDADE_MEDIA)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_EM_ANDAMENTO)
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "data_limite", "-criada_em"]
        verbose_name = "Meta Financeira"
        verbose_name_plural = "Metas Financeiras"

    def __str__(self):
        return self.titulo

    @property
    def valor_restante(self):
        """Informa quanto ainda falta para bater a meta."""
        restante = self.valor_alvo - self.valor_atual
        return restante if restante > Decimal("0.00") else Decimal("0.00")

    @property
    def progresso_percentual(self):
        """Calcula o progresso em porcentagem, limitado a 100%."""
        if self.valor_alvo <= 0:
            return 0

        percentual = (self.valor_atual / self.valor_alvo) * 100
        percentual_limitado = min(percentual, Decimal("100"))
        return round(float(percentual_limitado), 2)

    @property
    def progresso_css(self):
        """Retorna o progresso formatado para CSS inline."""
        return format(self.progresso_percentual, ".2f")

    def atualizar_status_automaticamente(self):
        """Ajusta o status com base no progresso e no prazo."""
        if self.valor_atual >= self.valor_alvo:
            self.status = self.STATUS_CONCLUIDA
        elif self.status == self.STATUS_PAUSADA:
            self.status = self.STATUS_PAUSADA
        elif self.data_limite < date.today():
            self.status = self.STATUS_ATRASADA
        else:
            self.status = self.STATUS_EM_ANDAMENTO

    def save(self, *args, **kwargs):
        """Mantem coerencia entre valores e status antes de gravar."""
        self.valor_alvo = Decimal(str(self.valor_alvo))
        self.valor_atual = Decimal(str(self.valor_atual))

        if self.valor_atual < Decimal("0.00"):
            self.valor_atual = Decimal("0.00")

        self.atualizar_status_automaticamente()
        super().save(*args, **kwargs)


class Investimento(models.Model):
    """Representa um investimento cadastrado pelo usuario."""

    TIPO_RENDA_FIXA = "RENDA_FIXA"
    TIPO_ACAO = "ACAO"
    TIPO_FII = "FII"
    TIPO_ETF = "ETF"
    TIPO_FUNDO = "FUNDO"
    TIPO_CRIPTO = "CRIPTO"
    TIPO_TESOURO = "TESOURO"
    TIPO_OUTRO = "OUTRO"
    TIPOS = [
        (TIPO_RENDA_FIXA, "Renda fixa"),
        (TIPO_ACAO, "Acao"),
        (TIPO_FII, "FII"),
        (TIPO_ETF, "ETF"),
        (TIPO_FUNDO, "Fundo"),
        (TIPO_CRIPTO, "Cripto"),
        (TIPO_TESOURO, "Tesouro"),
        (TIPO_OUTRO, "Outro"),
    ]

    STATUS_ATIVO = "ATIVO"
    STATUS_ENCERRADO = "ENCERRADO"
    STATUS_CHOICES = [
        (STATUS_ATIVO, "Ativo"),
        (STATUS_ENCERRADO, "Encerrado"),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="investimentos")
    nome = models.CharField(max_length=120)
    tipo = models.CharField(max_length=20, choices=TIPOS)
    instituicao = models.CharField(max_length=120)
    valor_aplicado = models.DecimalField(max_digits=12, decimal_places=2)
    valor_atual = models.DecimalField(max_digits=12, decimal_places=2)
    data_aplicacao = models.DateField()
    data_vencimento = models.DateField(blank=True, null=True)
    objetivo = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_ATIVO)
    observacao = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-data_aplicacao", "nome"]
        verbose_name = "Investimento"
        verbose_name_plural = "Investimentos"

    def __str__(self):
        return f"{self.nome} ({self.get_tipo_display()})"

    @property
    def rentabilidade_valor(self):
        """Mostra o ganho ou perda nominal do investimento."""
        return self.valor_atual - self.valor_aplicado

    @property
    def rentabilidade_percentual(self):
        """Calcula a rentabilidade percentual sobre o valor aplicado."""
        if self.valor_aplicado <= 0:
            return 0

        percentual = (self.rentabilidade_valor / self.valor_aplicado) * 100
        return round(float(percentual), 2)


class ConfiguracaoUsuario(models.Model):
    """Armazena preferencias do painel para cada usuario."""

    MOEDA_BRL = "BRL"
    MOEDA_USD = "USD"
    MOEDA_EUR = "EUR"
    MOEDA_CHOICES = [
        (MOEDA_BRL, "Real brasileiro (R$)"),
        (MOEDA_USD, "Dolar americano (US$)"),
        (MOEDA_EUR, "Euro (EUR)"),
    ]

    FORMATO_DATA_BR = "BR"
    FORMATO_DATA_ISO = "ISO"
    FORMATO_DATA_CHOICES = [
        (FORMATO_DATA_BR, "Dia/Mes/Ano"),
        (FORMATO_DATA_ISO, "Ano-Mes-Dia"),
    ]

    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name="configuracoes")
    moeda_padrao = models.CharField(max_length=5, choices=MOEDA_CHOICES, default=MOEDA_BRL)
    formato_data = models.CharField(max_length=10, choices=FORMATO_DATA_CHOICES, default=FORMATO_DATA_BR)
    receber_alertas_email = models.BooleanField(default=True)
    receber_alertas_vencimento = models.BooleanField(default=True)
    exibir_saldo_dashboard = models.BooleanField(default=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuracao do Usuario"
        verbose_name_plural = "Configuracoes dos Usuarios"

    def __str__(self):
        return f"Configuracoes de {self.usuario.username}"


class PlanoUsuario(models.Model):
    """Representa o plano comercial ativo do usuario."""

    PLANO_FREEMIUM = "FREEMIUM"
    PLANO_PREMIUM = "PREMIUM"
    PLANO_CHOICES = [
        (PLANO_FREEMIUM, "Freemium"),
        (PLANO_PREMIUM, "Premium"),
    ]

    STATUS_ATIVO = "ATIVO"
    STATUS_CANCELADO = "CANCELADO"
    STATUS_CHOICES = [
        (STATUS_ATIVO, "Ativo"),
        (STATUS_CANCELADO, "Cancelado"),
    ]

    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name="plano")
    nome_plano = models.CharField(max_length=20, choices=PLANO_CHOICES, default=PLANO_FREEMIUM)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_ATIVO)
    valor_mensal = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    ia_habilitada = models.BooleanField(default=False)
    limite_cartoes = models.PositiveSmallIntegerField(default=1)
    limite_metas = models.PositiveSmallIntegerField(default=2)
    limite_investimentos = models.PositiveSmallIntegerField(default=1)
    mercado_pago_preapproval_id = models.CharField(max_length=120, blank=True)
    mercado_pago_checkout_url = models.URLField(blank=True)
    mercado_pago_status = models.CharField(max_length=40, blank=True)
    mercado_pago_referencia_externa = models.CharField(max_length=120, blank=True)
    ultima_sincronizacao_gateway = models.DateTimeField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Plano do Usuario"
        verbose_name_plural = "Planos dos Usuarios"

    def __str__(self):
        return f"{self.usuario.username} - {self.get_nome_plano_display()}"

    @property
    def eh_premium(self):
        """Indica se o usuario esta no plano premium."""
        return self.nome_plano == self.PLANO_PREMIUM and self.status == self.STATUS_ATIVO

    def ativar_freemium(self):
        """Configura o usuario para o plano freemium."""
        self.nome_plano = self.PLANO_FREEMIUM
        self.status = self.STATUS_ATIVO
        self.valor_mensal = Decimal("0.00")
        self.ia_habilitada = False
        self.limite_cartoes = 1
        self.limite_metas = 2
        self.limite_investimentos = 1

    def ativar_premium(self):
        """Configura o usuario para o plano premium completo."""
        self.nome_plano = self.PLANO_PREMIUM
        self.status = self.STATUS_ATIVO
        self.valor_mensal = Decimal("10.50")
        self.ia_habilitada = True
        self.limite_cartoes = 999
        self.limite_metas = 999
        self.limite_investimentos = 999

    def save(self, *args, **kwargs):
        """Mantem coerencia automatica entre nome do plano e beneficios."""
        if self.nome_plano == self.PLANO_PREMIUM:
            self.ativar_premium()
        else:
            self.ativar_freemium()

        super().save(*args, **kwargs)


class AnaliseFinanceiraIA(models.Model):
    """Guarda o historico das analises inteligentes geradas para cada usuario."""

    STATUS_SUCESSO = "SUCESSO"
    STATUS_ERRO = "ERRO"
    STATUS_CHOICES = [
        (STATUS_SUCESSO, "Sucesso"),
        (STATUS_ERRO, "Erro"),
    ]

    SAUDE_OTIMA = "OTIMA"
    SAUDE_ESTAVEL = "ESTAVEL"
    SAUDE_ATENCAO = "ATENCAO"
    SAUDE_CRITICA = "CRITICA"
    SAUDE_CHOICES = [
        (SAUDE_OTIMA, "Otima"),
        (SAUDE_ESTAVEL, "Estavel"),
        (SAUDE_ATENCAO, "Atencao"),
        (SAUDE_CRITICA, "Critica"),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="analises_ia")
    periodo_inicio = models.DateField()
    periodo_fim = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_SUCESSO)
    modelo = models.CharField(max_length=60, blank=True)
    saude_financeira = models.CharField(
        max_length=12,
        choices=SAUDE_CHOICES,
        default=SAUDE_ESTAVEL,
    )
    resumo_executivo = models.TextField(blank=True)
    contexto_enviado = models.JSONField(default=dict, blank=True)
    metricas_resumo = models.JSONField(default=dict, blank=True)
    sinais_positivos = models.JSONField(default=list, blank=True)
    alertas_prioritarios = models.JSONField(default=list, blank=True)
    oportunidades = models.JSONField(default=list, blank=True)
    plano_acao = models.JSONField(default=list, blank=True)
    mensagem_erro = models.TextField(blank=True)
    resposta_bruta = models.JSONField(default=dict, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criada_em"]
        verbose_name = "Analise Financeira IA"
        verbose_name_plural = "Analises Financeiras IA"

    def __str__(self):
        return f"{self.usuario.username} - {self.periodo_inicio:%m/%Y} - {self.status}"
