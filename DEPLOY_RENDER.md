# Deploy do FinanPy no Render

Este guia deixa o deploy do FinanPy previsivel no Render e organiza a configuracao final do Mercado Pago.

## 1. Antes de publicar

1. Faça commit e push de todas as alterações para o GitHub.
2. No Mercado Pago, rotacione a `PUBLIC_KEY` e o `ACCESS_TOKEN` se elas ja foram expostas em conversa, print ou repositorio.
3. Confirme que o repositorio nao possui `.env`, `db.sqlite3`, `media/` ou `staticfiles/` versionados.

## 2. Criar o Blueprint no Render

1. Acesse o Render Dashboard.
2. Clique em `New` e depois em `Blueprint`.
3. Conecte o repositorio do GitHub do FinanPy.
4. O Render vai ler o arquivo `render.yaml`.
5. Confirme a criacao do banco `finanpy-db` e do web service `finanpy`.

O `render.yaml` ja define:

- Banco PostgreSQL.
- Build command: `bash build.sh`.
- Migrations dentro do `build.sh`, porque o plano gratuito do Render nao aceita `preDeployCommand`.
- Start command: `gunicorn finanpy.wsgi:application`.
- Health check: `/healthz/`.

## 3. Variaveis obrigatorias no Render

Preencha estas variaveis no web service:

```text
DJANGO_DEBUG=False
DJANGO_ENABLE_HTTPS=True
DJANGO_ALLOWED_HOSTS=SEU-SERVICO.onrender.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://SEU-SERVICO.onrender.com
FINANPY_SITE_URL=https://SEU-SERVICO.onrender.com
MERCADO_PAGO_PUBLIC_KEY=SUA_PUBLIC_KEY_NOVA
MERCADO_PAGO_ACCESS_TOKEN=SEU_ACCESS_TOKEN_NOVO
MERCADO_PAGO_WEBHOOK_SECRET=SUA_SECRET_DO_WEBHOOK
MERCADO_PAGO_BASE_URL=https://api.mercadopago.com
OPENAI_API_KEY=SUA_CHAVE_OPENAI
OPENAI_MODEL=gpt-5-mini
OPENAI_TIMEOUT_SECONDS=60
DJANGO_LOG_LEVEL=INFO
```

O `DATABASE_URL` e o `DJANGO_SECRET_KEY` sao gerados pelo Render via `render.yaml`.

## 4. Configurar webhook no Mercado Pago

Use a URL publica do Render:

```text
https://SEU-SERVICO.onrender.com/webhooks/mercado-pago/
```

O FinanPy tambem envia essa URL automaticamente no campo `notification_url` quando cria a assinatura Premium:

```text
https://SEU-SERVICO.onrender.com/webhooks/mercado-pago/?source_news=webhooks
```

Eventos que devem ser ativados:

```text
subscription_preapproval
subscription_authorized_payment
```

Observacao: para assinaturas, o Mercado Pago pode priorizar a configuracao enviada durante a criacao do pagamento. Por isso o backend ja manda `notification_url` no checkout Premium.

Depois de salvar, copie a assinatura secreta gerada pelo Mercado Pago e coloque em:

```text
MERCADO_PAGO_WEBHOOK_SECRET
```

## 5. Teste apos o deploy

1. Acesse `https://SEU-SERVICO.onrender.com/healthz/`.
2. Crie uma conta normal no FinanPy.
3. Entre em `Configuracoes`.
4. Clique no checkout Premium.
5. Conclua o fluxo no Mercado Pago.
6. Volte ao FinanPy e confira se o plano foi sincronizado.
7. Em `Configuracoes`, confira o `Historico da assinatura`.

## 6. Comandos uteis no Render Shell

Criar superusuario:

```bash
python manage.py createsuperuser
```

Conferir configuracao de producao:

```bash
python manage.py check --deploy
```

Aplicar migrations manualmente, se necessario:

```bash
python manage.py migrate
```
