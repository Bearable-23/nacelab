"""Prueba las reglas de colapso a mensual contra datos sintéticos.

POR QUÉ EXISTE ESTE ARCHIVO
---------------------------
Las tres reglas —fin_de_mes, promedio, suma— solo se diferencian en series MÁS
FINAS que mensual. Con lo que hay hoy en bronze (todo mensual o menos) las
tres devuelven el mismo número, así que construir el panel y verlo salir bien
no prueba absolutamente nada de la lógica que importa.

La trampa concreta que esta prueba busca: escribir `MAX(valor)` cuando se
quería "el valor de la ÚLTIMA fecha del mes". Son cosas distintas y en una
serie que sube y luego baja dentro del mes dan resultados diferentes. Con
datos monótonos crecientes —que es como se ven casi todas las series
económicas— el error queda invisible.

Por eso la serie sintética SUBE hasta media mes y BAJA después: así el máximo
y el último dato nunca coinciden, y confundirlos se nota.

Como en probar_tolerancia.py, esto corre el SQL DE VERDAD sustituyendo las
tablas de origen, no una copia de la regla.

Uso:
    python scripts/probar_panel.py
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

# Enero 2025 completo, días hábiles. El valor sube hasta el día 15 y baja
# después, así que el MÁXIMO del mes y el ÚLTIMO dato del mes son distintos.
# Tres series con los mismos números y distinta regla de colapso.
OBS = """(
  SELECT 'prueba' AS fuente, s AS serie_id, d AS fecha,
         CAST(IF(EXTRACT(DAY FROM d) <= 15,
                 EXTRACT(DAY FROM d),
                 30 - EXTRACT(DAY FROM d)) AS NUMERIC) AS valor,
         CURRENT_TIMESTAMP() AS ingested_at
  FROM UNNEST(GENERATE_DATE_ARRAY('2025-01-01', '2025-01-31')) AS d
  CROSS JOIN UNNEST(['col_finmes', 'col_promedio', 'col_suma']) AS s
  WHERE EXTRACT(DAYOFWEEK FROM d) BETWEEN 2 AND 6
)"""

DIM = """(
  SELECT 'col_finmes' AS serie_id, 'diaria' AS frecuencia, 'fin_de_mes' AS agregacion
  UNION ALL SELECT 'col_promedio', 'diaria', 'promedio'
  UNION ALL SELECT 'col_suma',     'diaria', 'suma'
)"""


def linea(t=""):
    print(f"\n{'─' * 70}")
    if t:
        print(t)
        print("─" * 70)


def sql_con_datos_falsos() -> str:
    texto = (RAIZ / "sql" / "gold_panel_mensual.sql").read_text(encoding="utf-8")
    for marcador, reemplazo in ((TABLA_OBS, OBS), (TABLA_DIM, DIM)):
        if marcador not in texto:
            raise SystemExit(
                f"No encontré {marcador} en gold_panel_mensual.sql.\n"
                f"El SQL cambió de forma y esta prueba ya no lo está probando: "
                f"actualízala antes de confiar en su resultado."
            )
        texto = texto.replace(marcador, reemplazo)
    return texto


def main() -> int:
    cliente = bq.cliente(os.environ["GCP_PROJECT"], sa="sa-transform")

    # Valores esperados, calculados aparte del SQL para que la prueba no
    # repita la misma lógica que pretende verificar.
    dias = []
    for d in range(1, 32):
        fecha_dow = (d + 2) % 7  # 2025-01-01 fue miércoles
        if fecha_dow in (0, 1):  # sábado y domingo
            continue
        dias.append(d if d <= 15 else 30 - d)
    esperado = {
        "col_finmes": float(dias[-1]),
        "col_promedio": sum(dias) / len(dias),
        "col_suma": float(sum(dias)),
    }
    n_esperado = len(dias)

    filas = list(cliente.query(f"""
        WITH panel AS ({sql_con_datos_falsos()})
        SELECT serie_id, metodo, valor, n_obs, primera_obs, ultima_obs
        FROM panel ORDER BY serie_id
    """).result())

    linea("Colapso de una serie hábil-diaria (enero 2025)")
    print(f"  La serie sube al día 15 y baja después. Máximo del mes = "
          f"{max(dias)}, último dato = {dias[-1]}.")
    print(f"  Si el SQL usara MAX(valor), fin_de_mes daría {max(dias)}.\n")
    print(f"  {'serie':<15}{'metodo':<12}{'obtenido':>11}{'esperado':>11}{'n_obs':>7}")

    fallos = []
    for f in filas:
        esp = esperado[f.serie_id]
        ok = abs(f.valor - esp) < 1e-9
        print(f"  {f.serie_id:<15}{f.metodo:<12}{f.valor:>11.4f}{esp:>11.4f}"
              f"{f.n_obs:>7}{'' if ok else '   <-- MAL'}")
        if not ok:
            fallos.append(f"{f.serie_id}: se obtuvo {f.valor}, se esperaba {esp}")
        if f.n_obs != n_esperado:
            fallos.append(
                f"{f.serie_id}: n_obs = {f.n_obs}, se esperaban {n_esperado} "
                f"días hábiles"
            )

    if len(filas) != 3:
        fallos.append(f"Se esperaban 3 series en el panel, llegaron {len(filas)}")

    linea("VEREDICTO")
    if fallos:
        for f in fallos:
            print(f"  ✗ {f}")
        return 1

    print(f"  ✓ fin_de_mes toma el ÚLTIMO dato ({esperado['col_finmes']:.0f}), "
          f"no el máximo ({max(dias)})")
    print(f"  ✓ promedio = {esperado['col_promedio']:.4f}")
    print(f"  ✓ suma = {esperado['col_suma']:.0f}")
    print(f"  ✓ n_obs = {n_esperado} en las tres, los días hábiles de enero")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
