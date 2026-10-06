#!/usr/bin/env bash
# Crea el .env del servidor con secretos aleatorios.   Uso:  bash deploy/aws/generar-env.sh tu-dominio.com
#
# No escribe ninguna contraseña de correo: SMTP_USER, SMTP_PASSWORD, MAIL_FROM y CONTACT_EMAIL los escribes tú a mano
# después (nano .env). Los secretos generados no se imprimen. Nunca sobrescribe un .env que ya exista.
set -euo pipefail
cd "$(dirname "$0")/../.."

domain="${1:-}"
if [[ ! "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "Uso: bash deploy/aws/generar-env.sh tu-dominio.com   (solo letras, números, puntos y guiones)" >&2
  exit 1
fi
if [ -e .env ]; then
  echo "Ya existe .env: no se sobrescribe. Bórralo si quieres empezar de nuevo." >&2
  exit 1
fi
command -v openssl >/dev/null || { echo "Falta openssl (sudo apt-get install -y openssl)." >&2; exit 1; }

cp .env.example .env
chmod 600 .env
for name in JWT_SECRET_KEY INTERNAL_API_TOKEN WEB_SECRET_KEY POSTGRES_PASSWORD AUTH_DB_PASSWORD WEATHER_DB_PASSWORD FORECAST_DB_PASSWORD CATALOG_DB_PASSWORD; do
  sed -i "s|^${name}=.*|${name}=$(openssl rand -hex 32)|" .env
done
sed -i "s|^DOMAIN=.*|DOMAIN=${domain}|; s|^TRUSTED_PROXY_HOPS=.*|TRUSTED_PROXY_HOPS=1|" .env
printf '\n# Con esto basta `docker compose up -d --build` (suma Caddy, el HTTPS, al compose principal).\nCOMPOSE_FILE=docker-compose.yml:deploy/aws/docker-compose.aws.yml\n' >> .env

echo "Listo: .env creado con secretos aleatorios para ${domain}."
echo "Falta lo que solo tú puedes escribir:  nano .env  ->  SMTP_HOST, SMTP_USER, SMTP_PASSWORD, MAIL_FROM y CONTACT_EMAIL"
