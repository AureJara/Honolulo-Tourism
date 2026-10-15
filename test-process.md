# Proceso de Pruebas — Honolulo-Tourism

---

## 4. Proceso de pruebas del grupo

El grupo sigue un **ciclo corto y repetible**:

1. Cada requisito del spec o función nueva se traduce en **casos de prueba por riesgo**.
2. Los casos se **automatizan**.
3. Se **ejecutan en local y en integración continua**.
4. Los resultados alimentan las **métricas y el informe**.
5. Todo defecto encontrado genera una **corrección y una prueba de regresión** para que no vuelva a aparecer.

---

## 5. Niveles y tipos de prueba

| Nivel / Tipo | Qué cubre | Dónde / Herramienta | Cantidad actual |
|---|---|---|---|
| **Unitarias e integración** (caja blanca/gris) | Reglas de negocio, validación, permisos, modelo de pronóstico de afluencia, boletería, BFF contra servicios simulados, base PostgreSQL real | `libs/*/tests` y `services/*/tests` · pytest | 918 (común 178, auth 157, weather 30, forecast 88, catalog 235, web 230) |
| **Aceptación** (caja negra) | Escenarios Gherkin del spec contra el sistema en marcha, con correo y proveedor reales de desarrollo | `qa/test_e2e_acceptance.py` | 29 |
| **Seguridad** | JWT manipulado, escalada de privilegios, CSRF, XSS/SQLi, subidas maliciosas, cabeceras y cookies | `qa/test_security.py` | 49 |
| **Privacidad, límite por IP y auditoría** | Contraseñas y secretos fuera de registros, bloqueo con 429, auditoría de seguridad | `qa/test_privacy_secrets.py`, `qa/test_rate_limit_and_audit.py` | 18 y 10 |
| **Resiliencia y sesión** | Servicios caídos, degradación de la interfaz, renovación de sesión concurrente | `qa/test_resilience.py`, `qa/test_session.py` | 7 y 6 |
| **Integridad de datos** | Restricciones de la base ante datos inválidos | `qa/test_db_integrity.py` | 16 |
| **Rendimiento y estrés** | Latencia p50/p95 y concurrencia; conexiones masivas y lentas, cuerpos gigantes | `qa/test_performance.py`, `scripts/stress.py` | 12 (+ herramienta propia) |
| **Funciones nuevas (e2e)** | Opiniones y moderación, entrega de correo, afluencia y boletería de punta a punta | `qa/test_reviews_e2e.py`, `test_email_delivery.py`, `test_visits_e2e.py`, `test_tickets_e2e.py` | 23, 6, 10 y 5 |
| **Interfaz y accesibilidad** | Flujos reales, teclado, móvil 375 px, WCAG 2.1 AA, comportamiento de la interfaz con jsdom | Manual + axe-core + jsdom | Revisión manual |
| **Análisis estático** | Secretos en el código y dependencias con vulnerabilidades conocidas | `scripts/check_secrets.py` · pip-audit | En cada cambio |

> **Total:** 918 pruebas unitarias e integración + 191 de extremo a extremo = **1 109 pruebas automáticas**.

---

## 6. Herramientas

| Herramienta | Propósito |
|---|---|
| **pytest** | Ejecución de todas las suites en Python 3.12 |
| **PostgreSQL real** (`pgserver` o `TEST_DATABASE_URL`) | Las pruebas usan el mismo motor que producción (no SQLite) para detectar diferencias de comportamiento |
| **`responses`** (biblioteca de servicios simulados) | Permite probar el BFF aislando a los demás servicios y simular caídas |
| **Suite de QA sin mocks** (`qa/`) | Se ejecuta contra el sistema real levantado con `python scripts/dev.py up` |
| **`scripts/stress.py`** | Herramienta propia de estrés (apunta solo a `127.0.0.1`): línea base, lectura masiva, rampa de concurrencia, ingreso masivo, conexiones lentas y cuerpos gigantes |
| **jsdom + axe-core** | Verificación de la interfaz sin navegador y auditoría de accesibilidad |
| **`check_secrets.py` + pip-audit** | Análisis estático de secretos y de vulnerabilidades de dependencias |
| **GitHub Actions** | Integración continua en cada push a `main` y en cada pull request |

---

## 7. Integración continua

El flujo de **GitHub Actions** ejecuta dos trabajos en paralelo:

