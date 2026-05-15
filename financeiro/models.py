"""Models do sistema financeiro."""

import base64
from datetime import date
from decimal import Decimal
import uuid

from django.contrib.auth.models import User
from django.db import models


class Notificacao(models.Model):
    """Guarda alertas e movimentações importantes exibidas no sininho do painel."""

    TIPO_INFO = "INFO"
    TIPO_SUCESSO = "SUCESSO"
    TIPO_ALERTA = "ALERTA"
    TIPO_ERRO = "ERRO"
    TIPO_CHOICES = [
        (TIPO_INFO, "Informação"),
        (TIPO_SUCESSO, "Sucesso"),
        (TIPO_ALERTA, "Alerta"),
        (TIPO_ERRO, "Erro"),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notificacoes")
    titulo = models.CharField(max_length=120)
    mensagem = models.TextField()
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default=TIPO_INFO)
    link = models.CharField(max_length=255, blank=True)
    lida = models.BooleanField(default=False)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criada_em", "-id"]
        verbose_name = "Notificação"
        verbose_name_plural = "Notificações"

    def __str__(self):
        return f"{self.titulo} - {self.usuario.username}"


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


class OrcamentoCompartilhado(models.Model):
    """Representa um orçamento premium compartilhado entre duas ou mais pessoas."""

    TIPO_CASAL = "CASAL"
    TIPO_FAMILIA = "FAMILIA"
    TIPO_CHOICES = [
        (TIPO_CASAL, "Casal"),
        (TIPO_FAMILIA, "Família"),
    ]

    dono = models.ForeignKey(User, on_delete=models.CASCADE, related_name="orcamentos_criados")
    nome = models.CharField(max_length=120)
    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES, default=TIPO_CASAL)
    codigo_convite = models.CharField(max_length=12, unique=True, blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "Orçamento Compartilhado"
        verbose_name_plural = "Orçamentos Compartilhados"

    def __str__(self):
        return self.nome

    def gerar_codigo_convite(self):
        """Cria um código curto para outra pessoa entrar no orçamento."""
        while True:
            codigo = uuid.uuid4().hex[:10].upper()
            if not OrcamentoCompartilhado.objects.filter(codigo_convite=codigo).exists():
                return codigo

    def save(self, *args, **kwargs):
        """Garante que todo orçamento tenha um código único antes de salvar."""
        if not self.codigo_convite:
            self.codigo_convite = self.gerar_codigo_convite()
        super().save(*args, **kwargs)


class MembroOrcamento(models.Model):
    """Liga usuários a um orçamento compartilhado."""

    PAPEL_ADMIN = "ADMIN"
    PAPEL_MEMBRO = "MEMBRO"
    PAPEL_CHOICES = [
        (PAPEL_ADMIN, "Administrador"),
        (PAPEL_MEMBRO, "Membro"),
    ]

    orcamento = models.ForeignKey(OrcamentoCompartilhado, on_delete=models.CASCADE, related_name="membros")
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="orcamentos_compartilhados")
    papel = models.CharField(max_length=10, choices=PAPEL_CHOICES, default=PAPEL_MEMBRO)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["usuario__first_name", "usuario__username"]
        unique_together = ("orcamento", "usuario")
        verbose_name = "Membro do Orçamento"
        verbose_name_plural = "Membros dos Orçamentos"

    def __str__(self):
        return f"{self.usuario.username} em {self.orcamento.nome}"


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
        (FORMA_DEBITO, "Cartão de Débito"),
        (FORMA_CREDITO, "Cartão de Crédito"),
        (FORMA_PIX, "PIX"),
        (FORMA_TRANSFERENCIA, "Transferência"),
        (FORMA_BOLETO, "Boleto"),
    ]

    ESCOPO_INDIVIDUAL = "INDIVIDUAL"
    ESCOPO_COMPARTILHADO = "COMPARTILHADO"
    ESCOPO_CHOICES = [
        (ESCOPO_INDIVIDUAL, "Individual"),
        (ESCOPO_COMPARTILHADO, "Conjunto"),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lancamentos")
    tipo = models.CharField(max_length=10, choices=TIPOS)
    escopo = models.CharField(max_length=20, choices=ESCOPO_CHOICES, default=ESCOPO_INDIVIDUAL)
    orcamento_compartilhado = models.ForeignKey(
        OrcamentoCompartilhado,
        on_delete=models.SET_NULL,
        related_name="lancamentos",
        blank=True,
        null=True,
    )
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
        verbose_name = "Lançamento"
        verbose_name_plural = "Lançamentos"

    def __str__(self):
        return f"{self.descricao} - R$ {self.valor}"

    @property
    def descricao_completa(self):
        """Exibe a parcela junto da descrição quando existir parcelamento."""
        if self.total_parcelas > 1:
            return f"{self.descricao} ({self.parcela_atual}/{self.total_parcelas})"
        return self.descricao

    def atualizar_status_automaticamente(self):
        """Mantém o status coerente com datas de vencimento e pagamento."""
        if self.status == self.STATUS_PAGO and not self.data_pagamento:
            self.data_pagamento = date.today()

        if self.data_pagamento:
            self.status = self.STATUS_PAGO
        elif self.data_vencimento < date.today():
            self.status = self.STATUS_ATRASADO
        else:
            self.status = self.STATUS_PENDENTE

    def save(self, *args, **kwargs):
        """Atualiza o status antes de salvar."""
        self.atualizar_status_automaticamente()

        if self.escopo == self.ESCOPO_INDIVIDUAL:
            self.orcamento_compartilhado = None

        if self.total_parcelas > 1 and not self.grupo_parcelas:
            self.grupo_parcelas = uuid.uuid4()

        super().save(*args, **kwargs)


class PlanoContencao(models.Model):
    """Plano premium para conter gastos por um periodo curto."""

    DURACAO_7 = 7
    DURACAO_15 = 15
    DURACAO_30 = 30
    DURACAO_CHOICES = [
        (DURACAO_7, "7 dias"),
        (DURACAO_15, "15 dias"),
        (DURACAO_30, "30 dias"),
    ]

    STATUS_ATIVO = "ATIVO"
    STATUS_FINALIZADO = "FINALIZADO"
    STATUS_CANCELADO = "CANCELADO"
    STATUS_CHOICES = [
        (STATUS_ATIVO, "Ativo"),
        (STATUS_FINALIZADO, "Finalizado"),
        (STATUS_CANCELADO, "Cancelado"),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="planos_contencao")
    titulo = models.CharField(max_length=120, default="Modo anti-descontrole")
    duracao_dias = models.PositiveSmallIntegerField(choices=DURACAO_CHOICES)
    data_inicio = models.DateField(default=date.today)
    data_fim = models.DateField()
    orcamento_total = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_ATIVO)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-data_inicio", "-id"]
        verbose_name = "Plano de Contencao"
        verbose_name_plural = "Planos de Contencao"

    def __str__(self):
        return f"{self.titulo} - {self.usuario.username}"

    @property
    def dias_totais(self):
        return max((self.data_fim - self.data_inicio).days + 1, 1)

    @property
    def dias_restantes(self):
        if self.status != self.STATUS_ATIVO:
            return 0
        restante = (self.data_fim - date.today()).days + 1
        return max(restante, 0)

    @property
    def esta_ativo(self):
        return self.status == self.STATUS_ATIVO and self.data_inicio <= date.today() <= self.data_fim

    def atualizar_status_automaticamente(self):
        """Finaliza planos ativos que ja passaram do prazo."""
        if self.status == self.STATUS_ATIVO and self.data_fim < date.today():
            self.status = self.STATUS_FINALIZADO

    def save(self, *args, **kwargs):
        self.atualizar_status_automaticamente()
        super().save(*args, **kwargs)


