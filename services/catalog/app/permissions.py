"""Quién puede llamar a cada endpoint (los roles vienen en el JWT que emite el servicio de cuentas)."""

from honolulo_common.security import auth_required

admin_only = auth_required(roles=["admin"])
signed_in = auth_required()                       # cualquier persona con sesión (usuario o administrador)
