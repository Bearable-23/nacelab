"""Prueba la regla de tolerancia de gold contra una serie hábil-diaria falsa.

POR QUÉ EXISTE ESTE ARCHIVO
---------------------------
La tolerancia se agregó para el tipo de cambio, que es hábil-diaria. Pero el
FIX todavía no está verificado y no hay una sola observación diaria en bronze,
así que la ruta que el cambio existe para arreglar no se ejercita con nada
real. Escribir esa lógica y darla por buena porque "gold reconstruyó" sería
comprobar que corrió, no que funciona.

Esta prueba genera una serie de lunes a viernes —con sus fines de semana y sus
huecos— y la mete por el SQL DE VERDAD, no por una copia de la regla. Lee
sql/gold_indicador.sql y sustituye las dos tablas de origen por datos
sintéticos. Si alguien cambia la regla en el archivo, esta prueba lo ve.

Lo que debe pasar:
  tolerancia 0  ->  ~30% de la serie diaria sin variación mensual
  tolerancia 7  ->  casi 0% sin variación, y fecha_ref_mensual señalando el
                    día hábil anterior cuando el objetivo cayó en fin de semana

Uso:
    python scripts/probar_tolerancia.py
"""

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

from core import bq  # noqa: E402

load_dotenv()

TABLA_OBS = "`{proyecto}.nacelab_bronze.serie_obs`"
TABLA_DIM = "`{proyecto}.nacelab_bronze.serie_dim`"

# Serie de lunes a viernes durante tres años y medio, más una mensual de
# control. La diaria arranca con tolerancia declarada; la mensual con 0.
OBS_SINTETICAS = """(
  SELECT
    'prueba' AS fuente,
    'diaria_7' AS serie_id,
    d AS fecha,
    CAST(17 + MOD(ABS(FARM_FINGERPRINT(CAST(d AS STRING))), 300) / 100 AS NUMERIC) AS valor,
    CURRENT_TIMESTAMP() AS ingested_at
  FROM UNNEST(GENERATE_DATE_ARRAY('2023-01-02', '2026-07-01')) AS d
  WHERE EXTRACT(DAYOFWEEK FROM d) BETWEEN 2 AND 6

  UNION ALL

  SELECT
    'prueba', 'diaria_0', d,
    CAST(17 + MOD(ABS(FARM_FINGERPRINT(CAST(d AS STRING))), 300) / 100 AS NUMERIC),
    CURRENT_TIMESTAMP()
  FROM UNNEST(GENERATE_DATE_ARRAY('2023-01-02', '2026-07-01')) AS d
  WHERE EXTRACT(DAYOFWEEK FROM d) BETWEEN 2 AND 6

  UNION ALL

  SELECT
    'prueba', 'mensual_0', d,
    CAST(100 + MOD(ABS(FARM_FINGERPRINT(CAST(d AS STRING))), 500) / 100 AS NUMERIC),
    CURRENT_TIMESTAMP()
  FROM UNNEST(GENERATE_DATE_ARRAY('2023-01-01', '2026-07-01', INTERVAL 1 MONTH)) AS d
)"""

DIM_SINTETICA = """(
  SELECT 'diaria_7' AS serie_id, 7 AS tolerancia_dias
  UNION ALL SELECT 'diaria_0', 0
  UNION ALL SELECT 'mensual_0', 0
)"""


def linea(t=""):
    print(f"\n{'─' * 70}")
    if t:
        print(t)
        print("─" * 70)


def sql_con_datos_falsos() -> str:
    """Toma el SQL real y le cambia las dos fuentes por datos sintéticos."""
    texto = (RAIZ / "sql" / "gold_indicador.sql").read_text(encoding="utf-8")

    for marcador, reemplazo in ((TABLA_OBS, OBS_SINTETICAS), (TABLA_DIM, DIM_SINTETICA)):
        if marcador not in texto:
            raise SystemExit(
                f"No encontré {marcador} en gold_indicador.sql.\n"
                f"El SQL cambió de forma y esta prueba ya no lo está probando: "
                f"actualízala antes de confiar en su resultado."
            )
        texto = texto.replace(marcador, reemplazo)
    return texto


