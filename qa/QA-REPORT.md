# Informe de QA — Honolulo (Escenarios 6, 7 y 8 + opiniones, moderación, políticas y seguridad reforzada)

| | |
|---|---|
| **Fecha** | 2026-09-30 (base) · 2026-10-03 (segunda ronda: opiniones, moderación, políticas, seguridad) |
| **Versión probada** | Sistema completo: `web`, `auth`, `weather`, `forecast`, `catalog` + PostgreSQL 16 (embebido) |
| **Entorno** | Windows 11, Python 3.11, Chromium (panel del navegador integrado), proveedor meteorológico **real** (Open-Meteo), correo en modo `file` y **SMTP real contra un servidor local de pruebas** |
| **Resultado** | **953 pruebas automatizadas en verde** (778 unitarias/integración + 175 de QA de extremo a extremo) y 21 defectos encontrados y corregidos (ninguno crítico) |
| **Veredicto** | Apto para pruebas de aceptación con el cliente. Pendientes antes de producción: ver §6. |

## 1. Estrategia

Pruebas **basadas en riesgo**, de caja negra sobre el sistema real (sin mocks) y de caja blanca en cada servicio:

| Nivel | Qué cubre | Dónde |
|---|---|---|
| Unitarias y de integración | Reglas de negocio (franjas, puntuación, disponibilidad, opiniones), validación, permisos, PostgreSQL real (no SQLite), SMTP local | `libs/*/tests`, `services/*/tests` — 778 pruebas |
| Aceptación (Gherkin del spec) | Escenarios 1–8 contra el sistema corriendo, con correo real de desarrollo y proveedor real | `qa/test_e2e_acceptance.py` — 28 |
| Seguridad | JWT manipulado, escalada de privilegios, CSRF, XSS/SQLi, subidas maliciosas, enumeración, cabeceras y cookies | `qa/test_security.py` — 49 |
| Resiliencia | Proveedor caído (puerto cerrado), servicios muertos, degradación de la UI | `qa/test_resilience.py` — 7 |
| Sesión | Renovación transparente, concurrencia de refrescos, varios dispositivos | `qa/test_session.py` — 6 |
| Integridad de datos | Restricciones de PostgreSQL ante datos inválidos | `qa/test_db_integrity.py` — 16 |
| Rendimiento (humo) | Latencia p50/p95 secuencial y con 10 usuarios concurrentes | `qa/test_performance.py` — 12 |
| Opiniones y moderación (nuevo) | Publicar/editar/eliminar, validación y abuso, permisos, fijar (límite 3, concurrencia), eliminar con auditoría, HTML escapado | `qa/test_reviews_e2e.py` — 23 |
| Entrega del correo (nuevo) | Servicio de cuentas real enviando por SMTP: el código llega al correo registrado, confirma la cuenta, fallos del servidor de correo | `qa/test_email_delivery.py` — 6 |
| Límite por IP y auditoría (nuevo) | Fuerza bruta de contraseñas, registros masivos y escrituras de la API bloqueados con `429`; otras IP no afectadas; `X-Forwarded-For` no falsificable; la IP real llega al registro de auditoría; solo administradores lo leen | `qa/test_rate_limit_and_audit.py` — 10 |
| Privacidad y secretos (nuevo) | La contraseña «canario» no aparece en registros, base ni correo; solo hash del código; políticas publicadas; cookies declaradas; cabeceras; ningún secreto expuesto | `qa/test_privacy_secrets.py` — 18 |
| Exploratoria en navegador | Flujos reales, teclado, móvil (375 px), XSS en interfaz, **axe-core** (WCAG 2.1 AA) | manual, evidencia en §4 |

## 2. Resultados

