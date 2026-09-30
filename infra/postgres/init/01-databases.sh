#!/bin/bash
# Crea una base y un rol por microservicio (mínimos privilegios: cada rol solo es dueño de su base).
# Las contraseñas llegan por variables de entorno del contenedor de PostgreSQL.
set -euo pipefail

for svc in auth weather forecast catalog; do
  var="$(echo "${svc}" | tr '[:lower:]' '[:upper:]')_DB_PASSWORD"
  password="${!var:?falta ${var}}"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE ${svc}_svc LOGIN PASSWORD '${password}';
CREATE DATABASE ${svc}_db OWNER ${svc}_svc;
REVOKE ALL ON DATABASE ${svc}_db FROM PUBLIC;
SQL
done