def main() -> int:
    cliente = bq.cliente(os.environ["GCP_PROJECT"], sa="sa-transform")

    # Solo se miden las filas donde una referencia PODRÍA existir.
    #
    # El primer mes de cualquier serie no tiene contra qué compararse, y el
    # primer año tampoco: son huecos correctos, no fallas. Medirlos sobre el
    # total mezcla el arranque de la serie con el problema de los fines de
    # semana, que es lo único que este cambio pretende arreglar.
    consulta = f"""
    WITH resultado AS ({sql_con_datos_falsos()}),
    limites AS (
      SELECT serie_id, MIN(fecha) AS inicio FROM resultado GROUP BY serie_id
    ),
    elegibles AS (
      SELECT r.*,
             r.fecha >= DATE_ADD(l.inicio, INTERVAL 1 MONTH) AS puede_mes,
             r.fecha >= DATE_ADD(l.inicio, INTERVAL 1 YEAR)  AS puede_anio
      FROM resultado r JOIN limites l USING (serie_id)
    )
    SELECT
      serie_id,
      COUNT(*) AS obs,
      COUNTIF(puede_mes) AS elegibles_mes,
      COUNTIF(puede_mes AND var_mensual IS NULL) AS sin_var_mensual,
      ROUND(SAFE_DIVIDE(COUNTIF(puede_mes AND var_mensual IS NULL),
                        COUNTIF(puede_mes)) * 100, 1) AS pct_sin_mensual,
      COUNTIF(puede_anio AND var_anual IS NULL) AS sin_var_anual,
      ROUND(SAFE_DIVIDE(COUNTIF(puede_anio AND var_anual IS NULL),
                        COUNTIF(puede_anio)) * 100, 1) AS pct_sin_anual,
      COUNTIF(fecha_ref_mensual != DATE_SUB(fecha, INTERVAL 1 MONTH)) AS ref_desplazada
    FROM elegibles
    GROUP BY serie_id
    ORDER BY serie_id
    """

    linea("Serie hábil-diaria sintética por el SQL real de gold")
    print("  (solo filas donde una referencia podría existir: sin el primer")
    print("   mes ni el primer año, que no tienen contra qué compararse)\n")
    print(f"  {'serie':<12}{'obs':>6}{'sin var_mes':>13}{'%':>7}"
          f"{'sin var_año':>13}{'%':>7}{'ref movida':>12}")

    filas = list(cliente.query(consulta).result())
    por_id = {f.serie_id: f for f in filas}
    for f in filas:
        print(f"  {f.serie_id:<12}{f.obs:>6}{f.sin_var_mensual:>13}"
              f"{f.pct_sin_mensual:>7}{f.sin_var_anual:>13}"
              f"{f.pct_sin_anual:>7}{f.ref_desplazada:>12}")

    linea("Ejemplos: qué fecha se usó realmente (tolerancia 7)")
    ejemplos = f"""
    WITH resultado AS ({sql_con_datos_falsos()})
    SELECT fecha, fecha_ref_mensual,
           DATE_SUB(fecha, INTERVAL 1 MONTH) AS objetivo,
           DATE_DIFF(DATE_SUB(fecha, INTERVAL 1 MONTH), fecha_ref_mensual, DAY) AS dias_atras
    FROM resultado
    WHERE serie_id = 'diaria_7'
      AND fecha_ref_mensual != DATE_SUB(fecha, INTERVAL 1 MONTH)
    ORDER BY fecha DESC
    LIMIT 5
    """
    print(f"  {'fecha':<12}{'objetivo':<12}{'se usó':<12}{'días atrás':>11}")
    for f in cliente.query(ejemplos).result():
        print(f"  {str(f.fecha):<12}{str(f.objetivo):<12}"
              f"{str(f.fecha_ref_mensual):<12}{f.dias_atras:>11}")

    # ------------------------------------------------------------ veredicto --
    linea("VEREDICTO")
    d0, d7, m0 = por_id["diaria_0"], por_id["diaria_7"], por_id["mensual_0"]
    fallos = []

    if d0.pct_sin_mensual < 20:
        fallos.append(
            f"Con tolerancia 0 la serie diaria debería perder ~30% de las "
            f"variaciones mensuales; perdió {d0.pct_sin_mensual}%. "
            f"Si esto baja, el problema que motivó el cambio no existe."
        )
    if d7.pct_sin_mensual > 2:
        fallos.append(
            f"Con tolerancia 7 casi no debería quedar hueco; "
            f"quedó {d7.pct_sin_mensual}%."
        )
    if d7.ref_desplazada == 0:
        fallos.append(
            "Ninguna referencia se movió de la fecha objetivo: la tolerancia "
            "no se está aplicando."
        )
    if m0.pct_sin_mensual > 5:
        fallos.append(
            f"La serie MENSUAL con tolerancia 0 perdió {m0.pct_sin_mensual}% "
            f"de variaciones. El cambio rompió el caso que ya funcionaba."
        )

    if fallos:
        for f in fallos:
            print(f"  ✗ {f}")
        return 1

    print(f"  ✓ Tolerancia 0 en diaria:  {d0.pct_sin_mensual}% sin variación mensual")
    print(f"  ✓ Tolerancia 7 en diaria:  {d7.pct_sin_mensual}% sin variación mensual")
    print(f"  ✓ {d7.ref_desplazada} referencias retrocedieron al día hábil anterior")
    print(f"  ✓ La serie mensual no cambió: {m0.pct_sin_mensual}% sin variación")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
