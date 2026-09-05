"""Materializa la capa gold a partir de bronze.

Corre con sa-transform y comprueba al final que sa-app puede leer el
resultado — que es el único permiso que de verdad importa para el sitio.

Uso:
    python scripts/construir_gold.py
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

from core import bq, transform  # noqa: E402

load_dotenv()
PROYECTO = os.environ["GCP_PROJECT"]


def linea(t=""):
    print(f"\n{'─' * 68}")
    if t:
        print(t)
        print("─" * 68)


def main() -> int:
    linea("1. Construyendo gold")
    cliente = bq.cliente(PROYECTO, sa="sa-transform")
    print(f"  Identidad: {bq.identidad()}")

    for modelo in ("gold_indicador", "gold_panel_mensual"):
        r = transform.construir(cliente, modelo)
        print(f"  {r['tabla']:<50} {r['filas']:>6} filas")

    linea("2. Muestra: INPC general, últimos 6 meses")
    q = f"""
        SELECT fecha, valor,
               ROUND(var_mensual, 2) AS var_mensual,
               ROUND(var_anual, 2)   AS var_anual,
               ROUND(percentil_var_anual, 3) AS percentil
        FROM `{PROYECTO}.nacelab_gold.gold_indicador`
        WHERE serie_id = 'inpc_general'
        ORDER BY fecha DESC
        LIMIT 6
    """
    print(f"  {'fecha':<12}{'valor':>10}{'var_mes':>10}{'var_anual':>12}{'pctil':>9}")
    for f in cliente.query(q).result():
        mes = f"{f.var_mensual:.2f}" if f.var_mensual is not None else "—"
        anual = f"{f.var_anual:.2f}" if f.var_anual is not None else "—"
        pct = f"{f.percentil:.3f}" if f.percentil is not None else "—"
        print(f"  {str(f.fecha):<12}{f.valor:>10}{mes:>10}{anual:>12}{pct:>9}")

    linea("3. ¿Puede sa-app leer gold?")
    # Esta es la prueba que importa: es la cuenta con la que va a correr el
    # sitio publico. Si falla aqui, falla en produccion.
    app = bq.cliente(PROYECTO, sa="sa-app")
    n = list(app.query(
        f"SELECT COUNT(*) AS n FROM `{PROYECTO}.nacelab_gold.gold_indicador`"
    ).result())[0].n
    print(f"  ✓ sa-app lee {n} filas de gold")

    print("\n  ¿Y bronze? (debe estar bloqueado)")
    try:
        app.query(
            f"SELECT COUNT(*) FROM `{PROYECTO}.nacelab_bronze.serie_obs`"
        ).result()
        print("  ✗ sa-app PUEDE leer bronze. No deberia.")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"  ✓ bloqueado: {str(e).splitlines()[0][:64]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
