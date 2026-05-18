"""Testes de seguranca e consistencia do backend do FinanPy."""

import hashlib
import hmac
import json
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import smtplib
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .forms import FotoPerfilForm
from .email_backends import BrevoAPIEmailBackend
from .ia_financeira import MODELO_ANALISE_LOCAL, gerar_analise_financeira_local
from .mercado_pago import MercadoPagoClient, MercadoPagoErro, RespostaAssinaturaMercadoPago
from .models import CartaoCredito, Categoria, ConfiguracaoUsuario, EventoAssinatura, Lancamento, MetaFinanceira, Notificacao, PlanoUsuario
from .views import (
    calcular_parcelas_futuras,
    calcular_uso_categoria,
    comparar_mes_atual_com_anterior,
    criar_lancamento_rapido,
    criar_lancamentos_recorrentes,
    detectar_gastos_pequenos_recorrentes,
    duplicar_lancamento_para_proximo_mes,
    gerar_alertas_limite_categoria,
    gerar_notificacoes_inteligentes,
    marcar_lancamento_como_pago,
    montar_acao_recomendada_hoje,
    montar_contexto_bloqueio_premium,
    montar_diagnostico_visual_ia,
    montar_fatura_cartao,
    montar_resumo_semanal,
    obter_proxima_conta_importante,
    prever_conclusao_meta,
    prever_fechamento_mes,
    mensagem_erro_email_transacional,
    sugerir_valor_para_guardar,
    sincronizar_plano_com_gateway,
    validar_assinatura_webhook_mercado_pago,
)


def adicionar_meses_for_test(data_base, quantidade_meses):
    """Replica a soma de meses para validar datas esperadas nos testes."""
    mes_final = data_base.month - 1 + quantidade_meses
    ano = data_base.year + mes_final // 12
    mes = mes_final % 12 + 1
    dia = min(data_base.day, monthrange(ano, mes)[1])
    return date(ano, mes, dia)


class PlanoUsuarioBackendTests(TestCase):
    """Garante que o plano do usuario nao seja alterado de forma indevida."""

    def setUp(self):
        """Cria um usuario autenticavel para os testes do painel."""
        self.usuario = User.objects.create_user(
            username="cliente",
            email="cliente@finanpy.com",
            password="senha-teste-123",
        )

    def test_status_cancelado_do_freemium_nao_volta_para_ativo_ao_salvar(self):
        """Protege o fluxo de cancelamento para nao reativar status automaticamente."""
        plano = PlanoUsuario.objects.create(usuario=self.usuario)

        plano.ativar_freemium(status=PlanoUsuario.STATUS_CANCELADO)
        plano.save()
        plano.refresh_from_db()

        self.assertEqual(plano.nome_plano, PlanoUsuario.PLANO_FREEMIUM)
        self.assertEqual(plano.status, PlanoUsuario.STATUS_CANCELADO)
        self.assertFalse(plano.ia_habilitada)
        self.assertFalse(plano.eh_premium)

    def test_post_manual_em_configuracoes_nao_ativa_premium(self):
        """Impede ativacao premium por POST criado manualmente no navegador."""
        plano = PlanoUsuario.objects.create(usuario=self.usuario)
        self.client.force_login(self.usuario)

        resposta = self.client.post(reverse("configuracoes"), {"ativar_premium": "1"})

        plano.refresh_from_db()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(plano.nome_plano, PlanoUsuario.PLANO_FREEMIUM)
        self.assertFalse(plano.ia_habilitada)

    def test_sincronizacao_recusa_assinatura_de_outro_usuario(self):
        """Evita que uma assinatura do gateway seja vinculada ao usuario errado."""
        plano = PlanoUsuario.objects.create(
            usuario=self.usuario,
            mercado_pago_preapproval_id="assinatura-123",
            mercado_pago_referencia_externa=f"finanpy-premium-user-{self.usuario.id}",
        )

        class ClienteMercadoPagoFalso:
            """Cliente falso para simular resposta do Mercado Pago sem rede."""

            def buscar_assinatura(self, preapproval_id):
                return {
                    "status": "authorized",
                    "external_reference": "finanpy-premium-user-999999",
                    "init_point": "https://www.mercadopago.com.br/checkout",
                }

        with patch("financeiro.views.MercadoPagoClient", return_value=ClienteMercadoPagoFalso()):
            with self.assertRaises(MercadoPagoErro):
                sincronizar_plano_com_gateway(plano)

        plano.refresh_from_db()
        self.assertEqual(plano.nome_plano, PlanoUsuario.PLANO_FREEMIUM)
        self.assertFalse(plano.ia_habilitada)
        self.assertTrue(
            EventoAssinatura.objects.filter(
                plano=plano,
                tipo=EventoAssinatura.TIPO_ERRO,
                referencia_externa="finanpy-premium-user-999999",
            ).exists()
        )

    def test_sincronizacao_autorizada_ativa_premium(self):
        """Confirma que uma assinatura valida libera os beneficios premium."""
        plano = PlanoUsuario.objects.create(
            usuario=self.usuario,
            mercado_pago_preapproval_id="assinatura-123",
            mercado_pago_referencia_externa=f"finanpy-premium-user-{self.usuario.id}",
        )

        class ClienteMercadoPagoFalso:
            """Cliente falso para simular assinatura autorizada."""

            def buscar_assinatura(self, preapproval_id):
                return {
                    "status": "authorized",
                    "external_reference": f"finanpy-premium-user-{self.usuario.id}",
                    "init_point": "https://www.mercadopago.com.br/checkout",
                }

            def __init__(self, usuario):
                self.usuario = usuario

        cliente_falso = ClienteMercadoPagoFalso(self.usuario)

        with patch("financeiro.views.MercadoPagoClient", return_value=cliente_falso):
            sincronizar_plano_com_gateway(plano)

        plano.refresh_from_db()
        self.assertEqual(plano.nome_plano, PlanoUsuario.PLANO_PREMIUM)
        self.assertEqual(plano.status, PlanoUsuario.STATUS_ATIVO)
        self.assertTrue(plano.ia_habilitada)
        self.assertTrue(EventoAssinatura.objects.filter(plano=plano, tipo=EventoAssinatura.TIPO_SINCRONIZACAO).exists())
        self.assertTrue(EventoAssinatura.objects.filter(plano=plano, tipo=EventoAssinatura.TIPO_PREMIUM_ATIVADO).exists())


