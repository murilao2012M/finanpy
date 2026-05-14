# HTTPS no FinanPy

Este projeto ja esta preparado para trabalhar com SSL/HTTPS no Django.

Importante:
- O certificado SSL real nao e criado dentro do Django.
- O certificado precisa ser emitido pelo seu provedor, proxy reverso ou servidor web.
- O Django apenas recebe a requisicao ja protegida e aplica as regras de seguranca.

## 1. Variaveis de ambiente recomendadas

Defina estas variaveis no ambiente de producao:

```powershell
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=sua-chave-secreta-forte
DJANGO_ALLOWED_HOSTS=seudominio.com,www.seudominio.com
DJANGO_ENABLE_HTTPS=True
DJANGO_CSRF_TRUSTED_ORIGINS=https://seudominio.com,https://www.seudominio.com
DJANGO_HSTS_SECONDS=31536000
DJANGO_HSTS_INCLUDE_SUBDOMAINS=True
DJANGO_HSTS_PRELOAD=True
```

## 2. O que o projeto faz quando HTTPS esta ativo

Quando `DJANGO_ENABLE_HTTPS=True`, o FinanPy:

- redireciona HTTP para HTTPS
- usa cookies de sessao apenas em conexao segura
- usa cookies CSRF apenas em conexao segura
- aceita o cabecalho `X-Forwarded-Proto` para proxies como Nginx, Render e Railway
- ativa HSTS para reforcar a navegacao segura

## 3. Como emitir o certificado SSL

Voce pode fazer isso no seu ambiente de deploy:

### Opcao A: Nginx + Certbot

- aponte o dominio para o servidor
- instale Nginx
- instale Certbot
- emita o certificado com Let's Encrypt
- encaminhe o trafego HTTPS para o Django/Gunicorn

### Opcao B: Hospedagens com SSL automatico

Servicos como Render, Railway, Vercel, Cloudflare ou similares geralmente:

- emitem SSL automaticamente
- renovam o certificado
- repassam a requisicao segura para a aplicacao

Nesse caso, basta configurar corretamente:

- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_ENABLE_HTTPS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`

## 4. Exemplo de proxy com Nginx

Exemplo conceitual:

```nginx
server {
    listen 80;
    server_name seudominio.com www.seudominio.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name seudominio.com www.seudominio.com;

    ssl_certificate /etc/letsencrypt/live/seudominio.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/seudominio.com/privkey.pem;

    location /static/ {
        alias /caminho/do/projeto/static/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 5. Como testar localmente

No desenvolvimento local, o projeto continua funcionando sem HTTPS.

Se quiser simular ambiente de producao, voce pode definir:

```powershell
$env:DJANGO_DEBUG="False"
$env:DJANGO_ENABLE_HTTPS="True"
```

Mas o ideal e testar HTTPS real ja no ambiente de deploy com dominio valido.