| Suite | Pruebas | Resultado |
|---|---:|---|
| Revisión de secretos en el código (`scripts/check_secrets.py`) | — | ✅ |
| `honolulo_common` (higiene del repositorio, secretos obligatorios, comandos de `dev.py`, registro de proveedores del clima, espera de PostgreSQL tras un cierre brusco, higiene de dependencias, arranque del PostgreSQL de pruebas, cliente HTTP entre servicios, herramienta de estrés, configuración del correo, entorno de desarrollo) | 150 | ✅ |
| `auth-service` (incluye la auditoría de seguridad y el aviso de cómo sale el código) | 149 | ✅ |
| `weather-service` | 30 | ✅ |
| `forecast-service` | 88 | ✅ |
| `catalog-service` | 179 | ✅ |
| `web` (incluye el límite por IP, la pantalla de seguridad, las llamadas internas y el aviso del código) | 182 | ✅ |
| QA: aceptación / seguridad / resiliencia / sesión / integridad / rendimiento | 28 / 49 / 7 / 6 / 16 / 12 | ✅ |
| QA nuevo: opiniones y moderación / entrega de correo / privacidad y secretos | 23 / 6 / 18 | ✅ |

**Rendimiento medido** (servidor de desarrollo de Flask, una instancia): p95 secuencial entre 63 y 91 ms en clima actual, calendario, pronóstico del día, historial y lugares; con 10 usuarios concurrentes p95 ≤ 150 ms y **0 errores**. Página principal p95 30 ms.

**Accesibilidad (axe-core, WCAG 2.0/2.1 A y AA):** 0 violaciones en la pantalla principal, registro, administración (lista y edición, ahora con la sección de opiniones), la **ventana de opiniones** y las **políticas** tras las correcciones. En móvil (375 px) no hay desbordamiento horizontal y la ventana cabe en la pantalla.

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
| Opiniones y puntuación (visitantes) | `test_reviews.py` (78), `test_reviews_web.py`, `qa/test_reviews_e2e.py`: una por persona y lugar, 1–5 estrellas enteras, comentario opcional ≤ 1000 sin enlaces, nombre público «Nombre I.», sin datos personales en la salida, promedio y distribución |
| Rol de administrador: fijar y eliminar comentarios (además de cambiar imágenes) | permisos 401/403, fijar con tope de 3 por lugar (también con 8 peticiones simultáneas), orden, eliminar con auditoría **sin conservar el texto**, moderación desde la ventana pública y desde `/admin/lugares/<lugar>` |
| Código de confirmación al correo del usuario | `test_hardening.py` + `qa/test_email_delivery.py`: el mensaje llega por SMTP **autenticado** a la dirección registrada, el código confirma la cuenta, un código distinto por persona, errores del servidor de correo → 503 claro, el código y la clave SMTP no salen en respuestas ni registros |
| Contraseñas que solo conoce el usuario | hash con sal, nunca en registros/base/correo (prueba «canario» en vivo), política (mín. 10, no comunes, sin nombre/correo), `create-admin` con contraseña oculta y la misma política, escáner de secretos en el código, servicios que no arrancan sin secretos fuertes |
| Políticas de privacidad y de cookies | contenido verificado (opiniones, correo, seguridad, moderación, ARCO), tabla de las 4 cookies reales (y prueba de que no se emite ninguna otra), aviso descartable, enlaces en el pie y versión 2026-10-03 registrada |

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

### Segunda ronda (2026-10-03)

