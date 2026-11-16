# Precios dinámicos para alquiler vacacional (Smoobu)

Herramienta libre de *smart pricing* para pequeños propietarios que gestionan
sus alojamientos con [Smoobu](https://www.smoobu.com). Cada pocos días rastrea
el mercado de la zona, calcula el precio y la estancia mínima de cada noche del
próximo año y los envía a Smoobu por su API, que a su vez los reparte a Airbnb,
Booking.com y el resto de portales conectados.

Hace lo mismo que las herramientas comerciales (PriceLabs, Beyond, Wheelhouse o
los Precios dinámicos de Smoobu), con dos diferencias: no cuesta nada por
propiedad y **cada precio se puede explicar**, factor a factor.

- Solo Python 3.10 o superior. Sin dependencias externas.
- Panel local para configurarlo todo y ver el calendario con el porqué de cada precio.
- Pensada para ejecutarse sola con una tarea programada (por ejemplo, una
  sesión de Claude que además revisa eventos locales y vigila anomalías).

## Cómo calcula el precio

```
precio = base × estacionalidad × día de la semana × eventos
              × demanda del mercado × antelación × ritmo propio
              × huecos × ajustes manuales
→ límites (mínimo, máximo, suelo de fechas lejanas) → redondeo → cambio máximo por ejecución
```

| Factor | Qué hace |
| --- | --- |
| **Precio base** | El precio de referencia de una noche normal. Puede autoajustarse poco a poco: sube si el alojamiento se llena estando por debajo del mercado y baja si se queda vacío, siempre dentro de un margen. |
| **Estacionalidad** | Perfil por meses (suavizado para que no haya saltos el día 1) combinado con lo que hace el mercado: cuánto más caro o barato está *cada* competidor en esa fecha respecto a su propio precio habitual. |
| **Día de la semana** | Ajuste por noche (viernes, sábado…). |
| **Eventos y festivos** | Navidad, Semana Santa y puentes se calculan solos; el resto se añade a mano o lo aporta la revisión periódica en `datos/eventos.json`. |
| **Demanda del mercado** | Ocupación estimada de la competencia en cada fecha frente a lo normal para esa antelación, más el ritmo al que se está llenando entre un rastreo y el siguiente. |
| **Antelación** | Descuento gradual de última hora para lo que sigue libre y prima para las fechas lejanas. |
| **Ritmo propio** | Compara lo ya reservado alrededor de cada fecha con lo que debería haber a esa antelación para acabar en la ocupación objetivo (la curva se aprende del historial propio de reservas). |
| **Huecos** | Los huecos entre reservas más cortos que la estancia mínima se abren bajando la estancia mínima a su medida, con el recargo o descuento que se configure. |
| **Estancia mínima dinámica** | Se mantiene la mínima habitual, pero las noches libres que no son hueco se abren a estancias más cortas al acercarse la fecha (siempre, o solo si se va retrasado de reservas), con un recargo que reparte la limpieza entre menos noches. |
| **Limpieza y beneficio por reserva** | Usted indica lo que cuesta cada limpieza y el beneficio mínimo que debe dejar una reserva: ninguna noche se vende por debajo de (limpieza + beneficio) / estancia mínima. |
| **Freno de descuentos** | Si el precio ya está por debajo del cuartil barato de la competencia, no se descuenta por demanda floja ni por retraso de reservas. |
| **Ajustes manuales** | Precio fijo, porcentaje o «no tocar» por rango de fechas. Además, si usted cambia un precio a mano en Smoobu, la herramienta lo detecta y lo respeta. |

Tres estrategias (prudente, equilibrada, ambiciosa) escalan la fuerza de los
factores de demanda.

## De dónde salen los datos de mercado

De la búsqueda pública de Airbnb, acotada a un rectángulo del mapa que usted
define (su competencia directa). Para una rejilla fija de fechas de llegada se
anota qué alojamientos enteros siguen disponibles y a qué precio por noche. De
ahí salen la ocupación del mercado, el índice de precio de igual a igual y el
ritmo de reservas. Las ventanas se anclan a una rejilla fija para que dos
rastreos en días distintos midan exactamente las mismas estancias.

**Booking.com** se añade como segunda fuente de demanda, pero no leyendo su web
(no lo permite), sino con una API de RapidAPI que tiene plan gratuito (500
consultas al mes). Una consulta por ventana de fechas devuelve cuántos
alojamientos del destino quedan libres; con eso se mide su ocupación y se
promedia con la de Airbnb. Para activarlo: cree una clave en RapidAPI,
suscríbase al plan gratuito de la API «Booking» (apidojo), póngala en
`secretos.json` (`"rapidapi": {"clave": "…"}`), busque su destino con
`python -m precios booking-destino "Nombre"` y anótelo en
`config.json` → `mercado.booking.destino`.

Uso responsable: solo páginas públicas, sin iniciar sesión, con pausas entre
consultas y unas decenas de ventanas por rastreo. Si el sitio pide parar
(códigos 403 o 429), el rastreo se detiene. No se saltan protecciones contra
robots. Revise las condiciones de uso del portal antes de utilizarlo; usted es
responsable del uso que haga.

## Puesta en marcha

1. Copie `config.ejemplo.json` a `config.json` y ponga sus apartamentos (el `id`
   es el de Smoobu) con su precio base, mínimo y máximo.
2. En Smoobu, **Ajustes → Avanzado → Claves API**, cree una clave y genere el
   secreto. No marque «Guest Data (PII)»: la herramienta no necesita datos de
   los huéspedes. Copie `secretos.ejemplo.json` a `secretos.json` y pegue ambos
   valores (o use las variables `SMOOBU_API_KEY` y `SMOOBU_API_SECRET`).
3. Compruebe la conexión y haga la primera pasada en simulación:

```
python -m precios estado
python -m precios ejecutar
python -m precios panel
```

4. Cuando los precios le convenzan, cambie el modo a `automatico` en el panel.

## Órdenes

| Orden | Qué hace |
| --- | --- |
| `python -m precios estado` | Comprueba la conexión con Smoobu y lista los apartamentos. |
| `python -m precios escanear` | Rastrea el mercado y guarda la foto del día en `datos/mercado/`. |
| `python -m precios calcular` | Calcula y enseña el resumen. No envía nada. |
| `python -m precios ejecutar` | Rastrea, calcula y, en modo automático, envía a Smoobu. Opciones: `--sin-escanear`, `--aplicar`, `--no-aplicar`, `--rapido`. |
| `python -m precios resumen` | Resumen de la última ejecución. |
| `python -m precios panel` | Abre el panel en `http://127.0.0.1:8777`. |
| `python -m precios booking-destino "Nombre"` | Busca el identificador de un destino en Booking (gasta una consulta). |
| `python -m precios clave-panel` | Fija la contraseña para entrar al panel desde fuera. |

## Seguridad

- En modo `simulacion` no se toca Smoobu.
- Antes de cada envío, un cortafuegos comprueba que ningún precio se sale de
  los límites y que no hay cambios bruscos generalizados; si algo no cuadra,
  no envía y lo dice.
- Tras enviar, relee Smoobu y verifica que lo guardado coincide.
- `config.json`, `secretos.json` y `datos/` están fuera del control de versiones.
- El panel solo escucha en la propia máquina.

## Verlo desde el móvil

El panel solo escucha en la propia máquina. Para abrirlo desde fuera se publica
con un túnel de Cloudflare en un dominio propio, protegido con contraseña:

1. `python -m precios clave-panel --host precios.midominio.com` genera la
   contraseña (queda en `datos/clave_panel.txt`; apúntela y borre el archivo).
   Con `--pedir` la escribe usted.
2. Cree el túnel y su registro DNS con `cloudflared` y deje su configuración en
   `datos/tunel.yml`:

```
tunnel: <id del túnel>
credentials-file: <ruta al .json de credenciales>
ingress:
  - hostname: precios.midominio.com
    service: http://127.0.0.1:8777
  - service: http_status:404
```

3. `python servicio.py` arranca el panel y el túnel, y relanza el túnel si se
   cae. En Windows, «Instalar arranque automatico.cmd» lo deja arrancando con
   la sesión.

Desde fuera solo se atiende ese dominio, hay que iniciar sesión, los intentos
fallidos se limitan y las órdenes que cambian algo exigen una cabecera que
ninguna otra web puede enviar en nombre del usuario. El equipo tiene que estar
encendido.

## Ejecución periódica

`tarea_programada.md` contiene las instrucciones para una sesión programada de
Claude (cada dos días): ejecuta el ciclo, revisa eventos locales y actualiza
`datos/eventos.json`, y avisa si ve algo raro. También sirve cualquier
programador de tareas que lance `python -m precios ejecutar`. Y si el servicio
permanente está en marcha, hace de red de seguridad: cuando pasan dos días sin
rastreo, ejecuta el ciclo él solo a las 10:30.

## Pruebas

```
python -m unittest discover tests
```

## Limitaciones

- El mercado se mide por lo que queda disponible, no por reservas reales: es una
  estimación, como en todas las herramientas de este tipo.
- En zonas con muy pocos comparables las señales de mercado son ruidosas; baje
  su peso o use solo el perfil manual.
- No es asesoramiento financiero. Fije siempre un precio mínimo que esté
  dispuesto a aceptar.

## Licencia

MIT. Véase `LICENSE`.
