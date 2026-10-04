# Construir desde la raíz del repositorio:  docker build -f services/auth/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY libs/honolulo_common /opt/honolulo_common
COPY requirements-lock.txt /tmp/requirements-lock.txt
COPY services/auth/requirements.txt .
RUN pip install -c /tmp/requirements-lock.txt -r requirements.txt /opt/honolulo_common

COPY services/auth/ .
RUN useradd --create-home --uid 10001 app && chown -R app /srv
USER app

EXPOSE 5001
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/healthz', timeout=2)"

# Las migraciones corren al iniciar para simplificar docker compose; en producción conviene
# ejecutarlas como un paso de despliegue aparte.
CMD ["sh", "-c", "flask --app app:create_app db upgrade && exec gunicorn -b 0.0.0.0:5001 -w 2 --threads 4 --access-logfile - wsgi:app"]