class PerfilUsuarioBackendTests(TestCase):
    """Valida recursos sensiveis do perfil do usuario."""

    def setUp(self):
        """Cria usuario e configuracao para testar a foto de perfil."""
        self.usuario = User.objects.create_user(
            username="perfil",
            email="perfil@finanpy.com",
            password="senha-teste-123",
        )
        self.configuracao = ConfiguracaoUsuario.objects.create(usuario=self.usuario)

    def test_foto_de_perfil_salva_no_banco_e_gera_data_uri(self):
        """Garante que o avatar carregue em producao sem depender da pasta media."""
        png_minimo = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c636000000200015d0b2a0b0000000049454e44ae426082"
        )
        arquivo = SimpleUploadedFile("avatar.png", png_minimo, content_type="image/png")
        form = FotoPerfilForm(files={"foto-foto_perfil": arquivo}, prefix="foto")

        self.assertTrue(form.is_valid(), form.errors)

        form.save(self.configuracao)
        self.configuracao.refresh_from_db()

        self.assertEqual(self.configuracao.foto_perfil_content_type, "image/png")
        self.assertTrue(self.configuracao.foto_perfil_binario)
        self.assertTrue(self.configuracao.foto_perfil_data_uri.startswith("data:image/png;base64,"))


class CadastroUsuarioEmailTests(TestCase):
    """Garante que falhas de SMTP nao derrubem o cadastro em producao."""

    def dados_cadastro(self, username="novo-cliente", email="novo@finanpy.com"):
        """Retorna payload valido para o formulario nativo de cadastro."""
        return {
            "username": username,
            "first_name": "Novo Cliente",
            "email": email,
            "password1": "Senha-forte-123",
            "password2": "Senha-forte-123",
        }

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_cadastro_cria_usuario_inativo_quando_email_e_enviado(self):
        """Cadastro bem-sucedido cria usuario inativo e redireciona para login."""
        resposta = self.client.post(reverse("registrar_usuario"), self.dados_cadastro())

        usuario = User.objects.get(username="novo-cliente")
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], reverse("login"))
        self.assertFalse(usuario.is_active)

    def test_falha_no_envio_de_email_nao_gera_internal_server_error(self):
        """Se o SMTP falhar, a tela volta com erro e o usuario nao fica preso inativo."""
        with patch("financeiro.views.auth.enviar_email_confirmacao_cadastro", side_effect=Exception("SMTP fora")):
            resposta = self.client.post(reverse("registrar_usuario"), self.dados_cadastro())

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(User.objects.filter(username="novo-cliente").exists())
        self.assertContains(resposta, "e-mail de confirmação")

    def test_traduz_erro_de_autenticacao_smtp(self):
        """Erro de login SMTP precisa apontar para credenciais da Brevo."""
        erro = smtplib.SMTPAuthenticationError(535, b"Authentication failed")

        mensagem = mensagem_erro_email_transacional(erro)

        self.assertIn("SMTP Key da Brevo", mensagem)

    def test_traduz_erro_de_remetente_recusado(self):
        """Erro de remetente precisa orientar sobre DEFAULT_FROM_EMAIL."""
        erro = smtplib.SMTPSenderRefused(550, "Sender rejected", "suporte@finanpy.com")

        mensagem = mensagem_erro_email_transacional(erro)

        self.assertIn("DEFAULT_FROM_EMAIL", mensagem)

    @override_settings(
        BREVO_API_KEY="chave-api-teste",
        DEFAULT_FROM_EMAIL="FinanPy <suporte@finanpy.com.br>",
    )
    def test_backend_brevo_api_monta_payload_transacional(self):
        """Backend HTTP da Brevo precisa montar payload sem usar porta SMTP."""
        backend = BrevoAPIEmailBackend()
        payloads = []

        def postar_payload_fake(payload):
            payloads.append(payload)
            return True

        backend._postar_payload = postar_payload_fake
        mensagem = EmailMessage(
            subject="Confirme seu cadastro",
            body="Clique no link para ativar sua conta.",
            from_email="FinanPy <suporte@finanpy.com.br>",
            to=["cliente@exemplo.com"],
        )

        enviados = backend.send_messages([mensagem])

        self.assertEqual(enviados, 1)
        self.assertEqual(payloads[0]["sender"]["email"], "suporte@finanpy.com.br")
        self.assertEqual(payloads[0]["to"][0]["email"], "cliente@exemplo.com")
        self.assertEqual(payloads[0]["subject"], "Confirme seu cadastro")


