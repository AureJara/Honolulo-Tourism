"""Actividad de seguridad para administradores: inicios de sesión, fallos, bloqueos y cambios de rol.

Los eventos los registra el servicio de cuentas (``auth/app/audit.py``); aquí solo se muestran. Solo cuentas ``admin``.
"""

from __future__ import annotations

from flask import Blueprint, flash, g, render_template, request

from .admin_common import admin_required, auth_call
from .security import csrf_token

bp = Blueprint("admin_security", __name__, url_prefix="/admin")

PAGE_SIZE = 50

# Etiqueta en español y gravedad de cada evento. Un evento nuevo sin entrada aquí igual se muestra (con su código).
EVENT_LABELS = {
    "registered": ("Registro de cuenta", "info"), "email_verified": ("Correo confirmado", "info"),
    "verify_failed": ("Código de confirmación incorrecto", "warn"), "code_resent": ("Código reenviado", "info"),
    "login_success": ("Ingreso correcto", "info"), "login_failed": ("Ingreso fallido", "warn"),
    "login_unverified": ("Ingreso sin correo confirmado", "info"), "login_blocked": ("Intento con la cuenta bloqueada", "warn"),
    "account_locked": ("Cuenta bloqueada por intentos fallidos", "alert"), "logout": ("Cierre de sesión", "info"),
    "refresh_reuse": ("Posible robo de sesión (token reutilizado)", "alert"),
    "admin_created": ("Administrador creado", "alert"), "role_changed": ("Cambio de rol", "alert"),
}


@bp.get("/seguridad")
@admin_required
def security_activity():
    try:
        page = max(1, int(request.args.get("pagina", "1")))
    except ValueError:
        page = 1
    event = request.args.get("evento") or None
    params = {"limit": PAGE_SIZE, "offset": (page - 1) * PAGE_SIZE}
    if event:
        params["event"] = event
    resp = auth_call("GET", "/api/v1/auth/admin/audit", params=params)
    body = resp.json() if resp.status_code == 200 else {"items": [], "total": 0, "events": list(EVENT_LABELS)}
    if resp.status_code == 422:
        flash("Ese tipo de evento no existe.", "error")
    elif resp.status_code != 200:
        flash("No se pudo cargar la actividad de seguridad.", "error")
    total = body["total"]
    return render_template(
        "admin/security.html", items=body["items"], total=total, page=page, pages=max(1, -(-total // PAGE_SIZE)),
        event=event if resp.status_code == 200 else None, events=body["events"], labels=EVENT_LABELS,
        csrf_token=csrf_token(), user=g.user)
