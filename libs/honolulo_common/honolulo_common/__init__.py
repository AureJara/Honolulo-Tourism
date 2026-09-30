"""Utilidades transversales compartidas por los microservicios de Honolulo.

Solo contiene código *transversal* (errores, autenticación JWT, cliente del proveedor
meteorológico, catálogo de condiciones, utilidades de fecha). La lógica de negocio vive
en cada servicio.
"""

__version__ = "0.1.0"