class AnaliseFinanceiraLocalTests(TestCase):
    """Garante que a inteligencia financeira local funcione sem API externa."""

    def test_diagnostico_local_gera_schema_da_tela(self):
        """Valida o formato usado pela tela de Analise Financeira e Relatorios."""
        contexto = {
            "indicadores": {
                "total_receitas": "5000.00",
                "total_despesas": "4200.00",
                "saldo_previsto": "800.00",
                "saldo_mes": "500.00",
                "total_pendente": "300.00",
                "total_atrasado": "0.00",
                "percentual_despesas_sobre_receitas": "84.00",
            },
            "contagens": {
                "contas_pendentes": 2,
                "contas_atrasadas": 0,
                "metas_total": 1,
                "metas_concluidas": 0,
                "investimentos_total": 1,
                "investimentos_ativos": 1,
            },
            "categorias_despesa": [{"nome": "Alimentacao", "total": "1200.00"}],
            "principal_categoria_despesa": "Alimentacao",
            "investimentos": {
                "total_atual": "1500.00",
                "rentabilidade_total": "50.00",
            },
        }

        analise, resposta_bruta = gerar_analise_financeira_local(contexto)

        self.assertEqual(resposta_bruta["modelo"], MODELO_ANALISE_LOCAL)
        self.assertIn(analise["saude_financeira"], {"OTIMA", "ESTAVEL", "ATENCAO", "CRITICA"})
        self.assertTrue(analise["resumo_executivo"])
        self.assertIn("Saldo previsto", analise["metricas_resumo"])
        self.assertGreaterEqual(len(analise["sinais_positivos"]), 2)
        self.assertGreaterEqual(len(analise["alertas_prioritarios"]), 2)
        self.assertGreaterEqual(len(analise["oportunidades"]), 2)
        self.assertGreaterEqual(len(analise["plano_acao"]), 3)


