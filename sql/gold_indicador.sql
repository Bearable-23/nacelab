-- gold.indicador — lo que la app consume, ya calculado.
--
-- La app NO hace estas cuentas: las hace el job y aquí quedan materializadas.
-- Por eso es una tabla y no una vista. Una vista tendría dos problemas:
-- calcularía en cada carga de página, y sa-app no podría leerla, porque por
-- debajo consulta bronze, donde no tiene acceso (haría falta una authorized
-- view). Materializar resuelve las dos cosas de un golpe.

WITH base AS (
  SELECT fuente, serie_id, fecha, valor, ingested_at
  FROM `{proyecto}.nacelab_bronze.serie_obs`
),

-- Las variaciones se calculan uniendo contra la FECHA exacta de hace un mes
-- o un año, no con LAG(1) o LAG(12).
--
-- LAG asume que las observaciones están completas y equiespaciadas. Si a una
-- serie mensual le falta un mes, LAG(12) compararía contra hace 13 meses y
-- devolvería un número plausible pero falso. Y en una serie irregular como
-- los censos de población, LAG(12) no significa nada.
--
-- Con el join por fecha, cuando no existe la observación de referencia el
-- resultado es NULL. Un hueco honesto vale más que un número inventado.
comparadas AS (
  SELECT
    b.fuente,
    b.serie_id,
    b.fecha,
    b.valor,
    b.ingested_at,
    m.valor AS valor_mes_previo,
    a.valor AS valor_anio_previo
  FROM base AS b
  LEFT JOIN base AS m
    ON  m.serie_id = b.serie_id
    AND m.fecha    = DATE_SUB(b.fecha, INTERVAL 1 MONTH)
  LEFT JOIN base AS a
    ON  a.serie_id = b.serie_id
    AND a.fecha    = DATE_SUB(b.fecha, INTERVAL 1 YEAR)
),

variaciones AS (
  SELECT
    fuente,
    serie_id,
    fecha,
    valor,
    ingested_at,
    SAFE_DIVIDE(valor - valor_mes_previo,  valor_mes_previo)  * 100 AS var_mensual,
    SAFE_DIVIDE(valor - valor_anio_previo, valor_anio_previo) * 100 AS var_anual
  FROM comparadas
)

-- CAST a FLOAT64, y no es cosmético: es un bug que estuvo en producción.
--
-- `valor` es NUMERIC en bronze, que es lo correcto para un dato crudo. Pero
-- NUMERIC llega a pandas como `decimal.Decimal`, y Streamlit manda los
-- dataframes al navegador por Arrow, donde un Decimal se codifica como
-- decimal128 con escala 9: el entero 3948334100 más "corre el punto 9
-- lugares". El lector del navegador se quedaba con el entero, así que una
-- inflación de 3.95% se dibujaba como 3,948,334,100.
--
-- Lo traicionero es que solo se rompía la GRÁFICA. Las tarjetas y el panel
-- lateral formatean con f-strings, y un Decimal ahí sale perfecto: el sitio
-- decía "3.95%" al lado de un eje que llegaba a 8,000,000,000.
--
-- En gold servimos porcentajes a una gráfica; la exactitud decimal de NUMERIC
-- no compra nada aquí. Bronze conserva NUMERIC, que es donde sí importa.
SELECT
  fuente,
  serie_id,
  fecha,
  CAST(valor AS FLOAT64)        AS valor,
  CAST(var_mensual AS FLOAT64)  AS var_mensual,
  CAST(var_anual AS FLOAT64)    AS var_anual,

  -- Dónde cae la variación anual de hoy dentro de toda la historia de la
  -- serie. Es lo que convierte "inflación 4.2%" en "4.2%, más alta que el
  -- 71% de los meses registrados" — que es la frase que un economista puede
  -- interpretar y un número suelto no.
  --
  -- PERCENT_RANK ignora los NULL, así que los meses sin comparativo anual
  -- no ensucian la distribución.
  PERCENT_RANK() OVER (PARTITION BY serie_id ORDER BY var_anual) AS percentil_var_anual,

  ingested_at
FROM variaciones
