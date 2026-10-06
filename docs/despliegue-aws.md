# Desplegar Honolulo en AWS (un servidor EC2 con Docker Compose)

Esta guía sube todo el sistema a **un solo servidor** de AWS: los 5 servicios y PostgreSQL corren en contenedores y **Caddy**
pone el HTTPS delante. Es lo más simple y barato para una exposición o un prototipo. Para producción seria con muchos
usuarios hay una opción mayor (ECS + RDS) al final.

> **Lo que NO se ha probado:** los `Dockerfile`, el `docker-compose.yml` y estos archivos de `deploy/aws/` **nunca se han
> construido ni ejecutado** (el equipo de desarrollo no tiene Docker). La primera vez puede aparecer algún error pequeño:
> el apartado «Si algo falla» dice dónde mirar. Una revisión estática confirmó que los nombres de las variables coinciden
> entre el compose y el código de cada servicio y que todo lo que copian los `Dockerfile` existe.

## Qué vas a tener al final

| Pieza | Dónde queda |
|---|---|
| Caddy (HTTPS, puertos 80 y 443) | única parte abierta a Internet |
| `web`, `auth`, `weather`, `forecast`, `catalog` | red interna de Docker |
| PostgreSQL 16 (una base por servicio) | volumen `pgdata` |
| Fotos subidas por el administrador | volumen `media` |
| Certificados HTTPS | volumen `caddy_data` |

**Costo aproximado:** entre 20 y 45 USD al mes en São Paulo (instancia `t3.small`, disco de 20 GB y la IP pública, que AWS
cobra aunque esté en uso). São Paulo es más caro que Virginia del Norte (`us-east-1`) pero queda más cerca de Perú.
Confirma el precio exacto en la calculadora de AWS y **crea primero una alerta de presupuesto**.

---

## Parte 1: en la consola de AWS

### 1. Alerta de presupuesto
*Facturación y administración de costos → Presupuestos → Crear presupuesto → Presupuesto de costos.* Pon un límite mensual
(por ejemplo 40 USD) y tu correo para los avisos. Mira también si tu cuenta tiene créditos del plan gratuito
(*Facturación → Créditos*).

### 2. Lanzar el servidor (EC2)
*EC2 → Instancias → Lanzar instancias* (región **América del Sur (São Paulo)**):

1. **Nombre:** `honolulo`.
2. **Imagen:** *Ubuntu Server 24.04 LTS*.
3. **Tipo de instancia:** `t3.small` (2 GB, el mínimo). Si lo notas lento, `t3.medium` (4 GB).
4. **Par de claves:** *Crear un nuevo par de claves*, nombre `honolulo`, tipo RSA, formato `.pem`. Se descarga una sola vez:
   **guárdalo bien y no lo compartas ni lo subas a git.**
5. **Configuraciones de red → Editar → Crear grupo de seguridad** con exactamente estas tres reglas:
   - SSH (22): origen **Mi IP**.
   - HTTP (80): origen **Cualquier lugar**.
   - HTTPS (443): origen **Cualquier lugar**.

   **No abras** ningún otro puerto: ni el 8000, ni del 5001 al 5004, ni el 5432 de la base de datos.
6. **Almacenamiento:** 20 GiB `gp3`.
7. *Lanzar instancia.*

### 3. IP fija (Elastic IP)
*EC2 → Direcciones IP elásticas → Asignar dirección IP elástica → Asignar.* Luego *Acciones → Asociar dirección IP elástica* y
elige la instancia `honolulo`. Sin esto la IP cambia cada vez que apagues el servidor.

### 4. Un dominio que apunte a esa IP
El HTTPS necesita un nombre, no solo la IP. Dos caminos:

