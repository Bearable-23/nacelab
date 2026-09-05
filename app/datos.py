"""Acceso a datos de la app.

La app NO calcula: lee gold, que el job ya dejó listo. Aquí solo hay
consultas y caché.

El caché importa más de lo que parece: sin él, cada interacción del usuario
—cambiar de indicador, mover un filtro— dispara una consulta a BigQuery.
Con `ttl=3600` los datos se refrescan una vez por hora, que es de sobra para
series mensuales que se publican una vez al mes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import bq  # noqa: E402
from core.catalog import agrupar_por_tema, cargar_catalogo, cargar_temas  # noqa: E402

TABLA_GOLD = "nacelab_gold.gold_indicador"


def proyecto() -> str:
    return os.environ.get("GCP_PROJECT", "nacelab-prod")


@st.cache_resource
def _cliente():
    """Un solo cliente por proceso. cache_resource, no cache_data:
    un cliente no se serializa ni se copia."""
    return bq.cliente(proyecto(), sa="sa-app")


@st.cache_data(ttl=3600)
def catalogo() -> dict[str, dict]:
    """Metadatos de las series. La fuente de verdad es catalog/series.yml,
    no BigQuery: los nombres y unidades viven junto al código."""
    series, _ = cargar_catalogo()
    return {
        s.id: {"nombre": s.nombre, "unidad": s.unidad,
               "frecuencia": s.frecuencia, "fuente": s.fuente, "tema": s.tema}
        for s in series if s.verificado
    }


@st.cache_data(ttl=3600)
def catalogo_completo() -> list[dict]:
    """TODAS las series, verificadas o no, para la página de metodología.

    Enseñar las no verificadas es parte del método, no un descuido: el sitio
    declara qué sabe y qué no. Una serie con `verificado: false` es una que
    todavía no ha pasado por ojos humanos, y eso el visitante debe poder verlo.
    """
    series, _ = cargar_catalogo()
    return [
        {
            "id": s.id,
            "nombre": s.nombre,
            "tema": s.tema,
            "fuente": s.fuente,
            "frecuencia": s.frecuencia,
            "unidad": s.unidad,
            "verificado": "sí" if s.verificado else "no",
        }
        for s in series
    ]


@st.cache_data(ttl=3600)
def secciones() -> list[tuple[dict, list[str]]]:
    """Las secciones del tablero: (tema, ids de sus series verificadas).

    Devuelve dicts y listas de ids, no dataclasses, porque el caché de
    Streamlit serializa el resultado y los objetos del catálogo no aportan
    nada del otro lado.
    """
    series, _ = cargar_catalogo()
    verificadas = [s for s in series if s.verificado]
    return [
        (
            {"id": t.id, "nombre": t.nombre, "descripcion": t.descripcion,
             "variacion": t.variacion, "referencia": t.referencia},
            [s.id for s in ss],
        )
        for t, ss in agrupar_por_tema(verificadas, cargar_temas())
    ]


@st.cache_data(ttl=3600)
def serie(serie_id: str) -> pd.DataFrame:
    """Historia completa de una serie.

    El serie_id se pasa como PARÁMETRO, no interpolado en el SQL. Aunque hoy
    venga de un selectbox, mañana puede venir de un query param de la URL, y
    entonces es input de usuario. Parametrizar siempre sale más barato que
    acordarse de parametrizar cuando toque.
    """
    from google.cloud import bigquery

    sql = f"""
        SELECT fecha, valor, var_mensual, var_anual, percentil_var_anual
        FROM `{proyecto()}.{TABLA_GOLD}`
        WHERE serie_id = @serie_id
        ORDER BY fecha
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("serie_id", "STRING", serie_id)]
    )
    return _cliente().query(sql, job_config=cfg).to_dataframe()


@st.cache_data(ttl=3600)
def ultimos() -> pd.DataFrame:
    """Última observación de cada serie, para el tablero de portada."""
    sql = f"""
        SELECT serie_id, fecha, valor, var_mensual, var_anual, percentil_var_anual
        FROM `{proyecto()}.{TABLA_GOLD}`
        QUALIFY ROW_NUMBER() OVER (PARTITION BY serie_id ORDER BY fecha DESC) = 1
    """
    return _cliente().query(sql).to_dataframe()


@st.cache_data(ttl=3600)
def chispas(n: int = 36) -> pd.DataFrame:
    """Últimas `n` observaciones de CADA serie, para las minigráficas.

    Una sola consulta para todas las tarjetas, no una por tarjeta. Con dos
    series la diferencia no se nota; con quince serían quince viajes a
    BigQuery en cada carga de página, y cada uno cobra el mínimo de 10 MB.

    QUALIFY filtra sobre el resultado de la función de ventana, que es lo que
    permite hacer "las últimas n de cada grupo" sin subconsulta.
    """
    from google.cloud import bigquery

    sql = f"""
        SELECT serie_id, fecha, valor, var_anual
        FROM `{proyecto()}.{TABLA_GOLD}`
        QUALIFY ROW_NUMBER() OVER (PARTITION BY serie_id ORDER BY fecha DESC) <= @n
        ORDER BY serie_id, fecha
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("n", "INT64", n)]
    )
    return _cliente().query(sql, job_config=cfg).to_dataframe()
