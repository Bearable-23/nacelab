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

SELECT
  fuente,
  serie_id,
  fecha,
  valor,
  var_mensual,
  var_anual,

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