- **Gratis:** crea un subdominio en [duckdns.org](https://www.duckdns.org) (por ejemplo `honolulo.duckdns.org`) y pon ahí tu
  IP elástica.
- **Con un dominio tuyo:** en tu proveedor (o en *Route 53*) crea un registro **A** que apunte a la IP elástica.

Espera unos minutos y comprueba que resuelve: `nslookup tu-dominio` debe devolver tu IP.

---

## Parte 2: conectarte al servidor

En PowerShell, en la carpeta donde guardaste `honolulo.pem`. La primera línea quita permisos que Windows rechaza para las
claves; reemplaza `TU_IP` por la IP elástica:

```
icacls .\honolulo.pem /inheritance:r /grant:r "${env:USERNAME}:R"
ssh -i .\honolulo.pem ubuntu@TU_IP
```

Responde `yes` la primera vez. Ya estás dentro del servidor.

## Parte 3: instalar y arrancar (en el servidor)

**1. Docker y git:**
```
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER
```
Sal con `exit`, vuelve a entrar con `ssh` (para que el permiso de Docker se aplique).

**2. Baja el proyecto y crea el `.env`** con secretos aleatorios (nada se imprime):
```
git clone https://github.com/AureJara/Honolulo-Tourism.git
cd Honolulo-Tourism
bash deploy/aws/generar-env.sh tu-dominio.duckdns.org
```

**3. Completa el correo, que solo tú puedes escribir:**
```
nano .env
```
Rellena `SMTP_HOST=smtp.gmail.com`, `SMTP_USER` (tu Gmail), `SMTP_PASSWORD` (una **contraseña de aplicación nueva**, 16 letras,
creada en https://myaccount.google.com/apppasswords), `MAIL_FROM` (por ejemplo `Honolulo <tu_cuenta@gmail.com>`) y
`CONTACT_EMAIL` (un correo real que leas; aparece en las políticas). Guarda con `Ctrl+O`, `Enter`, `Ctrl+X`.

**4. Construye y arranca** (la primera vez tarda varios minutos):
```
docker compose up -d --build
docker compose ps
```
Los 7 contenedores (`caddy`, `web`, `auth`, `weather`, `forecast`, `catalog`, `postgres`) deben estar en `running`/`healthy`.

**5. Crea tu cuenta de administradora.** La de tu computadora **no existe aquí**: es otra base de datos.
```
docker compose exec auth flask --app app:create_app create-admin
```
Te pide el correo, el nombre, el apellido y la contraseña (10 caracteres o más, no común, sin tu nombre ni tu correo).

## Parte 4: comprobar

1. Abre `https://tu-dominio.duckdns.org/healthz`: debe responder sin error y con candado. Caddy pide el certificado solo la
   primera vez; puede tardar un minuto.
2. Entra con tu cuenta de administradora y abre `/admin/seguridad`.
3. Regístrate con **otro** correo tuyo y comprueba que llega el código de confirmación.

---

## Mantenimiento

| Qué quieres | Comando (en el servidor, dentro de `Honolulo-Tourism`) |
|---|---|
| Ver registros de un servicio | `docker compose logs -f --tail 100 web` (o `auth`, `catalog`, `caddy`…) |
| Actualizar a lo último de GitHub | `git pull && docker compose up -d --build` |
| Reiniciar todo | `docker compose restart` |
| Respaldo de las bases de datos | `docker compose exec -T postgres pg_dumpall -U postgres > respaldo-$(date +%F).sql` |
| Ahorrar dinero sin perder nada | En la consola: *Detener instancia* (se conserva el disco; la IP elástica sigue cobrando) |
| Borrar todo y dejar de pagar | *Terminar instancia*, *Liberar dirección IP elástica* y eliminar el grupo de seguridad |

## Si algo falla

- **El candado no aparece / error de certificado:** el dominio no apunta a tu IP o el puerto 80 no está abierto en el grupo de
  seguridad. Revisa `docker compose logs caddy`.
- **Un contenedor se reinicia en bucle:** `docker compose logs --tail 80 NOMBRE`. Un servicio que dice «defina X» o «secreto
  débil» tiene un valor vacío o corto en el `.env`.
- **«No pudimos enviar el correo»:** `docker compose logs auth | grep "Fallo SMTP"` muestra el motivo exacto. El más común es
  una contraseña de aplicación mal escrita (deben ser 16 letras).
- **La construcción falla:** copia el último mensaje de error; lo más probable es un detalle de un `Dockerfile` que nunca se
  había construido.
- **No puedo entrar por SSH:** tu IP cambió. En el grupo de seguridad, edita la regla SSH y vuelve a elegir *Mi IP*.

## Seguridad antes de enseñarlo a otras personas

- El grupo de seguridad solo permite 22 (tu IP), 80 y 443.
- Usa una contraseña **fuerte** para la cuenta de administradora; no reutilices una que hayas escrito en un chat.
- No subas jamás el `.env` ni el archivo `.pem`.
- Las políticas de privacidad y de cookies son un texto base: antes de abrirlo a usuarios reales las debe revisar un abogado, y
  `CONTACT_EMAIL` debe ser un correo que alguien lea.
- El límite de peticiones por IP funciona porque `TRUSTED_PROXY_HOPS=1` (Caddy pasa la IP real). Un ataque desde muchas IP a la
  vez necesitaría una protección externa (por ejemplo Cloudflare).

## Opción para producción seria (más cara y con más trabajo)

| Pieza | Servicio de AWS |
|---|---|
| Imágenes | ECR |
| Los 5 contenedores | ECS Fargate, con **una sola tarea** para `web`, `weather` y `forecast` (por el planificador interno y el límite por IP en memoria) |
| Base de datos | RDS PostgreSQL 16, con las 4 bases de `infra/postgres/init` |
| HTTPS | Balanceador (ALB) con certificado de ACM |
| Secretos | Secrets Manager |
| Correo | Amazon SES (SMTP) |
| Fotos | S3: hay que escribir una clase nueva en `services/catalog/app/storage.py`, que es el punto de extensión |
| Nombres internos | Cloud Map (`auth`, `catalog`…), para no cambiar las URLs |

Calcula más de 100 USD al mes. No se recomienda hasta que haya usuarios reales.
