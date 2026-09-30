1. Información General

Módulo / Característica: Monitoreo y pronóstico meteorológico.

Actor Principal: Usuario autenticado.

Objetivo: Permitir al usuario consultar las condiciones meteorológicas actuales y el pronóstico de la zona turística de Honolulo, Tingo María, para facilitar la planificación de su visita.

Ubicación principal:

Lugar: Honolulo
Distrito: Mariano Dámaso Beraún
Provincia: Leoncio Prado
Departamento: Huánuco
País: Perú

Arquitectura: Microservicios.

Proveedor meteorológico: API externa de clima.

Base de datos: PostgreSQL.

2. Requerimientos Funcionales
RF01 — Autenticación

El sistema debe validar que el usuario tenga una sesión activa antes de permitir el acceso al módulo meteorológico.

RF02 — Ubicación

La interfaz debe mostrar Honolulo como ubicación principal del sistema.

La ubicación deberá estar asociada a sus coordenadas geográficas para realizar las consultas meteorológicas.

RF03 — Estado meteorológico actual

La interfaz principal debe mostrar el estado actual del clima de Honolulo.

Debe incluir como mínimo:

Temperatura.
Sensación térmica.
Humedad.
Probabilidad de lluvia.
Precipitación.
Velocidad del viento.
Estado climático.

Ejemplo visual:

┌──────────────────────────────────────┐
│ HONOLULO                              │
│ Monitoreo meteorológico              │
│                                      │
│          ☀  26°C                     │
│       Parcialmente nublado            │
│                                      │
│ Humedad       78%                     │
│ Lluvia        35%                     │
│ Viento        6 km/h                  │
└──────────────────────────────────────┘
RF04 — Pronóstico

La interfaz debe permitir visualizar el pronóstico meteorológico de Honolulo.

El usuario podrá seleccionar una fecha mediante el calendario, siguiendo el diseño que enviaste.

Por ejemplo:

        Octubre 2026

Lun Mar Mié Jue Vie Sáb Dom
              1   2   3   4
 5   6   7   8   9  10  11
12  13  14  15  16  17  18
19  20  21  22  23  24  25
26  27  28  29  30  31

Cada día podrá mostrar un indicador climático:

☀ Soleado
☁ Nublado
🌧 Lluvia
⛈ Tormenta
RF05 — Consulta de pronóstico

Al seleccionar un día del calendario, el sistema deberá solicitar al microservicio de pronóstico la información meteorológica correspondiente.

La interfaz mostrará:

Fecha seleccionada
Temperatura
Estado
Probabilidad de lluvia
Humedad
Viento
Precipitación
RF06 — Información en tiempo real

El sistema debe mostrar la fecha y hora de actualización de los datos meteorológicos.

Ejemplo:

Actualizado hace 5 minutos.

RF07 — Actualización

La interfaz deberá proporcionar una opción para actualizar la información meteorológica.

Botón:

"Actualizar clima"

RF08 — Historial meteorológico

El sistema deberá almacenar las consultas meteorológicas obtenidas de la API externa.

Cada registro debe contener:

id
ubicación
fecha
hora
temperatura
humedad
precipitación
probabilidad_lluvia
viento
nubosidad
condición
3. Evaluación del Pronóstico

Esta parte corresponde específicamente a la puntuación de la predicción del clima.

RF09 — Registro del pronóstico

El sistema deberá almacenar el pronóstico proporcionado por la API externa antes de la fecha/hora correspondiente.

Ejemplo:

01/10/2026 - 14:00

Temperatura prevista: 26°C
Probabilidad lluvia: 40%
Humedad prevista: 75%
RF10 — Comparación

Cuando exista información meteorológica observada para esa misma fecha y hora, el sistema deberá comparar:

PRONÓSTICO
    VS
OBSERVACIÓN REAL

Por ejemplo:

Temperatura prevista: 26°C
Temperatura observada: 25°C

Error: 1°C
RF11 — Puntuación

El sistema deberá calcular una puntuación de precisión del pronóstico.

La puntuación podrá considerar diferentes variables meteorológicas:

Temperatura
Precipitación
Probabilidad de lluvia
Humedad
Viento
Condición climática

El resultado podrá mostrarse como:

┌──────────────────────────┐
│ Precisión del pronóstico │
│                          │
│          91%             │
│                          │
│ Muy buena precisión      │
└──────────────────────────┘

La fórmula exacta de la puntuación deberá quedar definida como parámetro del sistema.

4. Reglas de Negocio
RN01 — Ubicación única

La versión inicial del sistema trabajará exclusivamente con:

Honolulo, Tingo María, Huánuco, Perú.

No será necesario seleccionar diferentes países.

Esto cambia respecto al ejemplo que te dio tu profesor.

RN02 — Coordenadas

Toda consulta meteorológica deberá utilizar las coordenadas configuradas para Honolulo.

