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
from core.catalog import cargar_catalogo  # noqa: E402

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
               "frecuencia": s.frecuencia, "fuente": s.fuente}
        for s in series if s.verificado
    }


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
