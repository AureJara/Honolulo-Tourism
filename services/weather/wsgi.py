"""Punto de entrada del servidor. El scheduler se inicia aquí (y no en create_app) para que los
comandos de CLI (``flask --app app:create_app db upgrade``) no lancen tareas en segundo plano."""

from app import create_app
from app.jobs import start_scheduler

app = create_app()
app.extensions["scheduler"] = start_scheduler(app)
