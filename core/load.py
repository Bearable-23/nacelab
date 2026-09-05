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
DIM = "serie_dim"
HISTORIAL = "serie_obs_historial"

ESQUEMA = [
    bigquery.SchemaField("fuente", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("serie_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("fecha", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("valor", "NUMERIC"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
]

# El catálogo, proyectado a BigQuery.
#
# Existe porque gold necesita saber la frecuencia de cada serie para decidir
# cómo buscar la observación de referencia, y `serie_obs` no la tiene. La
# alternativa era inferirla de los datos, pero este proyecto declara y no
# adivina — y la otra alternativa, inyectar la lista en el SQL desde Python,
# rompía que `sql/gold_indicador.sql` se pueda abrir y correr a mano.
ESQUEMA_DIM = [
    bigquery.SchemaField("serie_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("frecuencia", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("tolerancia_dias", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("agregacion", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("fuente", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("unidad", "STRING"),
    bigquery.SchemaField("tema", "STRING"),
    bigquery.SchemaField("sincronizado_at", "TIMESTAMP", mode="REQUIRED"),
]


# Las revisiones que el MERGE va a pisar.
#
# El INEGI revisa datos históricos: el IGAE de hace tres meses cambia de valor.
# El MERGE hace `UPDATE SET valor = s.valor` y el valor anterior desaparece.
# Para un tablero da igual —quieres la cifra vigente— pero para evaluar
# modelos es fatal: un pronóstico solo se juzga honestamente contra los datos
# que existían CUANDO se hizo, no contra la serie ya revisada. Un modelo
# evaluado con cifras revisadas hace trampa sin saberlo.
#
# Es el problema de datos en tiempo real (Croushore y Stark). Se resuelve
# guardando la versión anterior ANTES de pisarla; la historia que no se guarda
# hoy no se recupera nunca.
#
# Tabla aparte y no columnas de vigencia en serie_obs, a propósito: así
# serie_obs conserva exactamente su forma y su contrato —una fila por
# observación vigente— y nada de lo que ya lee de ella tiene que cambiar.
ESQUEMA_HISTORIAL = [
    bigquery.SchemaField("fuente", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("serie_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("fecha", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("valor_anterior", "NUMERIC"),
    bigquery.SchemaField("valor_nuevo", "NUMERIC"),
    bigquery.SchemaField("ingested_at_anterior", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("reemplazado_at", "TIMESTAMP", mode="REQUIRED"),
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

    hist = bigquery.Table(ref(cliente, HISTORIAL), schema=ESQUEMA_HISTORIAL)
    hist.clustering_fields = ["serie_id", "fecha"]
    cliente.create_table(hist, exists_ok=True)


def sincronizar_dim(cliente: bigquery.Client, series, tolerancias: dict,
                    agregaciones: dict) -> int:
    """Proyecta el catálogo a bronze.serie_dim.

    Se reemplaza ENTERA en cada corrida, al revés que `serie_obs`, que solo
    crece. No es una incoherencia: son cosas distintas. `serie_obs` guarda
    observaciones, que son hechos y no se borran. `serie_dim` es una copia del
    catálogo, y si una serie sale del YAML tiene que salir de aquí — si no,
    gold seguiría leyendo la configuración de algo que ya no existe.

    La verdad sigue estando en catalog/series.yml. Esta tabla es un reflejo,
    nunca una segunda fuente que se pueda editar por su cuenta.
    """
    ahora = datetime.now(timezone.utc).isoformat()
    filas = [
        {
            "serie_id": s.id,
            "frecuencia": s.frecuencia,
            # Frecuencia sin tolerancia declarada = 0, la regla estricta.
            "tolerancia_dias": int(tolerancias.get(s.frecuencia, 0)),
            # Ya viene validado contra la lista de métodos permitidos: un
            # valor inventado revienta en Python, no se cuela al SQL.
            "agregacion": agregaciones[s.id],
            "fuente": s.fuente,
            "unidad": s.unidad,
            "tema": s.tema,
            "sincronizado_at": ahora,
        }
        for s in series
    ]

    job = cliente.load_table_from_json(
        filas,
        ref(cliente, DIM),
        job_config=bigquery.LoadJobConfig(
            schema=ESQUEMA_DIM,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    job.result()
    return len(filas)


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
    # Transacción, y no dos consultas seguidas. Si el archivado ocurriera y el
    # MERGE fallara, el historial diría que un valor fue reemplazado cuando
    # sigue vigente: una bitácora que miente es peor que no tenerla. BEGIN /
    # COMMIT hace que las dos cosas pasen juntas o ninguna.
    sql = f"""
    BEGIN TRANSACTION;

    -- Guardar lo que el MERGE está a punto de pisar. Va ANTES del MERGE, que
    -- es el único momento en que las dos versiones coexisten: después, la
    -- anterior ya no existe en ningún lado.
    --
    -- La condición es la MISMA que dispara el UPDATE de abajo. Si alguna de
    -- las dos cambia sin la otra, el historial deja de reflejar la realidad.
    INSERT INTO `{ref(cliente, HISTORIAL)}`
      (fuente, serie_id, fecha, valor_anterior, valor_nuevo,
       ingested_at_anterior, reemplazado_at)
    SELECT
      t.fuente, t.serie_id, t.fecha,
      t.valor  AS valor_anterior,
      s.valor  AS valor_nuevo,
      t.ingested_at AS ingested_at_anterior,
      CURRENT_TIMESTAMP() AS reemplazado_at
    FROM `{ref(cliente, TABLA)}` AS t
    JOIN `{ref(cliente, STAGING)}` AS s
      ON  t.fuente   = s.fuente
      AND t.serie_id = s.serie_id
      AND t.fecha    = s.fecha
    WHERE t.valor IS DISTINCT FROM s.valor;

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
    ;

    COMMIT TRANSACTION;
    """

    # En un script multi-sentencia, num_dml_affected_rows del job padre no
    # corresponde al MERGE, así que la métrica que importa —cuántas revisiones
    # hubo— se mide contando el historial antes y después.
    #
    # El primer intento fue "filas del historial de los últimos 5 minutos", y
    # daba un falso positivo: al correr el script dos veces seguidas, la
    # segunda contaba el archivado de la primera y decía que había cambiado un
    # valor cuando no había cambiado nada. Justo el resultado que la prueba de
    # idempotencia existe para detectar, producido por el medidor y no por lo
    # medido. Un conteo antes/después no depende del reloj.
    def _n_historial() -> int:
        return list(cliente.query(
            f"SELECT COUNT(*) AS n FROM `{ref(cliente, HISTORIAL)}`"
        ).result())[0].n

    hist_antes = _n_historial()
    job = cliente.query(sql)
    job.result()

    return {
        "filas_afectadas": job.num_dml_affected_rows or 0,
        "revisiones_archivadas": _n_historial() - hist_antes,
    }
