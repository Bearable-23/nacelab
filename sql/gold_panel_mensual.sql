-- gold.panel_mensual — todas las series alineadas a mes, para poder cruzarlas.
--
-- POR QUÉ EXISTE
-- --------------
-- `gold_indicador` es largo y por serie: sirve para dibujar una serie y para
-- interpretarla, pero no para modelar. Una regresión necesita observaciones
-- CONTEMPORÁNEAS de varias series, y las series de este proyecto no comparten
-- frecuencia: el tipo de cambio es hábil-diario, el INPC mensual, los censos
-- decenales. Sin una rejilla común no hay econometría posible.
--
-- FORMATO LARGO, NO ANCHO
-- -----------------------
-- Una regresión quiere columnas (fecha | inpc | fix | igae), pero un PIVOT en
-- BigQuery exige la lista de columnas escrita a mano, y entonces agregar una
-- serie al catálogo obligaría a editar este archivo. Eso rompe la regla del
-- proyecto. Se queda largo —una fila por (mes, serie)— y quien modele pivota
-- en pandas con una línea. La rejilla es lo caro; el pivote no.
--
-- LO QUE ESTE MODELO NO HACE
-- --------------------------
-- No rellena huecos. Una serie decenal como los censos deja casi todos los
-- meses vacíos, y así debe verse: los 15 censos de población entre 1910 y 2020,
-- rellenados hacia adelante sobre la rejilla mensual completa, se convertirían
-- en 1,399 valores inventados, y una regresión sobre eso daría errores estándar
-- ridículamente pequeños sin que nada pareciera roto.
-- Interpolar es una decisión de quien analiza, tomada a la vista, no algo que
-- el almacén deba hacer a escondidas.

WITH base AS (
  SELECT serie_id, fecha, valor
  FROM `{proyecto}.nacelab_bronze.serie_obs`
),

dim AS (
  SELECT serie_id, frecuencia, agregacion
  FROM `{proyecto}.nacelab_bronze.serie_dim`
),

-- Se calculan los tres colapsos y luego se elige. Cuesta lo mismo que
-- calcular solo uno con estos volúmenes, y deja el SQL sin ramas.
colapsos AS (
  SELECT
    DATE_TRUNC(b.fecha, MONTH) AS mes,
    b.serie_id,
    COUNT(*) AS n_obs,
    MIN(b.fecha) AS primera_obs,
    MAX(b.fecha) AS ultima_obs,
    -- El valor de la ÚLTIMA fecha del mes, no el máximo valor del mes: son
    -- cosas distintas y confundirlas es un error clásico. ARRAY_AGG ordenado
    -- con LIMIT 1 es la forma de "argmax" en BigQuery.
    ARRAY_AGG(b.valor ORDER BY b.fecha DESC LIMIT 1)[OFFSET(0)] AS fin_de_mes,
    AVG(b.valor) AS promedio,
    SUM(b.valor) AS suma
  FROM base AS b
  GROUP BY mes, serie_id
)

SELECT
  c.mes,
  c.serie_id,

  -- CAST a FLOAT64 por la misma razón que en gold_indicador: un NUMERIC llega
  -- al navegador como decimal128 y se dibuja multiplicado por mil millones.
  -- AVG y SUM sobre NUMERIC devuelven NUMERIC, así que el riesgo está aquí
  -- igual que allá.
  CAST(
    CASE d.agregacion
      WHEN 'promedio'  THEN c.promedio
      WHEN 'suma'      THEN c.suma
      WHEN 'fin_de_mes' THEN c.fin_de_mes
    END AS FLOAT64
  ) AS valor,

  -- Sin ELSE en el CASE, a propósito: un método no reconocido da NULL y se
  -- ve. Con `ELSE c.fin_de_mes` una serie mal configurada se colapsaría por
  -- una regla que nadie pidió y el número saldría plausible. Python ya valida
  -- el catálogo antes de escribir la dim; esto es la segunda red.
  d.agregacion AS metodo,

  -- Con cuántas observaciones se armó el mes. Un mes de tipo de cambio con 22
  -- días hábiles y uno con 3 no valen lo mismo, y quien haga econometría
  -- necesita poder filtrar por eso. En una serie mensual siempre es 1.
  c.n_obs,
  c.primera_obs,
  c.ultima_obs,
  d.frecuencia

FROM colapsos AS c
LEFT JOIN dim AS d USING (serie_id)
ORDER BY c.serie_id, c.mes