class AcaoRecomendadaHojeTests(TestCase):
    """Garante que o dashboard escolha uma acao diaria util e priorizada."""

    def setUp(self):
        """Cria usuario, periodo atual e categorias basicas."""
        self.usuario = User.objects.create_user(
            username="acao-hoje",
            email="acao@finanpy.com",
            password="senha-teste-123",
        )
        hoje = date.today()
        self.inicio_mes = date(hoje.year, hoje.month, 1)
        self.fim_mes = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])
        self.categoria_despesa = Categoria.objects.create(
            usuario=self.usuario,
            nome="Moradia",
            tipo=Categoria.TIPO_DESPESA,
        )
        self.categoria_receita = Categoria.objects.create(
            usuario=self.usuario,
            nome="Salario",
            tipo=Categoria.TIPO_RECEITA,
        )

    def criar_lancamento(self, **kwargs):
        """Facilita a criacao de lancamentos do mes atual."""
        dados = {
            "usuario": self.usuario,
            "tipo": Lancamento.TIPO_DESPESA,
            "descricao": "Despesa teste",
            "valor": Decimal("100.00"),
            "categoria": self.categoria_despesa,
            "data_competencia": self.inicio_mes,
            "data_vencimento": date.today(),
            "status": Lancamento.STATUS_PENDENTE,
            "forma_pagamento": Lancamento.FORMA_PIX,
        }
        dados.update(kwargs)
        return Lancamento.objects.create(**dados)

    def test_prioriza_conta_atrasada(self):
        """Conta atrasada precisa aparecer antes de qualquer recomendacao leve."""
        self.criar_lancamento(
            descricao="Aluguel",
            valor=Decimal("1200.00"),
            data_vencimento=date.today() - timedelta(days=2),
        )

        acao = montar_acao_recomendada_hoje(self.usuario, self.inicio_mes, self.fim_mes)

        self.assertEqual(acao["tipo"], "danger")
        self.assertEqual(acao["prioridade"], "alta")
        self.assertEqual(acao["icone"], "fa-triangle-exclamation")
        self.assertIn("Resolva Uma Conta Atrasada Hoje", acao["titulo"])
        self.assertIn("status=ATRASADO", acao["botao_url"])

    def test_recomenda_categoria_mais_cara_quando_nao_ha_urgencia(self):
        """Sem atraso, vencimento ou saldo negativo, foca no maior gasto do mes."""
        self.criar_lancamento(
            tipo=Lancamento.TIPO_RECEITA,
            descricao="Salario",
            valor=Decimal("5000.00"),
            categoria=self.categoria_receita,
            status=Lancamento.STATUS_PAGO,
            data_pagamento=date.today(),
        )
        self.criar_lancamento(
            descricao="Mercado",
            valor=Decimal("850.00"),
            status=Lancamento.STATUS_PAGO,
            data_pagamento=date.today(),
        )

        acao = montar_acao_recomendada_hoje(self.usuario, self.inicio_mes, self.fim_mes)

        self.assertEqual(acao["tipo"], "warning")
        self.assertEqual(acao["prioridade"], "media")
        self.assertIn("Categoria Mais Cara", acao["titulo"])
        self.assertIn("categoria=", acao["botao_url"])

    def test_previsao_de_fechamento_positivo_com_risco_baixo(self):
        """Receitas maiores que despesas indicam fechamento positivo."""
        self.criar_lancamento(
            tipo=Lancamento.TIPO_RECEITA,
            descricao="Salario",
            valor=Decimal("1000.00"),
            categoria=self.categoria_receita,
            status=Lancamento.STATUS_PAGO,
            data_pagamento=date.today(),
        )
        self.criar_lancamento(
            descricao="Mercado",
            valor=Decimal("680.00"),
            status=Lancamento.STATUS_PAGO,
            data_pagamento=date.today(),
        )

        previsao = prever_fechamento_mes(self.usuario, self.inicio_mes, self.fim_mes)

        self.assertEqual(previsao["saldo_previsto"], Decimal("320.00"))
        self.assertEqual(previsao["status"], "POSITIVO")
        self.assertEqual(previsao["risco"], "baixo")
        self.assertEqual(previsao["mensagem"], "Você tende a fechar o mês positivo.")

    def test_previsao_de_fechamento_negativo_com_pendentes_e_atrasados(self):
        """Pendentes e atrasados entram na previsao e aumentam o risco."""
        self.criar_lancamento(
            tipo=Lancamento.TIPO_RECEITA,
            descricao="Salario",
            valor=Decimal("1000.00"),
            categoria=self.categoria_receita,
            status=Lancamento.STATUS_PAGO,
            data_pagamento=date.today(),
        )
        self.criar_lancamento(
            descricao="Conta pendente",
            valor=Decimal("700.00"),
            data_vencimento=date.today() + timedelta(days=5),
            status=Lancamento.STATUS_PENDENTE,
        )
        self.criar_lancamento(
            descricao="Conta atrasada",
            valor=Decimal("500.00"),
            data_vencimento=date.today() - timedelta(days=2),
            status=Lancamento.STATUS_PENDENTE,
        )

        previsao = prever_fechamento_mes(self.usuario, self.inicio_mes, self.fim_mes)

        self.assertEqual(previsao["saldo_previsto"], Decimal("-200.00"))
        self.assertEqual(previsao["status"], "NEGATIVO")
        self.assertEqual(previsao["risco"], "alto")

    def test_proxima_conta_prioriza_atrasada_mais_antiga(self):
        """Conta atrasada mais antiga deve ganhar de conta vencendo em breve."""
        self.criar_lancamento(
            descricao="Internet",
            valor=Decimal("120.00"),
            data_vencimento=date.today() - timedelta(days=1),
            status=Lancamento.STATUS_PENDENTE,
        )
        self.criar_lancamento(
            descricao="Condominio",
            valor=Decimal("450.00"),
            data_vencimento=date.today() - timedelta(days=5),
            status=Lancamento.STATUS_PENDENTE,
        )
        self.criar_lancamento(
            descricao="Energia",
            valor=Decimal("180.00"),
            data_vencimento=date.today() + timedelta(days=2),
            status=Lancamento.STATUS_PENDENTE,
        )

        conta = obter_proxima_conta_importante(self.usuario, self.inicio_mes, self.fim_mes)

        self.assertEqual(conta["descricao"], "Condominio")
        self.assertEqual(conta["valor"], Decimal("450.00"))
        self.assertEqual(conta["status"], "atrasada")
        self.assertIn("atrasada", conta["mensagem"])

    def test_proxima_conta_identifica_vencimento_em_breve(self):
        """Sem atraso ou vencimento hoje, retorna conta dos proximos 3 dias."""
        self.criar_lancamento(
            descricao="Internet",
            valor=Decimal("120.00"),
            data_vencimento=date.today() + timedelta(days=2),
            status=Lancamento.STATUS_PENDENTE,
        )

        conta = obter_proxima_conta_importante(self.usuario, self.inicio_mes, self.fim_mes)

        self.assertEqual(conta["descricao"], "Internet")
        self.assertEqual(conta["valor"], Decimal("120.00"))
        self.assertEqual(conta["status"], "vence_em_breve")
        self.assertEqual(conta["mensagem"], "Essa conta vence em 2 dias.")

    def test_detecta_gastos_pequenos_recorrentes_por_descricao(self):
        """Pequenas compras repetidas precisam aparecer como alerta acumulado."""
        categoria_cafe = Categoria.objects.create(
            usuario=self.usuario,
            nome="Café",
            tipo=Categoria.TIPO_DESPESA,
        )
        for indice in range(12):
            self.criar_lancamento(
                descricao=f"Café padaria {indice + 1}",
                valor=Decimal("8.00"),
                categoria=categoria_cafe,
                status=Lancamento.STATUS_PAGO,
                data_pagamento=date.today(),
            )

        recorrencias = detectar_gastos_pequenos_recorrentes(self.usuario, self.inicio_mes, self.fim_mes)

        self.assertTrue(recorrencias)
        recorrencia_cafe = next(item for item in recorrencias if item["descricao_base"] == "Café")
        self.assertEqual(recorrencia_cafe["quantidade"], 12)
        self.assertEqual(recorrencia_cafe["total"], Decimal("96.00"))
        self.assertEqual(recorrencia_cafe["mensagem"], "Pequenos gastos com café somaram R$ 96,00.")

    def test_alerta_limite_categoria_em_oitenta_por_cento(self):
        """Categoria acima de 80 por cento do limite mensal gera alerta."""
        self.categoria_despesa.limite_mensal = Decimal("300.00")
        self.categoria_despesa.save()
        self.criar_lancamento(
            descricao="Delivery",
            valor=Decimal("265.00"),
            status=Lancamento.STATUS_PAGO,
            data_pagamento=date.today(),
        )

        uso = calcular_uso_categoria(self.usuario, self.categoria_despesa, self.inicio_mes, self.fim_mes)
        alertas = gerar_alertas_limite_categoria(self.usuario, self.inicio_mes, self.fim_mes)

        self.assertEqual(uso["categoria"], "Moradia")
        self.assertEqual(uso["limite"], Decimal("300.00"))
        self.assertEqual(uso["gasto"], Decimal("265.00"))
        self.assertEqual(uso["percentual"], 88.3)
        self.assertEqual(uso["nivel"], "ATENCAO")
        self.assertEqual(alertas[0]["nivel"], "ATENCAO")

    def test_alerta_limite_categoria_em_estouro(self):
        """Categoria acima de 100 por cento do limite mensal vira estouro."""
        self.categoria_despesa.limite_mensal = Decimal("300.00")
        self.categoria_despesa.save()
        self.criar_lancamento(
            descricao="Aluguel extra",
            valor=Decimal("320.00"),
            status=Lancamento.STATUS_PAGO,
            data_pagamento=date.today(),
        )

        uso = calcular_uso_categoria(self.usuario, self.categoria_despesa, self.inicio_mes, self.fim_mes)

        self.assertEqual(uso["percentual"], 106.7)
        self.assertEqual(uso["nivel"], "ESTOURO")

    def test_fluxo_de_acoes_rapidas_de_lancamento(self):
        """Dashboard e tabela conseguem criar, pagar, duplicar e gerar recorrencias."""
        lancamento = criar_lancamento_rapido(
            self.usuario,
            {
                "tipo": Lancamento.TIPO_DESPESA,
                "descricao": "Internet",
                "valor": "120,50",
                "categoria": self.categoria_despesa.id,
                "data": self.inicio_mes.isoformat(),
                "status": Lancamento.STATUS_PENDENTE,
            },
        )

        pago = marcar_lancamento_como_pago(self.usuario, lancamento.id)
        duplicado = duplicar_lancamento_para_proximo_mes(self.usuario, lancamento.id)
        recorrentes = criar_lancamentos_recorrentes(self.usuario, lancamento.id, 3)

        self.assertEqual(lancamento.valor, Decimal("120.50"))
        self.assertEqual(pago.status, Lancamento.STATUS_PAGO)
        self.assertEqual(pago.data_pagamento, date.today())
        self.assertEqual(duplicado.data_competencia, adicionar_meses_for_test(self.inicio_mes, 1))
        self.assertIsNone(duplicado.data_pagamento)
        self.assertGreaterEqual(len(recorrentes), 2)

    def test_fatura_cartao_e_parcelas_futuras(self):
        """Cartao precisa mostrar fatura mensal e compromisso parcelado futuro."""
        cartao = CartaoCredito.objects.create(
            usuario=self.usuario,
            nome="Nubank",
            limite=Decimal("2000.00"),
        )
        self.criar_lancamento(
            descricao="Notebook",
            valor=Decimal("850.00"),
            forma_pagamento=Lancamento.FORMA_CREDITO,
            cartao=cartao,
            compra_parcelada=True,
            parcela_atual=1,
            total_parcelas=3,
        )
        self.criar_lancamento(
            descricao="Notebook",
            valor=Decimal("850.00"),
            forma_pagamento=Lancamento.FORMA_CREDITO,
            cartao=cartao,
            compra_parcelada=True,
            parcela_atual=2,
            total_parcelas=3,
            data_competencia=adicionar_meses_for_test(self.inicio_mes, 1),
            data_vencimento=adicionar_meses_for_test(date.today(), 1),
        )

        fatura = montar_fatura_cartao(self.usuario, cartao.id, self.inicio_mes, self.fim_mes)
        parcelas = calcular_parcelas_futuras(self.usuario, meses=2)

        self.assertEqual(fatura["total_fatura"], Decimal("850.00"))
        self.assertEqual(fatura["limite_disponivel"], Decimal("1150.00"))
        self.assertEqual(fatura["percentual_usado"], 42.5)
        self.assertEqual(parcelas[0]["total_parcelado"], Decimal("850.00"))

    def test_previsoes_resumo_diagnostico_e_bloqueio_premium(self):
        """Novos blocos inteligentes retornam estruturas prontas para os templates."""
        self.criar_lancamento(
            tipo=Lancamento.TIPO_RECEITA,
            descricao="Salario",
            valor=Decimal("3000.00"),
            categoria=self.categoria_receita,
            status=Lancamento.STATUS_PAGO,
            data_pagamento=date.today(),
        )
        self.criar_lancamento(
            descricao="Mercado",
            valor=Decimal("1000.00"),
            status=Lancamento.STATUS_PAGO,
            data_pagamento=date.today(),
        )
        meta = MetaFinanceira.objects.create(
            usuario=self.usuario,
            titulo="Reserva",
            valor_alvo=Decimal("2000.00"),
            valor_atual=Decimal("500.00"),
            valor_semanal_planejado=Decimal("100.00"),
            data_limite=date.today() + timedelta(days=180),
        )
        analise = {
            "saude_financeira": "ATENCAO",
            "resumo_executivo": "Resumo local",
            "sinais_positivos": ["Saldo positivo"],
            "alertas_prioritarios": ["Acompanhe despesas"],
            "oportunidades": ["Guardar parte do saldo"],
            "plano_acao": [
                {"titulo": "Revisar contas", "descricao": "Cheque pendencias", "prazo": "7_DIAS"},
                {"titulo": "Planejar mes", "descricao": "Defina cortes", "prazo": "30_DIAS"},
            ],
        }

        comparativo = comparar_mes_atual_com_anterior(self.usuario, self.inicio_mes, self.fim_mes)
        sugestao = sugerir_valor_para_guardar(self.usuario, self.inicio_mes, self.fim_mes)
        resumo = montar_resumo_semanal(self.usuario)
        previsao_meta = prever_conclusao_meta(meta)
        diagnostico = montar_diagnostico_visual_ia(analise)
        bloqueio = montar_contexto_bloqueio_premium(self.usuario, "ia")

        self.assertIn("despesas_variacao", comparativo)
        self.assertGreater(sugestao["valor_sugerido"], Decimal("0.00"))
        self.assertEqual(resumo["titulo"], "Resumo Da Semana")
        self.assertIn(previsao_meta["status_previsao"], {"NO_PRAZO", "FORA_DO_PRAZO", "SEM_RITMO", "CONCLUIDA"})
        self.assertEqual(diagnostico["saude"], "ATENCAO")
        self.assertEqual(len(diagnostico["acoes_7_dias"]), 1)
        self.assertEqual(bloqueio["botao_texto"], "Assinar Premium")

    def test_notificacoes_inteligentes_nao_duplicam_no_mesmo_dia(self):
        """Alertas automaticos precisam aparecer no sininho sem repetir em loop."""
        self.criar_lancamento(
            descricao="Boleto atrasado",
            valor=Decimal("200.00"),
            data_vencimento=date.today() - timedelta(days=2),
            status=Lancamento.STATUS_PENDENTE,
        )

        primeira_execucao = gerar_notificacoes_inteligentes(self.usuario, self.inicio_mes, self.fim_mes)
        segunda_execucao = gerar_notificacoes_inteligentes(self.usuario, self.inicio_mes, self.fim_mes)

        self.assertTrue(primeira_execucao)
        self.assertEqual(segunda_execucao, [])
        self.assertTrue(Notificacao.objects.filter(usuario=self.usuario, titulo="Conta Atrasada").exists())


