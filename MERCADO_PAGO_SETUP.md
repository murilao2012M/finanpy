# Mercado Pago no FinanPy

Este projeto ja esta preparado para criar assinaturas reais do plano Premium via Mercado Pago.

## Variaveis de ambiente

Defina estas variaveis antes de usar o checkout real:

```powershell
MERCADO_PAGO_ACCESS_TOKEN=SEU_ACCESS_TOKEN
MERCADO_PAGO_PUBLIC_KEY=SUA_PUBLIC_KEY
MERCADO_PAGO_WEBHOOK_SECRET=SUA_SECRET_KEY_DO_WEBHOOK
MERCADO_PAGO_BASE_URL=https://api.mercadopago.com
FINANPY_SITE_URL=https://seudominio.com
```

## Onde pegar as chaves

No painel do Mercado Pago Developers:

- Access Token
- Public Key

Use primeiro as credenciais de teste e depois troque para producao.

## Fluxo implementado

1. O usuario entra em `Configuracoes`
2. Clica em `Ir para checkout premium de R$ 10,50/mes`
3. O FinanPy cria uma assinatura recorrente no Mercado Pago
4. O usuario e redirecionado ao checkout hospedado
5. Ao voltar para `/assinatura/retorno/`, o sistema sincroniza o status da assinatura
6. Quando o status vier como autorizado/ativo, o Premium e liberado

## Rotas importantes

- `/assinatura/iniciar/`
- `/assinatura/retorno/`
- `/assinatura/sincronizar/`
- `/assinatura/cancelar/`
- `/webhooks/mercado-pago/`

## Como configurar o webhook no Mercado Pago

No painel de Developers do Mercado Pago:

1. Abra sua aplicacao
2. Entre em `Webhooks`
3. Configure a URL produtiva HTTPS:

```text
https://seudominio.com/webhooks/mercado-pago/
```

4. Ative o evento:

```text
subscription_preapproval
```

5. Copie a `secret key` do webhook e salve em:

```powershell
MERCADO_PAGO_WEBHOOK_SECRET=sua_secret_key_do_webhook
```

## O que o webhook faz no FinanPy

- recebe a notificacao do Mercado Pago
- valida a assinatura do webhook, se a secret key estiver configurada
- encontra a assinatura local pelo `preapproval_id`
- consulta o status real no gateway
- ativa ou reduz o plano automaticamente conforme o status recebido

## Observacao importante

Para uso real, `FINANPY_SITE_URL` precisa ser uma URL publica HTTPS valida.
Em ambiente local, voce pode testar a criacao da assinatura, mas o retorno completo funciona melhor em deploy publico.
