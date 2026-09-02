# nacelab

Plataforma pública de datos económicos de México. Indicadores oficiales del INEGI y
Banxico, actualizados automáticamente, con el código que los obtiene a la vista.

> **Principio:** todo resultado debe poder seguirse hasta su origen.

**Estado:** semana 1. Verificando ids de series contra las APIs. Nada en producción.

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
catalog/series.yml     El catálogo. Única fuente de verdad sobre qué series existen
core/catalog.py        Lectura del catálogo
core/fetch.py          Descarga desde INEGI y Banxico
scripts/probar_series.py   Verifica ids y muestra qué devuelve cada API
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
