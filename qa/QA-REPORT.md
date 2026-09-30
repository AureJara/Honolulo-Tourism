# Informe de QA — Honolulo (spec actualizado con los Escenarios 6, 7 y 8)

| | |
|---|---|
| **Fecha** | 2026-09-30 |
| **Versión probada** | Sistema completo: `web`, `auth`, `weather`, `forecast`, `catalog` + PostgreSQL 16 (embebido) |
| **Entorno** | Windows 11, Python 3.11, Chromium (panel del navegador integrado), proveedor meteorológico **real** (Open-Meteo), correo en modo `file` |
| **Resultado** | **530 pruebas automatizadas en verde** (412 unitarias/integración + 118 de QA de extremo a extremo) y 8 defectos encontrados y corregidos (ninguno crítico) |
| **Veredicto** | Apto para pruebas de aceptación con el cliente. Pendientes antes de producción: ver §6. |

## 1. Estrategia

Pruebas **basadas en riesgo**, de caja negra sobre el sistema real (sin mocks) y de caja blanca en cada servicio:

| Nivel | Qué cubre | Dónde |
|---|---|---|
| Unitarias y de integración | Reglas de negocio (franjas, puntuación, disponibilidad), validación, permisos, PostgreSQL real (no SQLite) | `libs/*/tests`, `services/*/tests` — 412 pruebas |
| Aceptación (Gherkin del spec) | Escenarios 1–8 contra el sistema corriendo, con correo real de desarrollo y proveedor real | `qa/test_e2e_acceptance.py` — 28 |
| Seguridad | JWT manipulado, escalada de privilegios, CSRF, XSS/SQLi, subidas maliciosas, enumeración, cabeceras y cookies | `qa/test_security.py` — 49 |
| Resiliencia | Proveedor caído (puerto cerrado), servicios muertos, degradación de la UI | `qa/test_resilience.py` — 7 |
| Sesión | Renovación transparente, concurrencia de refrescos, varios dispositivos | `qa/test_session.py` — 6 |
| Integridad de datos | Restricciones de PostgreSQL ante datos inválidos | `qa/test_db_integrity.py` — 16 |
| Rendimiento (humo) | Latencia p50/p95 secuencial y con 10 usuarios concurrentes | `qa/test_performance.py` — 12 |
| Exploratoria en navegador | Flujos reales, teclado, móvil (375 px), XSS en interfaz, **axe-core** (WCAG 2.1 AA) | manual, evidencia en §4 |

## 2. Resultados

| Suite | Pruebas | Resultado |
|---|---:|---|
| `honolulo_common` | 25 | ✅ |
| `auth-service` | 87 | ✅ |
| `weather-service` | 27 | ✅ |
| `forecast-service` | 85 | ✅ |
| `catalog-service` | 92 | ✅ |
| `web` | 96 | ✅ |
| QA: aceptación / seguridad / resiliencia / sesión / integridad / rendimiento | 28 / 49 / 7 / 6 / 16 / 12 | ✅ |

**Rendimiento medido** (servidor de desarrollo de Flask, una instancia): p95 secuencial entre 63 y 91 ms en clima actual, calendario, pronóstico del día, historial y lugares; con 10 usuarios concurrentes p95 ≤ 150 ms y **0 errores**. Página principal p95 30 ms.

**Accesibilidad (axe-core 4.10, WCAG 2.0/2.1 A y AA + buenas prácticas):** 0 violaciones en la pantalla principal, registro y administración (lista y edición) tras las correcciones.

## 3. Trazabilidad con el spec

| Requisito | Verificación |
|---|---|
| RF01 sesión activa | `test_rf01…` (todas las rutas del módulo dan 401), Escenario 6 web, `test_session.py` |
| RF02 ubicación · RN01/RN02 | `test_rf02_location…` (datos y coordenadas), `qa/test_e2e_acceptance.py` |
| RF03 · Escenario 1 · RN04 | `test_scenario1_*` (todos los campos, `updated_at` con hora de Lima) |
| RF04 · RF05 · Escenario 2 | `test_scenario2_calendar_and_day_forecast`, `test_rf04_calendar_days_come_only_from_stored_forecasts` (RN03: sin datos inventados, febrero 2028 = 29 días) |
| RF06 · RF07 | frescura «Actualizado hace…», botón «Actualizar clima» con intervalo mínimo |
| RF08 | `test_rf08_history_records_have_the_required_fields` |
| RF09 · RN05 · RF10 · RF11 · RN06 · Escenarios 3 y 4 | pronósticos solo-inserción, evaluación bajo demanda, puntuación parametrizable; **evaluación real verificada**: pronóstico de las 14:00 (31,2 °C / 47 %) vs. observado (31,3 °C / 45 %) → **94,2 % «Muy buena precisión»** |
| RN07 | `test_scenario4_*`: «Pronóstico pendiente de evaluación.» y sin puntaje |
| RN08 · Escenario 5 | `test_resilience.py` (proveedor caído real): dato anterior marcado `stale` + mensaje exacto; sin dato → 503. **Además ocurrió de verdad:** Open-Meteo devolvió HTTP 503 durante las pruebas y el sistema respondió conforme al spec |
| Escenario 6 (administrador: descripciones y fotos) | `test_photos.py`, `test_places.py`, `test_admin.py`, `test_scenario6_*` (edición desde la interfaz real + subida de foto + el visitante la ve), permisos por rol, auditoría de cambios |
| Escenario 7 (correo con código + consentimiento) | `test_registration.py`, `test_scenario7_*`: código de 6 dígitos por correo, sin sesión hasta confirmar, 5 intentos máximo, vencimiento, reenvío limitado, consentimiento obligatorio y registrado con versión, alias de Gmail |
| Escenario 8 (nombre y apellido reales) | `test_scenario8_*`: obligatorios y con validación de plausibilidad (se rechazan «Test», «aaaa», dígitos, HTML…) |

