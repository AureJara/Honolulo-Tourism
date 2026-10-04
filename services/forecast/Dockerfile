# Construir desde la raíz del repositorio:  docker build -f services/forecast/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY libs/honolulo_common /opt/honolulo_common
COPY requirements-lock.txt /tmp/requirements-lock.txt
COPY services/forecast/requirements.txt .
RUN pip install -c /tmp/requirements-lock.txt -r requirements.txt /opt/honolulo_common

COPY services/forecast/ .
RUN useradd --create-home --uid 10001 app && chown -R app /srv
USER app

EXPOSE 5003
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5003/healthz', timeout=2)"

# UN solo worker por las tareas programadas (refresco de pronóstico y evaluación); ver weather.
CMD ["sh", "-c", "flask --app app:create_app db upgrade && flask --app app:create_app seed && exec gunicorn -b 0.0.0.0:5003 -w 1 --threads 4 --access-logfile - wsgi:app"]
