"""Descarga las series verificadas y las consolida en bronze.serie_obs.

Este script es la prueba de que el MERGE es idempotente. Al final imprime
el conteo antes y después: si corres dos veces seguidas y el número cambia,
el MERGE está mal.

Uso:
    python scripts/cargar_bronze.py
    python scripts/cargar_bronze.py --simular-revision   # ver nota abajo
"""

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

from core import bq, fetch, load  # noqa: E402
from core.catalog import cargar_catalogo  # noqa: E402

load_dotenv()


def linea(t=""):
    print(f"\n{'─' * 68}")
    if t:
        print(t)
        print("─" * 68)


def descargar(series, defaults) -> list[fetch.Observacion]:
    """Trae las observaciones de las series verificadas del catálogo."""
    obs: list[fetch.Observacion] = []

    banxico = [s for s in series if s.fuente == "banxico" and s.lista]
    if banxico and fetch.hay_token("BANXICO_TOKEN"):
        payload = fetch.banxico_crudo([s.fuente_id for s in banxico])
        mapeo = {s.fuente_id: s.id for s in banxico}
        nuevas = fetch.banxico_parsear(payload, mapeo)
        print(f"  banxico  {len(nuevas):>5} obs de {len(banxico)} series")
        obs += nuevas
    elif banxico:
        print("  banxico  saltado (sin BANXICO_TOKEN)")

    for s in [x for x in series if x.fuente == "inegi" and x.lista]:
        payload, _ = fetch.inegi_crudo(
            indicador=s.fuente_id,
            idioma=defaults.get("idioma", "es"),
            entidad=defaults.get("entidad", "00"),
            dato_reciente=defaults.get("dato_reciente", False),
            banco=s.banco,
        )
        nuevas = fetch.inegi_parsear(payload, s.id)
        print(f"  inegi    {len(nuevas):>5} obs · {s.id}")
        obs += nuevas

    return obs


def simular_revision(cliente) -> None:
    """Altera un valor en bronze para imitar una revisión del INEGI.

    Es la prueba que de verdad importa: el MERGE tiene que CORREGIR el valor,
    no insertar una fila duplicada.
    """
    tabla = load.ref(cliente, load.TABLA)
    sql = f"""
        UPDATE `{tabla}`
        SET valor = 1
        WHERE serie_id = 'inpc_general' AND fecha = '2026-07-01'
    """
    job = cliente.query(sql)
    job.result()
    print(f"  Valor alterado a 1 en {job.num_dml_affected_rows} fila(s).")
    print("  Vuelve a correr el script sin la bandera: debe regresar a 145.169")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--simular-revision", action="store_true",
                   help="altera un valor para probar que el MERGE lo corrige")
    args = p.parse_args()

    series, defaults = cargar_catalogo()
    verificadas = [s for s in series if s.verificado and s.lista]

    linea("1. Catálogo")
    print(f"  {len(verificadas)} series verificadas: "
          f"{', '.join(s.id for s in verificadas)}")
    omitidas = [s.id for s in series if not s.verificado]
    if omitidas:
        print(f"  Omitidas por no estar verificadas: {', '.join(omitidas)}")

    cliente = bq.cliente(os.environ["GCP_PROJECT"])
    print(f"  Identidad: {bq.identidad()}")

    load.asegurar_tabla(cliente)

    if args.simular_revision:
        linea("Simulando una revisión del INEGI")
        simular_revision(cliente)
        return 0

    linea("2. Descarga")
    obs = descargar(verificadas, defaults)
    print(f"  TOTAL: {len(obs)} observaciones")

    linea("3. Staging")
    fuente_por_serie = {s.id: s.fuente for s in verificadas}
    n = load.cargar_staging(cliente, obs, fuente_por_serie)
    print(f"  {n} filas en staging")

    linea("4. MERGE a bronze")
    antes = load.contar(cliente)
    resultado = load.merge_a_bronze(cliente)
    despues = load.contar(cliente)

    print(f"  filas antes:     {antes:>6}")
    print(f"  filas afectadas: {resultado['filas_afectadas']:>6}")
    print(f"  filas después:   {despues:>6}")
    print(f"  filas nuevas:    {despues - antes:>6}")

    linea("IDEMPOTENCIA")
    if antes == 0:
        print("  Primera carga. Corre el script otra vez:")
        print("  el conteo NO debe cambiar y las filas afectadas deben ser 0.")
    elif despues == antes and resultado["filas_afectadas"] == 0:
        print("  ✓ Nada cambió. El MERGE es idempotente.")
    elif despues == antes:
        print(f"  ✓ Sin filas nuevas, pero se actualizaron "
              f"{resultado['filas_afectadas']}.")
        print("    Correcto si el INEGI revisó datos; sospechoso si no.")
    else:
        print(f"  ✗ Aparecieron {despues - antes} filas de más.")
        print("    El MERGE está insertando en vez de actualizar: revisa el ON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
