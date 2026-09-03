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


def merge_a_bronze(cliente: bigquery.Client) -> dict[str, int]:
    """Consolida staging en bronze.serie_obs de forma idempotente.

    Una observación se identifica de forma única por (fuente, serie_id, fecha).
    Tres casos, y el tercero es el que casi nadie escribe:

        no existe            -> INSERT
        existe y cambió      -> UPDATE
        existe y es igual    -> no hacer nada

    Correrlo dos veces seguidas no debe cambiar absolutamente nada.
    """
    sql = f"""
    MERGE INTO `{ref(cliente, TABLA)}` AS t
    USING `{ref(cliente, STAGING)}` AS s

      -- La clave de conciliación. Si esto queda incompleto, el MERGE deja de
      -- encontrar la fila existente y empieza a insertar duplicados en vez de
      -- actualizar. Es exactamente el modo de falla que hay que evitar.
      ON  t.fuente   = s.fuente
      AND t.serie_id = s.serie_id
      AND t.fecha    = s.fecha

    -- Solo se toca la fila si el valor REALMENTE cambió.
    --
    -- `IS DISTINCT FROM` compara tratando los NULL como un valor más:
    --     NULL IS DISTINCT FROM 5      -> true   (cambió)
    --     NULL IS DISTINCT FROM NULL   -> false  (no cambió)
    --        5 IS DISTINCT FROM 5      -> false  (no cambió)
    --
    -- Con el `!=` de siempre, `NULL != 5` da NULL, y una condición NULL no
    -- dispara el UPDATE: un dato que pasa de nulo a tener valor se quedaría
    -- desactualizado para siempre, sin ningún error visible.
    WHEN MATCHED AND t.valor IS DISTINCT FROM s.valor THEN
      UPDATE SET
        valor       = s.valor,
        ingested_at = s.ingested_at

    WHEN NOT MATCHED THEN
      INSERT (fuente, serie_id, fecha, valor, ingested_at)
      VALUES (s.fuente, s.serie_id, s.fecha, s.valor, s.ingested_at)

    -- Deliberadamente NO se usa `WHEN NOT MATCHED BY SOURCE THEN DELETE`.
    -- Eso borraría de bronze todo lo que no venga en la carga actual: si un
    -- día quitas una serie del catálogo o la API responde a medias, perderías
    -- historia que ya no se puede recuperar. Bronze solo crece.
    """

    job = cliente.query(sql)
    job.result()
    return {"filas_afectadas": job.num_dml_affected_rows or 0}
