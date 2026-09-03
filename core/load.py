"""Carga de observaciones a BigQuery.

Flujo:
    fetch  ->  tabla staging (se reemplaza entera)  ->  MERGE a bronze

Por qué staging + MERGE y no un INSERT directo: el INEGI **revisa datos
históricos**. El IGAE de hace tres meses cambia de valor. Con INSERT
duplicas; con TRUNCATE + carga pierdes el rastro de qué cambió y cuándo.
"""

from __future__ import annotations

from datetime import datetime, timezone

from google.cloud import bigquery

from core.fetch import Observacion

DATASET = "nacelab_bronze"
TABLA = "serie_obs"
STAGING = "_staging_serie_obs"

ESQUEMA = [
    bigquery.SchemaField("fuente", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("serie_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("fecha", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("valor", "NUMERIC"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
]


def ref(cliente: bigquery.Client, tabla: str) -> str:
    return f"{cliente.project}.{DATASET}.{tabla}"


def asegurar_tabla(cliente: bigquery.Client) -> None:
    """Crea bronze.serie_obs si no existe.

    Sin particionar, a propósito: BigQuery cobra un mínimo de 10 MB por tabla
    consultada, y esta tabla va a pesar mucho menos que eso durante años. El
    particionado no ahorraría nada y una serie diaria con décadas de historia
    se acercaría al tope de 10,000 particiones.

    El clustering sí sirve y no cuesta nada.
    """
    tabla = bigquery.Table(ref(cliente, TABLA), schema=ESQUEMA)
    tabla.clustering_fields = ["serie_id", "fecha"]
    cliente.create_table(tabla, exists_ok=True)


def cargar_staging(
    cliente: bigquery.Client,
    obs: list[Observacion],
    fuente_por_serie: dict[str, str],
) -> int:
    """Sube las observaciones a la tabla staging, reemplazándola entera.

    `fuente_por_serie` mapea serie_id -> 'inegi' | 'banxico'. Viene del
    catálogo: la fuente no se adivina desde el dato, se declara.

    Staging es desechable por diseño: si un job falla a la mitad, el estado
    inconsistente queda aquí y no en bronze.
    """
    ahora = datetime.now(timezone.utc).isoformat()
    filas = [
        {
            "fuente": fuente_por_serie[o.serie_id],
            "serie_id": o.serie_id,
            "fecha": o.fecha.isoformat(),
            # NUMERIC de BigQuery admite 9 decimales. El INEGI devuelve 10 o
            # más ("146.00009259780000000000") y la carga falla con
            # "Invalid NUMERIC value". Ese exceso es ruido del sistema de
            # origen: el INPC se publica oficialmente a 3 decimales.
            # Se formatea a 9 fijos en vez de usar str(float) para evitar
            # notación científica en valores extremos.
            "valor": f"{o.valor:.9f}",
            "ingested_at": ahora,
        }
        for o in obs
    ]

    job = cliente.load_table_from_json(
        filas,
        ref(cliente, STAGING),
        job_config=bigquery.LoadJobConfig(
            schema=ESQUEMA,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    job.result()
    return len(filas)


def contar(cliente: bigquery.Client) -> int:
    """Filas en bronze. Es la métrica con la que se comprueba la idempotencia."""
    q = f"SELECT COUNT(*) AS n FROM `{ref(cliente, TABLA)}`"
    return list(cliente.query(q).result())[0].n


# --------------------------------------------------------------------------- #
#  ↓↓↓  ESTO LO ESCRIBES TÚ  ↓↓↓
# --------------------------------------------------------------------------- #

def merge_a_bronze(cliente: bigquery.Client) -> dict[str, int]:
    """Consolida staging en bronze.serie_obs de forma idempotente.

    CONTEXTO
    --------
    Origen:  `{proyecto}.nacelab_bronze._staging_serie_obs`
    Destino: `{proyecto}.nacelab_bronze.serie_obs`

    Ambas tienen exactamente las mismas columnas:
        fuente STRING, serie_id STRING, fecha DATE,
        valor NUMERIC, ingested_at TIMESTAMP

    Una observación queda identificada de forma única por:
        (fuente, serie_id, fecha)

    QUÉ TIENE QUE LOGRAR
    --------------------
    1. Si la observación NO existe en destino  -> insertarla
    2. Si existe y el valor CAMBIÓ             -> actualizar valor e ingested_at
    3. Si existe y el valor es el MISMO        -> no tocarla

    El caso 3 es el que hace que correrlo dos veces seguidas no cambie nada.
    Y no es solo eficiencia: si actualizas `ingested_at` en cada corrida,
    pierdes la señal de cuándo cambió el dato de verdad. Esa columna es tu
    única evidencia de que el INEGI revisó una cifra.

    PISTA SOBRE UN CASO QUE MUERDE
    ------------------------------
    `valor` admite NULL. En SQL, `NULL != 5` no es TRUE: es NULL, y una
    condición NULL no dispara el UPDATE. Piensa qué pasa si un dato pasa de
    NULL a tener valor, o al revés. Busca cómo comparar dos valores tratando
    los NULL como iguales entre sí.

    CÓMO PROBARLO (scripts/cargar_bronze.py lo hace solo)
    -----------------------------------------------------
    a) Córrelo dos veces  -> el conteo de filas NO debe cambiar
    b) Altera un valor a mano en bronze y vuelve a correr:
           UPDATE `...serie_obs` SET valor = 1
           WHERE serie_id = 'inpc_general' AND fecha = '2026-07-01';
       -> debe volver a 145.169, no duplicarse

    Documentación: MERGE de BigQuery en
    cloud.google.com/bigquery/docs/reference/standard-sql/dml-syntax#merge_statement
    """
    sql = f"""
    -- TODO: escribe aquí el MERGE.
    --
    -- Esqueleto:
    --   MERGE INTO `{ref(cliente, TABLA)}` AS t
    --   USING `{ref(cliente, STAGING)}` AS s
    --   ON  ...
    --   WHEN MATCHED AND ... THEN UPDATE SET ...
    --   WHEN NOT MATCHED THEN INSERT (...) VALUES (...)
    """

    if "TODO" in sql:
        raise NotImplementedError(
            "Falta escribir el MERGE en core/load.py -> merge_a_bronze()"
        )

    job = cliente.query(sql)
    job.result()
    return {"filas_afectadas": job.num_dml_affected_rows or 0}
