"""Construcción de la capa gold.

Corre con sa-transform: lee bronze, escribe gold. Ni ingesta ni sirve.

El SQL vive en sql/*.sql, no incrustado en Python. Dos razones:
  - se puede abrir en cualquier editor de SQL y correrlo a mano
  - cuando llegue dbt, migrar es copiar el archivo a models/ y cambiar la
    referencia a la tabla por un ref(). No hay que desenterrarlo del código.
"""

from __future__ import annotations

from pathlib import Path

from google.cloud import bigquery

RAIZ = Path(__file__).resolve().parent.parent
SQL = RAIZ / "sql"

DATASET_GOLD = "nacelab_gold"


def leer_sql(nombre: str, proyecto: str) -> str:
    """Carga un .sql y sustituye el marcador de proyecto."""
    texto = (SQL / nombre).read_text(encoding="utf-8")
    return texto.replace("{proyecto}", proyecto)


def construir(cliente: bigquery.Client, modelo: str) -> dict:
    """Materializa un modelo de gold como tabla.

    CREATE OR REPLACE reconstruye la tabla entera en cada corrida. Con miles
    de filas es instantáneo y elimina toda una clase de bugs: no hay estado
    parcial que conciliar. Cuando gold crezca lo suficiente para que esto
    duela, será momento de dbt con materialización incremental — no antes.
    """
    destino = f"{cliente.project}.{DATASET_GOLD}.{modelo}"
    consulta = leer_sql(f"{modelo}.sql", cliente.project)

    job = cliente.query(f"CREATE OR REPLACE TABLE `{destino}` AS\n{consulta}")
    job.result()

    n = list(cliente.query(f"SELECT COUNT(*) AS n FROM `{destino}`").result())[0].n
    return {"tabla": destino, "filas": n}
