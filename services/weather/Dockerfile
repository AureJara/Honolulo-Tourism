# Construir desde la raíz del repositorio:  docker build -f services/weather/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY libs/honolulo_common /opt/honolulo_common
COPY services/weather/requirements.txt .
RUN pip install -r requirements.txt /opt/honolulo_common

COPY services/weather/ .
RUN useradd --create-home --uid 10001 app && chown -R app /srv
USER app

EXPOSE 5002
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5002/healthz', timeout=2)"

# UN solo worker: el servicio ejecuta tareas programadas en proceso (APScheduler) y más workers
# duplicarían las consultas al proveedor. Se escala con hilos.
CMD ["sh", "-c", "flask --app app:create_app db upgrade && exec gunicorn -b 0.0.0.0:5002 -w 1 --threads 4 --access-logfile - wsgi:app"]