- **`pruebas`** — instala dependencias, revisa secretos y corre las seis suites unitarias y de integración con PostgreSQL real.
- **`dependencias`** — ejecuta `pip-audit` sobre las versiones fijadas.

Si todo está en verde el cambio puede fusionarse; si no, se corrige y se vuelve a empujar.

> [!NOTE]
> La suite de QA de extremo a extremo **no corre en la nube** porque necesita el sistema completo en marcha. El grupo la ejecuta en local antes de cada entrega.

---

## 8. Métricas de calidad (ISO/IEC 25010 y 25023)

| Característica | Métrica | Cómo se obtiene |
|---|---|---|
| **Adecuación funcional** | Corrección funcional = pruebas aprobadas / ejecutadas. Cobertura funcional = escenarios del spec con prueba / escenarios especificados | Resultado de pytest y trazabilidad del informe de QA |
| **Eficiencia de desempeño** | Tiempo de respuesta p95 en secuencial y con 10 usuarios concurrentes; errores bajo carga | `qa/test_performance.py` y `scripts/stress.py` |
| **Fiabilidad** | Defectos corregidos / hallados; comportamiento con un servicio caído | Informe de QA y `test_resilience` |
| **Seguridad** | Controles de acceso probados; vulnerabilidades conocidas en dependencias | `test_security` y pip-audit |
| **Usabilidad / accesibilidad** | Violaciones de accesibilidad detectadas por axe-core | Auditoría en pantallas principales |
| **Mantenibilidad** | Pruebas de regresión ejecutadas automáticamente en cada cambio | GitHub Actions |

**Resultados registrados hasta ahora (informe de QA):**
- Todas las suites en verde.
- Latencia p95 secuencial entre **63 y 91 ms** en las consultas principales.
- Con 10 usuarios concurrentes: p95 ≤ **150 ms** y **0 errores**.
- **0 violaciones de accesibilidad** en las pantallas auditadas.

> [!NOTE]
> Estas cifras se midieron en un servidor de desarrollo, por lo que son de referencia.

---

## 9. Actividades del grupo

| Actividad | Entregable | Estado |
|---|---|---|
| Mantener y ejecutar las suites unitarias y de integración en cada cambio | Resultado de `run_tests.py` y de GitHub Actions | ✅ Realizado y continuo |
| Escribir pruebas para cada función nueva (afluencia, boletería) | Pruebas del catálogo, de la web y de extremo a extremo | ✅ Realizado |
| Ejecutar la QA de extremo a extremo antes de cada entrega | Resultado de la suite `qa/` | 🔄 Continuo |
| Probar seguridad, resiliencia y estrés | Informe de hallazgos y correcciones | ✅ Realizado; repetir tras cambios importantes |
| Medir la cobertura de código (p. ej. `coverage.py`) y fijar un mínimo | Porcentaje de cobertura por servicio | ⏳ Por hacer |
| Pruebas de aceptación automatizadas en navegador real (p. ej. Playwright) | Scripts y capturas de los flujos de compra y consulta | ⏳ Por hacer |
| Probar con gunicorn y con Docker (despliegue real) | Resultados de carga y de arranque | ⏳ Por hacer |
| Registrar cada defecto, corregirlo y añadir su prueba de regresión | Tabla de defectos en el informe de QA | 🔄 Continuo |
| Calcular las métricas ISO/IEC 25023 y redactar el informe final | Informe con métricas y conclusiones | ⏳ Por hacer |

---

## 10. Riesgos y limitaciones

| Riesgo | Detalle |
|---|---|
| **Entorno de ejecución** | La QA de extremo a extremo se ejecutó en el servidor de desarrollo de Flask; no se ha medido contra gunicorn ni contra Docker. |
| **Navegadores** | La revisión manual se hizo solo con Chromium; faltan lectores de pantalla y dispositivos reales. |
| **Correo real** | La entrega se probó con un servidor SMTP local, no contra Gmail. |
| **Datos de demostración** | Las pruebas de afluencia usan datos ficticios; el error de predicción es optimista y debe revalidarse con visitas reales. |
| **Pago de la boletería** | Es simulado; una pasarela real requeriría pruebas adicionales de cobro, devoluciones y seguridad. |
| **Pruebas de penetración externas** | No se usaron herramientas como ZAP o Burp; la batería de seguridad es propia. |
