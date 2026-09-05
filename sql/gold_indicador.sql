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

-- El catálogo, proyectado a BigQuery por cargar_bronze.py. De aquí sale la
-- única cosa que este SQL no puede deducir de las observaciones: cuántos días
-- se puede retroceder para encontrar el dato de referencia.
dim AS (
  SELECT serie_id, tolerancia_dias
  FROM `{proyecto}.nacelab_bronze.serie_dim`
),

objetivos AS (
  SELECT
    b.fuente,
    b.serie_id,
    b.fecha,
    b.valor,
    b.ingested_at,
    -- COALESCE a 0: una serie que aún no esté en la dim se comporta con la
    -- regla estricta en vez de quedarse sin variaciones.
    COALESCE(d.tolerancia_dias, 0) AS tolerancia,
    DATE_SUB(b.fecha, INTERVAL 1 MONTH) AS objetivo_mes,
    DATE_SUB(b.fecha, INTERVAL 1 YEAR)  AS objetivo_anio
  FROM base AS b
  LEFT JOIN dim AS d USING (serie_id)
),

-- Las variaciones NO se calculan con LAG(1) ni LAG(12).
--
-- LAG asume que las observaciones están completas y equiespaciadas. Si a una
-- serie mensual le falta un mes, LAG(12) compararía contra hace 13 meses y
-- devolvería un número plausible pero falso. Y en una serie irregular como
-- los censos de población, LAG(12) no significa nada.
--
-- Se busca la observación de referencia POR FECHA. Con tolerancia 0 eso es la
-- fecha exacta, y si no existe el resultado es nulo: un hueco honesto vale
-- más que un número inventado.
--
-- Con tolerancia > 0 se acepta la observación más reciente ANTERIOR o igual
-- al objetivo, dentro de la ventana. Eso es para las series hábil-diarias:
-- el tipo de cambio no cotiza sábados ni festivos, así que "hace exactamente
-- un mes" muchas veces no existe, y la regla estricta dejaría ~30% de la
-- serie sin variación sin que nada estuviera mal.
--
-- Siempre hacia atrás, nunca hacia adelante: tomar un dato posterior al
-- objetivo sería mirar el futuro para explicar el presente.
-- El primer intento fue un subquery correlacionado con ORDER BY ... LIMIT 1,
-- que se lee muy bien y BigQuery rechaza: "Correlated subqueries that
-- reference other tables are not supported unless they can be de-correlated".
-- Esta es la forma de-correlacionada: un join por RANGO que trae todos los
-- candidatos de la ventana, y QUALIFY que se queda con el más reciente.
--
-- Con tolerancia 0 el rango colapsa a una igualdad y esto se comporta
-- exactamente como la versión anterior. No hay dos caminos que mantener.
ref_mes AS (
  SELECT
    o.serie_id,
    o.fecha,
    r.fecha AS ref_fecha,
    r.valor AS ref_valor
  FROM objetivos AS o
  JOIN base AS r
    ON  r.serie_id = o.serie_id
    AND r.fecha <= o.objetivo_mes
    AND r.fecha >= DATE_SUB(o.objetivo_mes, INTERVAL o.tolerancia DAY)
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY o.serie_id, o.fecha ORDER BY r.fecha DESC
  ) = 1
),

ref_anio AS (
  SELECT
    o.serie_id,
    o.fecha,
    r.fecha AS ref_fecha,
    r.valor AS ref_valor
  FROM objetivos AS o
  JOIN base AS r
    ON  r.serie_id = o.serie_id
    AND r.fecha <= o.objetivo_anio
    AND r.fecha >= DATE_SUB(o.objetivo_anio, INTERVAL o.tolerancia DAY)
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY o.serie_id, o.fecha ORDER BY r.fecha DESC
  ) = 1
),

-- LEFT JOIN, no JOIN: una observación sin referencia dentro de la ventana
-- tiene que sobrevivir con variación nula. Con INNER se perderían filas
-- enteras, que es peor que el hueco que estamos evitando.
variaciones AS (
  SELECT
    o.fuente,
    o.serie_id,
    o.fecha,
    o.valor,
    o.ingested_at,
    m.ref_fecha AS fecha_ref_mensual,
    a.ref_fecha AS fecha_ref_anual,
    SAFE_DIVIDE(o.valor - m.ref_valor, m.ref_valor) * 100 AS var_mensual,
    SAFE_DIVIDE(o.valor - a.ref_valor, a.ref_valor) * 100 AS var_anual
  FROM objetivos AS o
  LEFT JOIN ref_mes  AS m ON m.serie_id = o.serie_id AND m.fecha = o.fecha
  LEFT JOIN ref_anio AS a ON a.serie_id = o.serie_id AND a.fecha = o.fecha
)

SELECT
  fuente,
  serie_id,
  fecha,

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
  CAST(valor AS FLOAT64)       AS valor,
  CAST(var_mensual AS FLOAT64) AS var_mensual,
  CAST(var_anual AS FLOAT64)   AS var_anual,

  -- Contra qué fecha se comparó realmente.
  --
  -- Con tolerancia 0 esto es siempre el objetivo exacto y parece redundante.
  -- Con tolerancia > 0 no lo es: dice si la variación de hoy se midió contra
  -- hace 30 días o contra hace 33 porque los otros tres fueron festivos. Una
  -- comparación aproximada que no dice contra qué se aproximó no se puede
  -- auditar, y todo aquí tiene que poder seguirse hasta su origen.
  fecha_ref_mensual,
  fecha_ref_anual,

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