| ID | Sev. | Hallazgo | Cómo se detectó | Estado |
|---|:-:|---|---|---|
| **D9** | Media (seguridad) | El archivo nuevo de secretos locales (`.env.local`) **no estaba en `.gitignore`**: un `git add .` habría subido las claves. | Prueba de higiene `test_local_secret_and_data_files_are_ignored_by_git`, antes de crear el archivo | ✅ Corregido |
| **D10** | Media (seguridad) | El escáner de secretos **ignoraba** `os.getenv("JWT_SECRET_KEY", "dev-secret-change-me")` porque tomaba «change-me» como marcador de posición: no habría detectado justo lo que se quería eliminar. | Su propia prueba unitaria | ✅ Corregido (marcadores anclados) |
| **D11** | Media (seguridad) | `create-admin` **no aplicaba la política de contraseñas** (ni el método de hash configurado) y pedía una contraseña aunque solo se promoviera una cuenta existente (sin efecto: engañoso). | Revisión de código | ✅ Corregido + 3 pruebas |
| **D12** | Media (robustez) | Si fallaba la carga de opiniones, la pantalla de edición de un lugar devolvía 503 entera. | Fallaron pruebas existentes al añadir la sección | ✅ Corregido: la edición no depende de las opiniones |
| **D13** | Baja (UX) | Tras cerrar la ventana de opiniones la página quedaba sin poder desplazarse si el evento `close` se retrasaba (bloqueo mediante una clase puesta por JavaScript). | Exploración en navegador | ✅ Corregido: regla CSS `html:has(dialog[open])` + regresión |
| **D14** | Media (a11y) | La tabla de cookies (región con scroll) no se podía enfocar con el teclado (axe: `scrollable-region-focusable`). | axe-core | ✅ Corregido (`role="region"`, `tabindex="0"`) + regresión |
| **D15** | Media (operación) | Tras un cierre brusco (apagón, ventana cerrada) PostgreSQL tarda más de 10 s en recuperarse y `dev.py up` fallaba con `TimeoutExpired`; había que repetir el comando a mano. | Ocurrió varias veces durante las pruebas; reproducido matando PostgreSQL de golpe | ✅ Corregido: `dev.py` espera la recuperación (hasta 3 min), explica qué pasa y continúa solo + 3 pruebas |
| **D16** | Baja (pruebas) | La contraseña aleatoria de QA podía contener un fragmento del correo de prueba (1 de cada 256 ejecuciones) y la nueva política la rechazaba: una prueba fallaba al azar. | Falla intermitente de `test_scenario7_gmail_aliases…` | ✅ Corregido: contraseña independiente del esquema de correos |
| **D17** | Baja (herramientas) | Una corrida de `run_tests.py` dejó 137 errores en la suite de `auth` (`AssertionError` en `pgserver`): el PostgreSQL desechable de las pruebas no terminó de arrancar porque la suite anterior aún lo apagaba, y `pgserver` guarda la instancia fallida en su caché y la devuelve a medias en los intentos siguientes. | Falla intermitente en una corrida completa (no se repitió en 3 corridas posteriores) | ✅ Corregido: reintentos que descartan la instancia a medias + 2 pruebas |
| **D18** | Media (honestidad de la interfaz) | Tras registrarse, la web decía «Te enviamos un código de confirmación por correo» aunque en modo archivo (sin SMTP configurado) el código no salía: quedaba en `.mail/` y la persona nunca lo recibía. | Se notó registrando a mano en desarrollo, no en las pruebas (todas simulaban el envío) | ✅ Corregido: `auth` informa en `delivery` si el correo sale a Internet; la web dice dónde quedó el código y cómo configurar el envío; en producción solo se informa `external` y no se revela nada + 13 pruebas |
| **D19** | Media (herramientas) | `setup-mail` aceptaba cualquier cadena de 8 caracteres o más como «contraseña de aplicación»: se guardó un valor que no tenía la forma de una contraseña de aplicación de Google (16 letras), casi seguro la contraseña normal. Gmail la rechaza (error 535) y además quedaba en texto plano en `.env.local`. | Ningún correo con código podía salir y `dev.py up` no avisaba de nada; se descubrió investigando que «no llegó el correo» | ✅ Corregido: `setup-mail` solo acepta 16 letras y nunca repite lo escrito; `dev.py up` avisa al arrancar si lo guardado no tiene esa forma + 18 pruebas |
| **D20** | Media (herramientas) | `python scripts/dev.py test-mail` terminaba con `KeyError: '_SQLA_BASE'`: el comando nunca había podido funcionar (solo envía un correo pero pedía la dirección de una base de datos que no levanta). Al fallar el envío mostraba además un traceback de Python en lugar del motivo. | La prueba de envío que se recomienda antes de usar el correo no se podía ejecutar | ✅ Corregido: no exige la base de datos, sale con el motivo claro y código de error, sin traceback + 4 pruebas |
| **D21** | Media (herramientas) | Lanzado con el Python del sistema (no el del proyecto), `dev.py up` fallaba con `No module named 'PIL'` a mitad de las migraciones. | El sistema no arrancaba desde una terminal normal, solo desde el entorno virtual | ✅ Corregido: `dev.py`, `run_tests.py` y `stress.py` se relanzan solos con el Python de `.venv` (el padre espera sin cortar el Ctrl+C para que el hijo apague PostgreSQL ordenadamente) + 8 pruebas |