class MercadoPagoClientTests(TestCase):
    """Testa detalhes do payload enviado ao Mercado Pago."""

    def setUp(self):
        """Cria usuario com e-mail para montar assinatura."""
        self.usuario = User.objects.create_user(
            username="pagador",
            email="pagador@finanpy.com",
            password="senha-teste-123",
        )

    @override_settings(
        MERCADO_PAGO_ACCESS_TOKEN="token-fake",
        FINANPY_SITE_URL="https://finanpy.onrender.com",
    )
    def test_criacao_de_assinatura_envia_webhook_publico(self):
        """Assinaturas precisam apontar para o webhook publico do FinanPy."""
        payloads = []

        def request_falsa(self, metodo, caminho, payload=None):
            payloads.append(payload)
            return {
                "id": "preapproval-123",
                "init_point": "https://www.mercadopago.com.br/checkout/fake",
                "status": "pending",
                "external_reference": payload["external_reference"],
            }

        with patch.object(MercadoPagoClient, "_request", request_falsa):
            resposta = MercadoPagoClient().criar_assinatura_premium(
                self.usuario,
                "finanpy-premium-user-1",
            )

        self.assertEqual(resposta.identificador, "preapproval-123")
        self.assertEqual(
            payloads[0]["notification_url"],
            "https://finanpy.onrender.com/webhooks/mercado-pago/?source_news=webhooks",
        )
        self.assertEqual(payloads[0]["back_url"], "https://finanpy.onrender.com/assinatura/retorno/")


