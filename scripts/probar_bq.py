"""Comprueba la conexión a BigQuery Y que los permisos son los que creemos.

No basta con conectarse. Lo que de verdad hay que probar es lo que cada
cuenta NO puede hacer: un diseño de permisos que solo se verifica por el
lado positivo no está verificado.

Uso:
    python scripts/probar_bq.py
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os  # noqa: E402

from dotenv import load_dotenv  # noqa: E402
from google.api_core import exceptions  # noqa: E402

from core import bq  # noqa: E402

load_dotenv()
PROYECTO = os.environ["GCP_PROJECT"]


def linea(t=""):
    print(f"\n{'─' * 68}")
    if t:
        print(t)
        print("─" * 68)


def probar_escritura(sa: str, dataset: str, debe_poder: bool) -> bool:
    """Intenta crear y borrar una tabla temporal. Devuelve si el test pasó."""
    os.environ["IMPERSONATE_SA"] = f"{sa}@{PROYECTO}.iam.gserviceaccount.com"
    cliente = bq.cliente(PROYECTO)
    tabla = f"{PROYECTO}.{dataset}._prueba_permisos"

    try:
        cliente.query(f"CREATE OR REPLACE TABLE `{tabla}` AS SELECT 1 AS x").result()
        cliente.query(f"DROP TABLE `{tabla}`").result()
        pudo = True
        detalle = ""
    except exceptions.Forbidden as e:
        pudo = False
        detalle = str(e).split("\n")[0][:70]
    except Exception as e:  # noqa: BLE001
        pudo = False
        detalle = f"{type(e).__name__}: {str(e).splitlines()[0][:60]}"

    ok = pudo == debe_poder
    esperado = "debe poder" if debe_poder else "NO debe poder"
    simbolo = "✓" if ok else "✗"
    print(f"  {simbolo}  {sa:<14} escribir en {dataset:<16} {esperado:<14} "
          f"{'pudo' if pudo else 'bloqueado'}")
    if detalle and not debe_poder and ok:
        print(f"       └─ {detalle}")
    return ok


def main() -> int:
    linea("1. Conexión e identidad")
    print(f"  Proyecto: {PROYECTO}")
    print(f"  Identidad: {bq.identidad()}")

    cliente = bq.cliente(PROYECTO)
    datasets = sorted(d.dataset_id for d in cliente.list_datasets())
    print(f"  Datasets visibles: {', '.join(datasets)}")

    linea("2. Frontera de permisos")
    print("  Lo importante no es que funcione, sino que lo prohibido falle.\n")

    original = os.environ.get("IMPERSONATE_SA", "")
    casos = [
        ("sa-ingest", "nacelab_bronze", True),
        ("sa-ingest", "nacelab_gold", False),
        ("sa-app", "nacelab_gold", False),
    ]
    resultados = [probar_escritura(*c) for c in casos]
    os.environ["IMPERSONATE_SA"] = original

    linea("RESULTADO")
    if all(resultados):
        print("  Los permisos se comportan como los diseñamos.")
        return 0
    print("  ✗ Al menos un permiso NO es el que creíamos. Revisar antes de seguir.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