RN03 — Fuente meteorológica

Los datos meteorológicos deberán provenir de un proveedor externo mediante API.

El sistema no deberá generar artificialmente los datos climáticos.

RN04 — Datos actuales

Los datos actuales deberán indicar la fecha y hora en que fueron obtenidos.

RN05 — Pronósticos

Todo pronóstico deberá almacenarse con:

fecha de emisión
fecha objetivo
hora objetivo
ubicación
variables meteorológicas

Esto es necesario para poder evaluarlo posteriormente.

RN06 — Evaluación

Un pronóstico solamente podrá recibir una puntuación definitiva cuando exista información observada correspondiente a la misma:

ubicación
fecha
hora
RN07 — Información insuficiente

Si todavía no existe información observada, el sistema deberá mostrar:

"Pronóstico pendiente de evaluación."

No deberá mostrar una puntuación inventada.

RN08 — Error de API

Si la API meteorológica no responde:

"No se pudo actualizar la información meteorológica. Inténtelo nuevamente."

El sistema podrá mostrar el último dato almacenado, indicando claramente que corresponde a una actualización anterior.

5. Criterios de Aceptación — BDD / Gherkin
Escenario 1 — Visualización del clima actual

Dado que el usuario se encuentra autenticado.

Y se encuentra en la página principal de Honolulo.

Cuando el sistema carga la información meteorológica.

Entonces debe mostrar la temperatura, humedad, precipitación, probabilidad de lluvia, viento y condición climática actual.

Escenario 2 — Selección de una fecha

Dado que el usuario se encuentra en el módulo meteorológico.

Cuando selecciona una fecha en el calendario.

Entonces el sistema debe consultar el pronóstico meteorológico correspondiente.

Y debe mostrar las condiciones previstas para dicha fecha.

Escenario 3 — Evaluación del pronóstico

Dado que existe un pronóstico almacenado para Honolulo.

Y existe información meteorológica observada para la misma fecha y hora.

Cuando el sistema ejecuta la evaluación.

Entonces debe comparar los valores pronosticados con los valores observados.

Y debe calcular el nivel de precisión.

Escenario 4 — Pronóstico aún no evaluable

Dado que existe un pronóstico almacenado.

Pero todavía no existe información meteorológica observada para la fecha correspondiente.

Cuando el usuario consulta la evaluación.

Entonces el sistema debe indicar:

"Pronóstico pendiente de evaluación."

Escenario 5 — API meteorológica no disponible

Dado que el usuario solicita información meteorológica.

Cuando el proveedor externo no responde.

Entonces el sistema debe informar que el servicio meteorológico no está disponible.

Y no debe presentar datos como actuales si no puede confirmar su actualización.

Escenario 6 - Cuenta de administrador

El administrador puede modificar la informacion de las descripciones de los luegares y fotos

Esenario 7

Se deve poder logear con cuentas de Gmail con autentificacion y tiene que llegar un correo con un codigo de confirmacion y al registrarse el usuario tine que confirmar su concentimiento en las politicas de privacidad

Esenario 8 

El usuario al registrarse pedira datos reales de nombre apellido 

Esenario 9 

El usuario puede ver la informacion mas no comentar ni puntuar con estrellas si no esta resgistrado y logueado

6. Diseño de API
Obtener clima actual
GET /api/v1/weather/current

Respuesta:

{
    "location": "Honolulo",
    "updated_at": "2026-09-30T14:00:00",
    "weather": {
        "temperature": 26.4,
        "feels_like": 27.1,
        "humidity": 78,
        "precipitation": 0.4,
        "rain_probability": 35,
        "wind_speed": 6.2,
        "cloud_cover": 64,
        "condition": "Parcialmente nublado"
    }
}
Obtener pronóstico
GET /api/v1/weather/forecast?date=2026-10-01

Respuesta:

{
    "location": "Honolulo",
    "date": "2026-10-01",
    "forecast": [
        {
            "time": "08:00",
            "temperature": 22.5,
            "humidity": 82,
            "rain_probability": 40,
            "precipitation": 0.2,
            "condition": "Nublado"
        },
        {
            "time": "14:00",
            "temperature": 27.1,
            "humidity": 70,
            "rain_probability": 55,
            "precipitation": 1.1,
            "condition": "Lluvia ligera"
        }
    ]
}
Obtener evaluación
GET /api/v1/weather/evaluation/{forecast_id}

Respuesta:

{
    "location": "Honolulo",
    "forecast_id": 152,
    "status": "evaluated",
    "accuracy_score": 91.5,
    "variables": {
        "temperature": {
            "predicted": 25.0,
            "observed": 24.2,
            "error": 0.8
        },
        "humidity": {
            "predicted": 75,
            "observed": 78,
            "error": 3
        },
        "rain": {
            "predicted": true,
            "observed": true,
            "correct": true
        }
    }
}