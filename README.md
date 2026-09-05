# nacelab

Plataforma pública de datos económicos de México. Indicadores oficiales del INEGI y
Banxico, actualizados automáticamente, con el código que los obtiene a la vista.

> **Principio:** todo resultado debe poder seguirse hasta su origen.

**En línea:** https://nacelab-314189642979.us-central1.run.app

**Estado:** pipeline completo funcionando. Tres series verificadas. La ingesta todavía
se corre a mano — automatizarla con Cloud Scheduler es lo siguiente, así que los datos
se actualizan cuando alguien ejecuta el job.

---

## Los cuatro pilares

| | Pregunta que responde |
|---|---|
| 📊 **Datos** | ¿Qué está pasando? |
| 🧪 **Análisis** | ¿Por qué? |
| 🤖 **Modelos** | ¿Qué podría pasar? |
| 💻 **Reproducibilidad** | ¿Cómo lo compruebo yo mismo? |

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

# 3. Probar
python scripts/probar_series.py
python scripts/probar_series.py --crudo    # ver el JSON completo
```

---

## Estructura

```
catalog/series.yml   El catálogo. Única fuente de verdad sobre qué series existen
core/                fetch (APIs) · load (MERGE a bronze) · transform (gold) · bq (auth)
sql/                 El SQL de gold, fuera de Python: se puede correr a mano
app/                 Streamlit. Lee gold, no calcula
scripts/             setup_gcp · cargar_bronze · construir_gold · probar_*
```

Flujo completo:

```
API  →  bronze.serie_obs  →  gold.gold_indicador  →  Streamlit
        MERGE idempotente    variaciones y percentil    solo lee
        sa-ingest            sa-transform               sa-app
```

```bash
python scripts/cargar_bronze.py     # API -> bronze
python scripts/construir_gold.py    # bronze -> gold
streamlit run app/main.py           # ver el sitio
```

Regla: **si algo calcula, va en `core/`. Si algo dibuja, va en la app.**
El día que llegue otro frontend, `core/` no se toca.

---

## Sobre los ids de series

Un id copiado de un ejemplo de documentación **no es un id verificado**. El catálogo
marca cada serie con `verificado: true|false`, y `scripts/probar_series.py` consulta la
API para enseñarte qué devuelve realmente cada id — nombre, unidad, frecuencia y último
dato — antes de que construyas nada encima.

Ninguna serie pasa a `verificado: true` sin que un humano haya visto esa salida.

---

## Seguridad

- Los tokens van en `.env`, que está en `.gitignore`. Nunca en el código.
- El token de Banxico viaja en el header `Bmx-Token`, nunca en la URL: hay tokens de
  terceros indexados en buscadores por haberlos puesto como query param.
- El INEGI sí exige el token dentro de la ruta, por eso `fetch.py` nunca imprime una
  URL del INEGI sin enmascararla antes.

## Fuentes

- [INEGI — Banco de Indicadores](https://www.inegi.org.mx/servicios/api_indicadores.html)
- [Banxico — SIE API](https://www.banxico.org.mx/SieAPIRest/service/v1/doc/series)