Defectos adicionales atrapados por las propias pruebas antes de llegar a QA: los dígitos Unicode de ancho completo pasaban la validación del código (regex `\d`); la bomba de píxeles se rechazaba con un mensaje engañoso.

### Revisión de diseño (SOLID)

Tras la segunda ronda se revisó el código nuevo contra SOLID y se corrigió lo que no cumplía el principio
abierto/cerrado o de responsabilidad única, **sin cambiar el comportamiento** (todas las pruebas de antes siguen en verde):

| Hallazgo | Corrección | Prueba que lo demuestra |
|---|---|---|
| El envío de correo elegía el backend con una cadena `if/elif` por nombre | registro `@register_backend` (con `external`) y `@register_smtp_security`; `send` no cambia al añadir otro | `test_extensibility.py` (auth) |
| Un `SMTP_SECURITY` mal escrito (p. ej. `startls`) se trataba como texto plano | ahora es un error explícito | `test_a_misspelled_smtp_security_never_degrades_to_plain_text` |
| Las reglas de contraseña eran una función con pasos fijos | lista de reglas `@password_rule` | `test_a_new_password_rule_is_enforced…` |
| La validación del comentario mezclaba limpieza y reglas en una función | saneadores y reglas registrables (`review_schemas.py`) | `test_review_extensibility.py` |
| `service.py` y `api.py` del catálogo mezclaban lugares, fotos y opiniones | módulos `reviews.py`, `reviews_api.py`, `review_schemas.py`, `permissions.py`, `responses.py`, `text.py` | suites existentes |
| La lógica de opiniones dependía de `flask.g` | recibe quién actúa (`claims`) como parámetro | `test_the_review_domain_works_without_an_http_request` |
| La web mezclaba clima, opiniones y administración en `api.py` y `admin.py` | `reviews_api.py`, `admin_reviews.py`, `admin_common.py`, `relay.py`, `validators.py` | suites existentes |
| `weather` y `forecast` creaban directamente el cliente de Open-Meteo y guardaban su nombre fijo: cambiar de proveedor obligaba a editar ambos servicios | interfaz `WeatherProvider` + registro `@register_provider`, elección con `WEATHER_PROVIDER`; el nombre del proveedor lo da el propio cliente | `test_weather_provider.py`, `test_provider_wiring.py` |
| `dev.py` despachaba comandos con `if/elif` | tabla `@command` | `test_a_new_dev_command_needs_no_change_to_main` |
| El servidor SMTP de pruebas y el JS de la ventana de opiniones despachaban con cadenas de `if` | métodos `do_<VERBO>` y tabla `ACTIONS` | `test_dialog_buttons_dispatch_through_an_action_table` |

### Revisión de seguridad (lista de 15 puntos)

Se revisó cada punto contra el código real (9 estaban resueltos, 6 parciales) y se cerraron los parciales de bajo riesgo:

| Hallazgo | Corrección | Prueba |
|---|---|---|
| **S1 · Sin límite de peticiones por IP**: se podían probar contraseñas en muchas cuentas, crear cuentas en masa o disparar correos a direcciones ajenas | Límite por IP en la web (reglas ampliables, `429` + `Retry-After`, `X-Forwarded-For` solo con proxy de confianza) | `test_ratelimit.py` (19), `qa/test_rate_limit_and_audit.py` |
| **S2 · Sin auditoría de acceso** (inicios de sesión, fallos, bloqueos, cambios de rol) | Registro de eventos en `auth` + pantalla `/admin/seguridad`, 180 días de retención, política de privacidad actualizada (v2026-10-04) | `test_audit.py` (25), `test_admin_security.py` |
| **S3 · Entorno distinto al declarado**: el entorno de pruebas tenía `marshmallow 4.3.1` pero los requisitos piden `<4` (Docker instalaría la 3.26.2) | Entorno alineado; las pruebas pasan con las versiones declaradas (se comprobó también en un entorno aparte) | `test_dependencies.py` |
| **S4 · Dependencias sin fijar ni acotar** (instalaciones no reproducibles) | `requirements-lock.txt` (28 paquetes, con rueda para Python 3.12/Linux), cotas superiores, Dependabot | `test_dependencies.py` |
| **S5 · Paquetes declarados sin uso** (`marshmallow` en weather y forecast; `PyJWT`/`requests`/`tzdata` repetidos) | Eliminados; cada servicio se instaló solo con lo suyo y arranca | `test_every_declared_package_is_used…` |
| **S6 · Sin `.dockerignore`** | Creado: sin `.git`, `.env`, datos locales ni pruebas en las imágenes | `test_dockerignore…` |
| **S7 · `setuptools 65.5` con 4 avisos conocidos** (herramienta de instalación del entorno) | Actualizado; `pip-audit`: sin vulnerabilidades conocidas | — |

Pendiente (decisión de producto, no se hizo): verificación en dos pasos para administradores, recuperación de contraseña,
cabecera `script-src` estricta (requiere compilar Tailwind y alojar las fuentes) y reducir la vida del token de acceso.

### Pruebas de estrés y resistencia a abusos

Herramienta propia: `scripts/stress.py` (solo apunta a `127.0.0.1`; 9 pruebas, incluida la que comprueba que rechaza servidores
ajenos). Mide a la vez a una persona normal desde otra IP para saber si un abusador perjudica a los demás. Resultados completos en
el README («Resistencia a abusos y pruebas de estrés»).

| Hallazgo | Corrección | Prueba |
|---|---|---|
| **E1 · Cada llamada interna de la web abría una conexión TCP nueva** (`requests.request`): a ≥ 100 conexiones simultáneas el equipo agotó los puertos locales, la web y el catálogo dejaron de responder unos segundos y la persona normal quedó sin servicio (se recuperó sola; ningún proceso murió) | Sesión compartida con pool, sin cookies, con una repetición segura de lecturas (`honolulo_common/http_client.py`). Cada llamada pasa de ≈ 5–6 ms a ≈ 3 ms y de 800–1 200 conexiones a 1–7 en la misma comparación | `test_http_client.py` (8), `test_upstream_pooling.py` (4) |
| **E2 · Un servicio caído retenía el hilo de la web** hasta los 12 s de espera | Tiempo de conexión aparte y corto (`UPSTREAM_CONNECT_TIMEOUT_S`, 3 s) | `test_upstream_pooling.py` |
| **E3 · Documentación obsoleta**: el README aún decía «sin limitación de intentos por IP» | Corregido | — |

Comprobado sin hallazgos: con el límite por IP, una sola persona con 100 conexiones solo consigue 300 respuestas por minuto y el resto
recibe `429` sin afectar a nadie más; cuerpos de hasta 40 MB, direcciones de 100 000 caracteres y 500 cabeceras se rechazan al instante;
no hay fuga de memoria tras la carga; todo se recupera.

Abierto (no es un defecto del código, sino de cómo se publique): conexiones lentas («slowloris») y número de hilos de gunicorn
necesitan un proxy delante y no se pudieron probar (gunicorn no corre en Windows; Docker sin construir). El ingreso masivo es la
petición anónima más cara (≈ 40 por segundo, un hash scrypt cada una): el límite por IP la contiene, pero un ataque desde muchas IP
necesitaría protección en el borde.

## 5. Observaciones (no son defectos) y riesgos aceptados

