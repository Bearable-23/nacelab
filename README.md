# nacelab

Plataforma pública de datos económicos de México. Indicadores oficiales del INEGI y
Banxico, con el código que los obtiene a la vista.

> **Principio:** todo resultado debe poder seguirse hasta su origen.

**En línea:** https://nacelab-314189642979.us-central1.run.app

**Estado:** el pipeline funciona de punta a punta y el sitio está publicado. Tres
series verificadas. **La ingesta se corre a mano**, así que los datos se actualizan
cuando alguien ejecuta el job — automatizarla con Cloud Scheduler sigue pendiente.

---

## Los cuatro pilares

| | Pregunta que responde | Estado |
|---|---|---|
| 📊 **Datos** | ¿Qué está pasando? | construido |
| 🧪 **Análisis** | ¿Por qué? | a medias: hay interpretación por serie, no cruces |
| 🤖 **Modelos** | ¿Qué podría pasar? | no existe todavía |
| 💻 **Reproducibilidad** | ¿Cómo lo compruebo yo mismo? | construido |

La columna de estado está aquí a propósito. Un README que promete cuatro pilares
cuando hay dos es la misma clase de error que este proyecto persigue en los datos.

---

## Cómo correrlo

```bash
# 1. Entorno
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 2. Tokens (ambos gratuitos)
copy .env.example .env
#   INEGI:   https://www.inegi.org.mx/servicios/api_indicadores.html
#   Banxico: https://www.banxico.org.mx/SieAPIRest/service/v1/token

# 3. Probar sin tocar BigQuery
python scripts/probar_series.py
python scripts/probar_series.py --crudo          # ver el JSON completo
python scripts/probar_series.py --id 736181      # interrogar una clave suelta
```

---

## Estructura

```
catalog/series.yml   El catálogo. Única fuente de verdad sobre qué series existen,
                     a qué tema pertenecen, cómo se colapsan y cuánta tolerancia
                     admiten al buscar su dato de referencia
core/                fetch (APIs) · load (bronze) · transform (gold) · bq (auth)
                     catalog (lectura y validación del YAML)
sql/                 El SQL de gold, fuera de Python: se puede correr a mano
app/                 Streamlit. Lee gold, no calcula
  componentes.py       dibuja: tarjeta, minigráfica, serie con ejes
  filtros.py           el panel lateral y su sincronía con la URL
  vistas.py            las páginas
  main.py              solo arma el menú
scripts/             carga, construcción y pruebas
.streamlit/          el tema. Sin esto el sitio sigue la preferencia del visitante
```

Flujo completo:

```
API  →  bronze.serie_obs  →  gold.gold_indicador     →  Streamlit
        MERGE idempotente     variaciones, percentil     solo lee
        sa-ingest             sa-transform               sa-app
            │                       │
            ├─ serie_dim            └─ gold.panel_mensual
            │  el catálogo             todo alineado a mes,
            │  proyectado a BQ         para poder cruzarlo
            │
            └─ serie_obs_historial
               lo que la fuente revisó, antes de pisarlo
```

```bash
python scripts/cargar_bronze.py     # API -> bronze
python scripts/construir_gold.py    # bronze -> gold (los dos modelos)
streamlit run app/main.py           # ver el sitio
```

Regla: **si algo calcula, va en `core/`. Si algo dibuja, va en la app.**
El día que llegue otro frontend, `core/` no se toca.

---

## El catálogo manda

Agregar un indicador es editar `catalog/series.yml`. Nunca escribir código.

El catálogo decide qué series existen, **en qué sección del sitio aparecen** (el menú
se genera de ahí: un tema sin series verificadas no produce una página vacía), si la
tarjeta muestra nivel o variación, dónde va la línea de referencia, cómo se colapsa la
serie a frecuencia mensual y cuántos días puede retroceder para hallar su dato de
comparación.

### Dónde se encuentran las claves

Los tokens y las claves salen de sitios distintos, y solo los primeros estaban
documentados aquí:

| Para qué | Dónde |
|---|---|
| Clave de un indicador del INEGI | [Constructor de consultas](https://inegi.org.mx/app/querybuilder2/default.html?2.0=) |
| Explorar indicadores del INEGI por tema | [Banco de Indicadores](https://www.inegi.org.mx/app/indicadores/) |
| Id de una serie de Banxico (tipo `SF43718`) | [SIE](https://www.banxico.org.mx/SieInternet/) |

El BIE viejo (`inegi.org.mx/sistemas/bie/`) responde 500 y no sirve para esto, igual
que `BIE` a secas ya no sirve como parámetro de banco.

Una vez que tengas la clave, antes de escribirla en el catálogo:

```bash
python scripts/probar_series.py --id 736181
```

Interroga los dos bancos, calcula el salto típico entre observaciones y avisa si la
serie lleva meses sin actualizarse.

### Sobre los ids de series

Un id copiado de un ejemplo de documentación **no es un id verificado**. El catálogo
marca cada serie con `verificado: true|false`, y `scripts/probar_series.py` consulta la
API para enseñarte qué devuelve realmente cada id — frecuencia, última actualización y
último dato — antes de que construyas nada encima.

Ninguna serie pasa a `verificado: true` sin que un humano haya visto esa salida.

Dos trampas ya documentadas: el parámetro de banco no es universal (`BIE-BISE` para los
económicos de coyuntura, `BISE` para censos y encuestas), y **un id que responde 200 no
es un id vigente** — hay series descontinuadas que devuelven datos de aspecto
perfectamente normal. Lo único que las delata es `LASTUPDATE`.

---

## Decisiones que no son obvias

**Las variaciones se calculan por fecha, no con `LAG`.** `LAG(12)` sobre una serie a la
que le falta un mes compara contra hace trece y devuelve un número plausible y falso.
El join por fecha devuelve nulo cuando no hay contra qué comparar.

**Con tolerancia por frecuencia.** Una serie hábil-diaria no cotiza fines de semana, así
que "hace exactamente un mes" muchas veces no existe: la regla estricta dejaría un 37%
de la serie sin variación. Para esas se acepta el dato anterior más cercano dentro de
una ventana declarada — siempre hacia atrás, nunca hacia adelante — y gold registra
contra qué fecha comparó realmente.

**Las revisiones se guardan.** El INEGI revisa datos históricos. El MERGE pisaba el
valor anterior y desaparecía, lo cual da igual para un tablero y es fatal para evaluar
modelos: un pronóstico solo se juzga contra los datos que existían cuando se hizo.

**El panel mensual no rellena huecos.** Una serie decenal deja casi todos los meses
vacíos, y así debe verse. Los censos de población son 15 observaciones reales entre
1910 y 2020: rellenarlas hacia adelante sobre la rejilla mensual completa las
convertiría en 1,399 valores inventados, y una regresión sobre eso daría errores
estándar ridículamente pequeños sin que nada pareciera roto.

---

## Pruebas

Todas se corren a mano; **no hay CI todavía**.

```bash
python scripts/probar_series.py      # ¿los ids son lo que creemos?
python scripts/probar_bq.py          # ¿la autenticación y los permisos?
python scripts/probar_app.py         # ¿la app lee gold y produce texto?
python scripts/probar_tolerancia.py  # ¿la regla de tolerancia funciona?
python scripts/probar_panel.py       # ¿el colapso a mensual funciona?
```

Las dos últimas prueban rutas que **ningún dato real ejercita todavía**: las reglas de
tolerancia y de colapso solo se diferencian en series más finas que mensual, y hoy no
hay ninguna cargada. Generan una serie hábil-diaria sintética y la meten por el SQL de
verdad — leen el `.sql` y sustituyen las tablas de origen. Si alguien cambia la regla en
el archivo, la prueba lo ve; si ya no encuentra las tablas que sustituye, aborta en vez
de dar un verde silencioso.

---

## Seguridad

- Los tokens van en `.env`, que está en `.gitignore`. Nunca en el código.
- El token de Banxico viaja en el header `Bmx-Token`, nunca en la URL: hay tokens de
  terceros indexados en buscadores por haberlos puesto como query param.
- El INEGI sí exige el token dentro de la ruta, por eso `fetch.py` nunca imprime una
  URL del INEGI sin enmascararla antes.
- Tres identidades separadas: la cuenta que sirve el sitio no puede leer los datos
  crudos, solo la tabla ya calculada.

## Fuentes

- [INEGI — Banco de Indicadores](https://www.inegi.org.mx/servicios/api_indicadores.html)
- [Banxico — SIE API](https://www.banxico.org.mx/SieAPIRest/service/v1/doc/series)