class AcoesSensiveisBackendTests(TestCase):
    """Valida que acoes sensiveis exigem POST e protecao CSRF na interface."""

    def setUp(self):
        """Prepara usuario autenticado para acessar rotas protegidas."""
        self.usuario = User.objects.create_user(
            username="assinante",
            email="assinante@finanpy.com",
            password="senha-teste-123",
        )
        self.client.force_login(self.usuario)

    def test_logout_e_pagamento_nao_aceitam_get(self):
        """Evita que links simples executem logout, checkout ou sincronizacao."""
        rotas_post = [
            "logout",
            "checkout_premium",
            "iniciar_checkout_premium",
            "sincronizar_assinatura_premium",
            "cancelar_assinatura_premium",
        ]

        for nome_rota in rotas_post:
            with self.subTest(nome_rota=nome_rota):
                resposta = self.client.get(reverse(nome_rota))
                self.assertEqual(resposta.status_code, 405)

    def test_checkout_cria_evento_de_assinatura(self):
        """Garante que o checkout real fica auditavel no banco."""

        class ClienteMercadoPagoFalso:
            """Cliente falso para criar checkout sem chamada de rede."""

            def criar_assinatura_premium(self, usuario, referencia_externa):
                return RespostaAssinaturaMercadoPago(
                    identificador="preapproval-abc",
                    init_point="https://www.mercadopago.com.br/checkout/fake",
                    status="pending",
                    referencia_externa=referencia_externa,
                )

        with patch("financeiro.views.MercadoPagoClient", return_value=ClienteMercadoPagoFalso()):
            resposta = self.client.post(reverse("checkout_premium"))

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], "https://www.mercadopago.com.br/checkout/fake")
        self.assertTrue(
            EventoAssinatura.objects.filter(
                usuario=self.usuario,
                tipo=EventoAssinatura.TIPO_CHECKOUT_CRIADO,
                mercado_pago_preapproval_id="preapproval-abc",
                status_gateway="pending",
            ).exists()
        )

    def test_recurso_premium_bloqueado_gera_evento(self):
        """Tentativas freemium em recurso premium entram no historico."""
        resposta = self.client.post(reverse("simulador_decisoes"), data={})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Desbloqueie")
        self.assertTrue(
            EventoAssinatura.objects.filter(
                usuario=self.usuario,
                tipo=EventoAssinatura.TIPO_ACESSO_BLOQUEADO,
            ).exists()
        )

    @override_settings(DEBUG=False, MERCADO_PAGO_WEBHOOK_SECRET="")
    def test_webhook_sem_secret_e_recusado_em_producao(self):
        """Em producao, webhook sem secret configurado nao pode ser aceito."""
        request = RequestFactory().post(reverse("webhook_mercado_pago"))

        assinatura_valida = validar_assinatura_webhook_mercado_pago(request, "preapproval-123")

        self.assertFalse(assinatura_valida)

    @override_settings(DEBUG=False, MERCADO_PAGO_WEBHOOK_SECRET="webhook-secret-test")
    def test_webhook_valido_registra_evento_e_sincroniza_plano(self):
        """Webhook valido precisa registrar auditoria e sincronizar o Premium."""
        plano = PlanoUsuario.objects.create(
            usuario=self.usuario,
            mercado_pago_preapproval_id="abc123",
            mercado_pago_referencia_externa=f"finanpy-premium-user-{self.usuario.id}",
        )
        payload = {
            "type": "subscription_preapproval",
            "action": "updated",
            "data": {"id": "abc123"},
        }
        timestamp = "1710000000"
        request_id = "request-123"
        manifesto = f"id:abc123;request-id:{request_id};ts:{timestamp};"
        assinatura = hmac.new(
            b"webhook-secret-test",
            manifesto.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        class ClienteMercadoPagoFalso:
            """Cliente falso para simular assinatura ativa no webhook."""

            def buscar_assinatura(self, preapproval_id):
                return {
                    "status": "authorized",
                    "external_reference": f"finanpy-premium-user-{self.usuario.id}",
                    "init_point": "https://www.mercadopago.com.br/checkout",
                }

            def __init__(self, usuario):
                self.usuario = usuario

        with patch("financeiro.views.MercadoPagoClient", return_value=ClienteMercadoPagoFalso(self.usuario)):
            resposta = self.client.post(
                f"{reverse('webhook_mercado_pago')}?data.id=abc123",
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_X_SIGNATURE=f"ts={timestamp},v1={assinatura}",
                HTTP_X_REQUEST_ID=request_id,
            )

        plano.refresh_from_db()
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(plano.eh_premium)
        self.assertTrue(
            EventoAssinatura.objects.filter(
                plano=plano,
                tipo=EventoAssinatura.TIPO_WEBHOOK_RECEBIDO,
                mercado_pago_evento_id="request-123",
            ).exists()
        )