- **Caché pública de 30 s** en `/api/v1/places`: tras editar, un visitante puede ver el texto anterior hasta 30 s (la foto usa claves inmutables, sin este efecto).
- **El registro revela si un correo ya existe** (`409 EMAIL_TAKEN`). Se prefirió la claridad para el usuario; `login` y `reenviar código` no filtran información.
- **Un access token sigue válido hasta 15 min tras cerrar sesión** (JWT sin estado); el *refresh* sí se revoca de inmediato.
- Cabecera `Server: Werkzeug/…` visible: solo en el servidor de desarrollo; en producción usa gunicorn tras un proxy.
- Varias cuentas pueden iniciar sesión a la vez; cerrar una no cierra las demás (verificado).
- **La suite de QA abre unas 2 400 conexiones por ejecución** y en Windows quedan en `TIME_WAIT` unos 2 minutos: dos ejecuciones seguidas pueden agotar los puertos locales (`WinError 10048`) y hacer fallar una prueba al azar (pasa sola). Esperar ~2 min entre corridas completas.
- **Las opiniones se moderan después de publicarse** (no hay cola previa) y solo se bloquean los enlaces: no hay filtro de insultos.
- **La cuenta administradora local tiene una contraseña más corta que el mínimo actual (10 caracteres)**: su dueña la fijó directamente en la base de desarrollo, fuera de los flujos que aplican la política (registro y `create-admin`). La política no es retroactiva. Antes de publicar el sistema hay que cambiarla por una que la cumpla. La cuenta de ejemplo que usaba una clave aún más corta ya se eliminó.
- Quien ya tenía cuenta aceptó la política anterior; no se le pide aceptar la nueva (queda la versión registrada por cuenta).

## 6. Lo que NO se probó (y conviene antes de producción)

1. **Entrega real de correo a Gmail**: se probó de punta a punta contra un servidor SMTP local (autenticación, destinatario correcto, fallos), pero **no** contra Gmail ni en bandejas reales (no hay credenciales en el entorno). Usar `python scripts/dev.py setup-mail` y `test-mail` con la cuenta real; revisar también la carpeta de spam.
2. **«Loguear con cuentas de Gmail»** se implementó como correo (Gmail o cualquier otro) confirmado con código. **No** hay «Iniciar sesión con Google» (OAuth); requiere credenciales de Google Cloud.
3. **Docker**: los `Dockerfile` y `docker-compose.yml` no se construyeron (no hay Docker en el equipo).
4. **Navegadores y dispositivos**: solo Chromium; sin lectores de pantalla reales ni dispositivos físicos.
5. **Carga**: se midió con `scripts/stress.py` contra el servidor de desarrollo de Flask en Windows (hasta 400 conexiones, ingreso masivo, 300 conexiones lentas, cuerpos gigantes). **No** contra gunicorn ni Docker, y sin pruebas de larga duración (soak): las cifras son de referencia y varían con la carga del equipo.
6. **Políticas de privacidad y de cookies**: son textos base; requieren revisión legal (Ley N.° 29733) y un correo de contacto real (`CONTACT_EMAIL`).
7. **Datos de fotos reales**: las cascadas del diseño muestran un marcador hasta que el administrador suba fotos.
8. Sin herramientas externas de pentesting (ZAP/Burp); la batería de seguridad es manual/automatizada propia.

## 7. Cómo reproducirlo

```bash
python scripts/dev.py up --mail file --no-rate-limit   # sistema completo (otra terminal); la QA lee los códigos de .mail/
python scripts/run_tests.py                    # revisión de secretos + 778 pruebas unitarias y de integración
cd qa && python -m pytest -q                   # 175 pruebas de QA de extremo a extremo
```

Las pruebas de administración crean una **cuenta administradora temporal** (registro + código del correo + promoción en la base) y la desactivan al terminar; si prefieres una existente, define `QA_ADMIN_EMAIL` y `QA_ADMIN_PASSWORD`. Las pruebas de opiniones crean datos reales en la base de desarrollo y los eliminan al terminar.
