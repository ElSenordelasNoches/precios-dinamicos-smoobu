# Revisión periódica de precios (cada dos días)

Instrucciones para la sesión programada. Trabaja en la carpeta del proyecto y
usa el intérprete de Python de la máquina (en Windows puede hacer falta la ruta
completa). No pidas confirmación para lo que aquí se describe; lo que se salga
de aquí, déjalo anotado en el informe y no lo hagas.

## 1. Ciclo de precios

Ejecuta `python -m precios ejecutar` y lee el resumen que imprime.

- Rastrea el mercado, calcula y, si la configuración está en modo `automatico`,
  envía los precios a Smoobu. En modo `simulacion` solo calcula. **No cambies el
  modo ni fuerces el envío con `--aplicar`:** eso lo decide el propietario.
- Si el rastreo falla o queda incompleto, la herramienta sigue con el último
  rastreo bueno. Si falla dos ejecuciones seguidas, mira el error: lo normal es
  que la página haya cambiado de estructura (`precios/mercado.py`,
  función `leer_resultados`). Arréglalo solo si el cambio es pequeño y las
  pruebas (`python -m unittest discover tests`) siguen pasando. Si el sitio
  responde 403 o 429, no insistas ni busques rodeos: anótalo.
- Si el envío queda bloqueado por el cortafuegos, no lo fuerces: explica en el
  informe qué lo ha bloqueado.

## 2. Eventos locales (una vez por semana basta)

Si `datos/eventos.json` no existe o su campo `revisado` tiene más de 7 días,
busca en la web eventos de los próximos 9 meses que muevan la demanda de
alojamiento en la zona de los apartamentos (mira `config.json` → `mercado`):
carnaval y fiestas patronales con sus fechas oficiales de este año, festivales,
pruebas deportivas, ferias, congresos, escalas masivas, y los periodos de
vacaciones escolares de los principales mercados emisores.

Escribe solo lo que hayas podido comprobar en una fuente fiable (ayuntamiento,
cabildo, organizador) y con fechas concretas:

```json
{
  "revisado": "AAAA-MM-DD",
  "eventos": [
    {"nombre": "…", "desde": "AAAA-MM-DD", "hasta": "AAAA-MM-DD",
     "ajuste_pct": 8, "fuente": "https://…"}
  ]
}
```

Criterio para `ajuste_pct`: 4 a 6 para eventos menores, 8 a 12 para los que
llenan la localidad, hasta 20 solo para los excepcionales. No dupliques
Navidad, Semana Santa ni los puentes nacionales: ya se calculan solos. Quita
los eventos pasados. Si has cambiado el archivo, ejecuta
`python -m precios ejecutar --sin-escanear` para que los precios lo recojan.

## 3. Vigilancia

Mira en el resumen y avisa en el informe si ves algo de esto:

- un apartamento con ocupación muy por debajo del mercado a 30 días;
- el precio base pegado a su tope o a su suelo de autoajuste durante varias
  ejecuciones (el propietario debería mover su precio base);
- muchas noches en el precio mínimo o en el máximo;
- huecos de una o dos noches a menos de una semana que siguen sin venderse;
- verificación del envío con fechas que no coinciden.

## 4. Informe

Guarda en `datos/informes/AAAA-MM-DD.md` un informe breve en español, en prosa,
sin relleno: qué ha cambiado en el mercado, qué se ha hecho con los precios de
cada apartamento, qué eventos se han añadido o quitado y qué conviene que
revise el propietario. Termina la sesión con un resumen de tres o cuatro líneas.