class LimiteCategoriaContencao(models.Model):
    """Limite de gasto definido para uma categoria durante o plano."""

    plano = models.ForeignKey(PlanoContencao, on_delete=models.CASCADE, related_name="limites")
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE, related_name="limites_contencao")
    limite = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["categoria__nome"]
        unique_together = ("plano", "categoria")
        verbose_name = "Limite por Categoria"
        verbose_name_plural = "Limites por Categoria"

    def __str__(self):
        return f"{self.categoria.nome} - R$ {self.limite}"


class MetaFinanceira(models.Model):
    """Representa um objetivo financeiro do usuario."""

    STATUS_EM_ANDAMENTO = "EM_ANDAMENTO"
    STATUS_CONCLUIDA = "CONCLUIDA"
    STATUS_PAUSADA = "PAUSADA"
    STATUS_ATRASADA = "ATRASADA"
    STATUS_CHOICES = [
        (STATUS_EM_ANDAMENTO, "Em andamento"),
        (STATUS_CONCLUIDA, "Concluída"),
        (STATUS_PAUSADA, "Pausada"),
        (STATUS_ATRASADA, "Atrasada"),
    ]

    PRIORIDADE_BAIXA = "BAIXA"
    PRIORIDADE_MEDIA = "MEDIA"
    PRIORIDADE_ALTA = "ALTA"
    PRIORIDADE_CHOICES = [
        (PRIORIDADE_BAIXA, "Baixa"),
        (PRIORIDADE_MEDIA, "Média"),
        (PRIORIDADE_ALTA, "Alta"),
    ]

    ESTRATEGIA_AGRESSIVA = "AGRESSIVA"
    ESTRATEGIA_SUAVE = "SUAVE"
    ESTRATEGIA_CONSERVADORA = "CONSERVADORA"
    ESTRATEGIA_CHOICES = [
        (ESTRATEGIA_AGRESSIVA, "Meta Agressiva"),
        (ESTRATEGIA_SUAVE, "Meta Suave"),
        (ESTRATEGIA_CONSERVADORA, "Meta Conservadora"),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name="metas")
    titulo = models.CharField(max_length=120)
    descricao = models.TextField(blank=True)
    valor_alvo = models.DecimalField(max_digits=12, decimal_places=2)
    valor_atual = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    valor_semanal_planejado = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    data_inicio = models.DateField(default=date.today)
    data_limite = models.DateField()
    estrategia = models.CharField(max_length=15, choices=ESTRATEGIA_CHOICES, default=ESTRATEGIA_SUAVE)
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
        self.valor_semanal_planejado = Decimal(str(self.valor_semanal_planejado or Decimal("0.00")))

        if self.valor_atual < Decimal("0.00"):
            self.valor_atual = Decimal("0.00")

        if self.valor_semanal_planejado < Decimal("0.00"):
            self.valor_semanal_planejado = Decimal("0.00")

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
    foto_perfil = models.FileField(upload_to="perfis/", blank=True)
    foto_perfil_binario = models.BinaryField(blank=True, null=True)
    foto_perfil_content_type = models.CharField(max_length=100, blank=True)
    foto_perfil_nome = models.CharField(max_length=255, blank=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração do Usuário"
        verbose_name_plural = "Configurações dos Usuários"

    def __str__(self):
        return f"Configurações de {self.usuario.username}"

    @property
    def foto_perfil_data_uri(self):
        """Retorna a foto em formato data URI para funcionar tambem em producao."""
        if not self.foto_perfil_binario or not self.foto_perfil_content_type:
            return ""

        dados = self.foto_perfil_binario
        if isinstance(dados, memoryview):
            dados = dados.tobytes()
        else:
            dados = bytes(dados)

        imagem_base64 = base64.b64encode(dados).decode("ascii")
        return f"data:{self.foto_perfil_content_type};base64,{imagem_base64}"

    @property
    def tem_foto_perfil(self):
        """Indica se o usuario ja possui uma foto cadastrada."""
        return bool(self.foto_perfil_binario or self.foto_perfil)

    def atualizar_foto_perfil(self, arquivo):
        """Salva a foto no banco para nao depender do armazenamento local do Render."""
        arquivo.seek(0)
        self.foto_perfil_binario = arquivo.read()
        arquivo.seek(0)
        self.foto_perfil_content_type = getattr(arquivo, "content_type", "") or "application/octet-stream"
        self.foto_perfil_nome = arquivo.name[:255]
        self.foto_perfil = ""


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

    def aplicar_beneficios_freemium(self):
        """Aplica limites e beneficios do plano gratuito sem alterar o status."""
        self.valor_mensal = Decimal("0.00")
        self.ia_habilitada = False
        self.limite_cartoes = 1
        self.limite_metas = 2
        self.limite_investimentos = 1

    def aplicar_beneficios_premium(self):
        """Aplica limites e beneficios do plano premium sem alterar o status."""
        self.valor_mensal = Decimal("10.50")
        self.ia_habilitada = True
        self.limite_cartoes = 999
        self.limite_metas = 999
        self.limite_investimentos = 999

    def ativar_freemium(self, status=None):
        """Configura o usuario para o plano freemium."""
        self.nome_plano = self.PLANO_FREEMIUM
        self.status = status or self.STATUS_ATIVO
        self.aplicar_beneficios_freemium()

    def ativar_premium(self):
        """Configura o usuario para o plano premium completo."""
        self.nome_plano = self.PLANO_PREMIUM
        self.status = self.STATUS_ATIVO
        self.aplicar_beneficios_premium()

    def save(self, *args, **kwargs):
        """Mantem coerencia automatica entre nome do plano e beneficios sem apagar status."""
        if self.nome_plano == self.PLANO_PREMIUM:
            self.aplicar_beneficios_premium()
            if self.status != self.STATUS_ATIVO:
                self.status = self.STATUS_ATIVO
        else:
            self.aplicar_beneficios_freemium()

        super().save(*args, **kwargs)


class EventoAssinatura(models.Model):
    """Registra eventos importantes do ciclo de vida da assinatura Premium."""

    TIPO_CHECKOUT_CRIADO = "CHECKOUT_CRIADO"
    TIPO_WEBHOOK_RECEBIDO = "WEBHOOK_RECEBIDO"
    TIPO_SINCRONIZACAO = "SINCRONIZACAO"
    TIPO_PREMIUM_ATIVADO = "PREMIUM_ATIVADO"
    TIPO_PREMIUM_CANCELADO = "PREMIUM_CANCELADO"
    TIPO_ACESSO_BLOQUEADO = "ACESSO_BLOQUEADO"
    TIPO_ERRO = "ERRO"
    TIPO_CHOICES = [
        (TIPO_CHECKOUT_CRIADO, "Checkout criado"),
        (TIPO_WEBHOOK_RECEBIDO, "Webhook recebido"),
        (TIPO_SINCRONIZACAO, "Sincronizacao"),
        (TIPO_PREMIUM_ATIVADO, "Premium ativado"),
        (TIPO_PREMIUM_CANCELADO, "Premium cancelado"),
        (TIPO_ACESSO_BLOQUEADO, "Acesso bloqueado"),
        (TIPO_ERRO, "Erro"),
    ]

    ORIGEM_SISTEMA = "SISTEMA"
    ORIGEM_MERCADO_PAGO = "MERCADO_PAGO"
    ORIGEM_USUARIO = "USUARIO"
    ORIGEM_CHOICES = [
        (ORIGEM_SISTEMA, "Sistema"),
        (ORIGEM_MERCADO_PAGO, "Mercado Pago"),
        (ORIGEM_USUARIO, "Usuário"),
    ]

    usuario = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="eventos_assinatura",
        blank=True,
        null=True,
    )
    plano = models.ForeignKey(
        PlanoUsuario,
        on_delete=models.SET_NULL,
        related_name="eventos_assinatura",
        blank=True,
        null=True,
    )
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    origem = models.CharField(max_length=20, choices=ORIGEM_CHOICES, default=ORIGEM_SISTEMA)
    mercado_pago_preapproval_id = models.CharField(max_length=120, blank=True, db_index=True)
    mercado_pago_evento_id = models.CharField(max_length=120, blank=True, db_index=True)
    mercado_pago_tipo = models.CharField(max_length=80, blank=True)
    mercado_pago_acao = models.CharField(max_length=80, blank=True)
    status_gateway = models.CharField(max_length=40, blank=True)
    referencia_externa = models.CharField(max_length=120, blank=True)
    valor = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    moeda = models.CharField(max_length=5, default="BRL")
    mensagem = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em", "-id"]
        verbose_name = "Evento de Assinatura"
        verbose_name_plural = "Eventos de Assinatura"
        indexes = [
            models.Index(fields=["tipo", "criado_em"]),
            models.Index(fields=["usuario", "criado_em"]),
            models.Index(fields=["status_gateway", "criado_em"]),
        ]

    def __str__(self):
        usuario = self.usuario.username if self.usuario else "sem usuario"
        return f"{self.get_tipo_display()} - {usuario}"


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
