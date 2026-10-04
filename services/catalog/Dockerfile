# Construir desde la raíz del repositorio:  docker build -f services/catalog/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY libs/honolulo_common /opt/honolulo_common
COPY requirements-lock.txt /tmp/requirements-lock.txt
COPY services/catalog/requirements.txt .
RUN pip install -c /tmp/requirements-lock.txt -r requirements.txt /opt/honolulo_common

COPY services/catalog/ .
# Las fotos viven en /media (volumen). El proceso corre sin privilegios.
RUN useradd --create-home --uid 10001 app && mkdir -p /media && chown -R app /srv /media
USER app
ENV MEDIA_ROOT=/media

EXPOSE 5004
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5004/healthz', timeout=2)"

CMD ["sh", "-c", "flask --app app:create_app db upgrade && flask --app app:create_app seed && exec gunicorn -b 0.0.0.0:5004 -w 2 --threads 4 --access-logfile - wsgi:app"]