## 4. Defectos encontrados

| ID | Sev. | Hallazgo | Cómo se detectó | Estado |
|---|:-:|---|---|---|
| **D1** | Media | `GET /api/v1/weather/evaluation/<id>` con un `id` que no cabe en `bigint` (p. ej. 40 dígitos) devolvía **HTTP 500** con la traza en el log. | Prueba de fuzzing de la suite de seguridad | ✅ Corregido (404) + regresión `test_out_of_range_forecast_ids_are_404_not_500` |
| **D2** | Baja | Al faltar el consentimiento, el registro mostraba solo ese error y ocultaba los demás (p. ej. «nombre no real»): el usuario los descubría de uno en uno. | Exploración en navegador | ✅ Corregido: todos los errores juntos |
| **D3** | Baja | Los avisos (p. ej. «¡Correo confirmado!») quedaban fijos sobre el contenido indefinidamente y sin botón de cierre. | Exploración en navegador | ✅ Corregido: se cierran solos (6 s; 12 s los errores) y con botón accesible |
| **D4** | Baja | `srcset` declaraba `1600w` aunque la foto almacenada era menor, y la base guardaba el tamaño del **original** y no el de la imagen servida. | Inspección en navegador | ✅ Corregido: se guardan y declaran las dimensiones reales (+ pruebas) |
| **D5** | Media (a11y) | Contraste insuficiente (WCAG 1.4.3, mínimo 4,5:1): texto del pie (`text-outline` sobre fondo beige) y celdas de otros meses del calendario (4,49:1). | axe-core | ✅ Corregido: color terciario del diseño; 0 violaciones |
| **D6** | Baja | En móvil, `/admin/lugares` ensanchaba toda la página (550 px en un viewport de 375) por textos `sr-only` posicionados de forma absoluta fuera del contenedor con scroll. | Medición del desbordamiento en 375 px | ✅ Corregido (`relative` en el contenedor) + regresión |
| **D7** | Media (operación) | `scripts/dev.py` dejaba **procesos huérfanos** (en Windows el lanzador del venv crea un hijo que `terminate()` no alcanza) que seguían sirviendo código antiguo y compartían el puerto sin avisar. | Un `readyz` sin `catalog` tras reiniciar | ✅ Corregido: verificación previa de puertos y cierre del árbol de procesos |
| **D8** | Baja (herramientas) | El PostgreSQL temporal de las pruebas podía quedar corrupto (se borran carpetas de `%TEMP%`) y las suites fallaban en bloque. | Falla masiva de la suite | ✅ Corregido: se recrea solo |

Defectos adicionales atrapados por las propias pruebas antes de llegar a QA: los dígitos Unicode de ancho completo pasaban la validación del código (regex `\d`); la bomba de píxeles se rechazaba con un mensaje engañoso.

## 5. Observaciones (no son defectos) y riesgos aceptados

- **Caché pública de 30 s** en `/api/v1/places`: tras editar, un visitante puede ver el texto anterior hasta 30 s (la foto usa claves inmutables, sin este efecto).
- **El registro revela si un correo ya existe** (`409 EMAIL_TAKEN`). Se prefirió la claridad para el usuario; `login` y `reenviar código` no filtran información.
- **Un access token sigue válido hasta 15 min tras cerrar sesión** (JWT sin estado); el *refresh* sí se revoca de inmediato.
- Cabecera `Server: Werkzeug/…` visible: solo en el servidor de desarrollo; en producción usa gunicorn tras un proxy.
- Varias cuentas pueden iniciar sesión a la vez; cerrar una no cierra las demás (verificado).

## 6. Lo que NO se probó (y conviene antes de producción)

1. **Entrega real de correo**: en desarrollo el mensaje se guarda en `.mail/`. Falta probar SMTP (p. ej. Gmail con *contraseña de aplicación*) y su entrega a bandejas reales.
2. **«Loguear con cuentas de Gmail»** se implementó como correo (Gmail o cualquier otro) confirmado con código. **No** hay «Iniciar sesión con Google» (OAuth); requiere credenciales de Google Cloud.
3. **Docker**: los `Dockerfile` y `docker-compose.yml` no se construyeron (no hay Docker en el equipo).
4. **Navegadores y dispositivos**: solo Chromium; sin lectores de pantalla reales ni dispositivos físicos.
5. **Carga**: solo humo con 10 concurrentes; sin pruebas de estrés/soak ni servidor de producción (gunicorn).
6. **Política de privacidad**: es un texto base; requiere revisión legal (Ley N.° 29733).
7. **Datos de fotos reales**: las cascadas del diseño muestran un marcador hasta que el administrador suba fotos.
8. Sin herramientas externas de pentesting (ZAP/Burp); la batería de seguridad es manual/automatizada propia.

## 7. Cómo reproducirlo

```bash
python scripts/dev.py up                       # sistema completo (otra terminal)
python scripts/run_tests.py                    # 412 pruebas unitarias y de integración
cd qa && QA_ADMIN_EMAIL=<admin> QA_ADMIN_PASSWORD=<clave> python -m pytest -q     # 118 pruebas de QA
```

Las pruebas de administración se omiten si no se define una cuenta administradora (`python -m flask --app app:create_app set-role <correo> admin` en `services/auth`).
