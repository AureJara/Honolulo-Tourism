# Construir desde la raíz del repositorio:  docker build -f services/web/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY libs/honolulo_common /opt/honolulo_common
COPY services/web/requirements.txt .
RUN pip install -r requirements.txt /opt/honolulo_common

COPY services/web/ .
RUN useradd --create-home --uid 10001 app && chown -R app /srv
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

# Un worker con hilos: la renovación de tokens (refresh rotatorio) se coordina en memoria dentro del
# proceso. Para escalar a varios procesos ver README, «Limitaciones conocidas».
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "1", "--threads", "8", "--access-logfile", "-", "wsgi:app"]
