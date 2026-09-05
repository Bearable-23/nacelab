"""Las páginas del sitio.

Cada función de este módulo es una página del menú. Ninguna sabe qué
indicadores existen: reciben las secciones que el catálogo declaró y dibujan
lo que haya. Por eso agregar una serie —o un tema entero— no toca este
archivo.
"""

from __future__ import annotations

from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from app import componentes, datos, interpretacion
from app.filtros import Filtros

RAIZ = Path(__file__).resolve().parent.parent


def _encabezado(titulo: str, descripcion: str = ""):
    st.title(titulo)
    if descripcion:
        st.caption(descripcion)


def _recortar(df: pd.DataFrame, filtros: Filtros) -> tuple[pd.DataFrame, bool]:
    """Aplica el filtro de año y avisa si hubo que deshacerlo.

    Una serie irregular puede quedarse con un solo punto, y una línea necesita
    dos: el resultado sería un lienzo en blanco sin explicación. Pasa con los
    censos, que van cada 5 o 10 años. Cuando el recorte deja menos de dos
    observaciones se muestra la serie completa y se dice por qué.
    """
    if not filtros.desde:
        return df, False
    vista = df[pd.to_datetime(df["fecha"]).dt.year >= filtros.desde]
    if len(vista) < 2:
        return df, True
    return vista, False


# --------------------------------------------------------------------------- #
# Detalle de una serie (se reutiliza en cada página de tema)
# --------------------------------------------------------------------------- #

def _detalle(ids: list[str], meta: dict, tema_de: dict, filtros: Filtros, clave: str):
    """Selector de serie + gráfica con ejes + ficha de texto."""
    if not ids:
        return

    st.subheader("Serie histórica")
    elegida = st.selectbox(
        "Indicador",
        ids,
        format_func=lambda s: meta[s]["nombre"],
        key=f"sel_{clave}",
    )

    df = datos.serie(elegida)
    if df.empty:
        st.warning("Sin datos para esta serie.")
        return

    vista, recortada_de_mas = _recortar(df, filtros)
    izq, der = st.columns([3, 1])

    with izq:
        with st.container(border=True):
            st.altair_chart(
                componentes.grafica_serie(vista, tema_de[elegida]),
                use_container_width=True,
            )
            if recortada_de_mas:
                st.caption(
                    f"Se muestra la serie completa: desde {filtros.desde} quedaba "
                    f"menos de una observación. Esta serie es de frecuencia "
                    f"**{meta[elegida]['frecuencia']}**."
                )

    with der:
        componentes.panel_serie(df, meta[elegida])


# --------------------------------------------------------------------------- #
# Páginas
# --------------------------------------------------------------------------- #

def resumen(meta: dict, secciones: list, filtros: Filtros):
    """Portada: todas las secciones, solo tarjetas."""
    _encabezado(
        "nacelab",
        "Datos económicos de México del INEGI y Banxico. "
        "Todo resultado puede seguirse hasta su origen.",
    )

    ultimos = datos.ultimos()
    chispas = datos.chispas()

    dibujadas = 0
    for tema, ids in secciones:
        visibles = filtros.visibles(ids, meta)
        if not visibles:
            continue
        dibujadas += 1
        st.subheader(tema["nombre"])
        if tema["descripcion"]:
            st.caption(tema["descripcion"])
        componentes.rejilla(visibles, meta, tema, ultimos, chispas)

    if dibujadas == 0:
        st.info(
            "Ningún indicador pasa los filtros activos. "
            "Usa **Limpiar filtros** en la barra lateral."
        )


def pagina_tema(tema: dict, ids: list[str], meta: dict, tema_de: dict,
                filtros: Filtros):
    """Una sección con sus tarjetas y el detalle de sus series."""
    _encabezado(tema["nombre"], tema["descripcion"])

    visibles = filtros.visibles(ids, meta)
    if not visibles:
        st.info(
            "Ningún indicador de esta sección pasa los filtros activos. "
            "Usa **Limpiar filtros** en la barra lateral."
        )
        return

    componentes.rejilla(visibles, meta, tema, datos.ultimos(), datos.chispas())
    st.divider()
    _detalle(visibles, meta, tema_de, filtros, clave=tema["id"])


def comparar(meta: dict, filtros: Filtros):
    """Varias series en una misma gráfica.

    El problema real de comparar indicadores económicos son las unidades: un
    índice de precios, un tipo de cambio y un conteo de personas no caben en
    un mismo eje. Las dos salidas honestas son mirar la VARIACIÓN (todos en
    por ciento) o reindexar al inicio del tramo (todos arrancan en 100). Las
    dos están aquí y ninguna es la de por defecto por accidente: la variación
    anual es la que no depende de dónde empieza la ventana.
    """
    _encabezado(
        "Comparar",
        "Varias series en el mismo eje. Elige cómo hacerlas comparables.",
    )

    disponibles = filtros.visibles(list(meta.keys()), meta)
    if len(disponibles) < 2:
        st.info(
            "Hacen falta al menos dos indicadores que pasen los filtros. "
            "Hoy el catálogo tiene pocos: esta página crece con las fuentes."
        )
        return

    elegidas = st.multiselect(
        "Indicadores",
        disponibles,
        default=disponibles[:2],
        format_func=lambda s: meta[s]["nombre"],
    )
    modo = st.radio(
        "Cómo compararlas",
        ["Variación anual (%)", "Índice base 100 al inicio"],
        horizontal=True,
        help="La variación anual no depende de dónde empieza la ventana. "
             "El índice sí: reindexar cambia el dibujo si mueves el filtro de año.",
    )

    if len(elegidas) < 2:
        st.warning("Elige al menos dos indicadores.")
        return

    marcos = []
    for sid in elegidas:
        df = datos.serie(sid)
        if df.empty:
            continue
        vista, _ = _recortar(df, filtros)
        vista = vista.copy()
        vista["indicador"] = meta[sid]["nombre"]

        if modo.startswith("Variación"):
            vista["y"] = vista["var_anual"]
        else:
            base = vista["valor"].iloc[0]
            # Si la primera observación es 0 o nula, reindexar da infinito o
            # NaN. Se omite la serie en vez de dibujar una línea inventada.
            if interpretacion.sin_dato(base) or base == 0:
                st.caption(f"«{meta[sid]['nombre']}» se omite: no tiene base válida.")
                continue
            vista["y"] = vista["valor"] / base * 100
        marcos.append(vista[["fecha", "y", "indicador"]])

    if not marcos:
        st.warning("No hay datos comparables con los filtros actuales.")
        return

    junto = pd.concat(marcos).dropna(subset=["y"])
    if junto.empty:
        st.warning(
            "Las series elegidas no tienen valores comparables en este modo. "
            "Con variación anual, una serie irregular como los censos no "
            "produce ningún punto."
        )
        return

    # Una serie puede desaparecer del resultado sin que la gráfica esté vacía:
    # eliges dos indicadores, se dibuja uno y nadie te avisa. Es el mismo tipo
    # de hueco silencioso que el SQL de gold evita al comparar contra la fecha
    # exacta en vez de usar LAG. Si algo no se pudo dibujar, hay que decirlo.
    perdidas = [meta[s]["nombre"] for s in elegidas
                if meta[s]["nombre"] not in set(junto["indicador"])]
    if perdidas:
        st.warning(
            f"No se dibuja: **{'**, **'.join(perdidas)}**. "
            + (
                "En variación anual hace falta la observación de hace "
                "exactamente un año, y una serie irregular —como los censos, "
                "que van cada 5 o 10 años— no la tiene nunca. "
                "Prueba con «Índice base 100»."
                if modo.startswith("Variación")
                else "No tiene un valor inicial válido en el tramo elegido."
            )
        )

    grafica = (
        alt.Chart(junto)
        .mark_line(point=True)
        .encode(
            x=alt.X("fecha:T", title=None),
            y=alt.Y("y:Q", title=modo, scale=alt.Scale(zero=False)),
            color=alt.Color("indicador:N", title=None,
                            legend=alt.Legend(orient="bottom")),
            tooltip=["indicador:N", "fecha:T", alt.Tooltip("y:Q", format=",.2f")],
        )
        .properties(height=420)
    )
    with st.container(border=True):
        st.altair_chart(grafica, use_container_width=True)


def metodologia():
    """Cómo se produce cada número. El cuarto pilar, como página propia."""
    _encabezado(
        "Metodología",
        "De dónde sale cada dato y cómo comprobarlo tú mismo.",
    )

    st.subheader("El flujo")
    st.code(
        "API  →  bronze.serie_obs  →  gold.gold_indicador  →  esta página\n"
        "        MERGE idempotente    variaciones y percentil    solo lee\n"
        "        sa-ingest            sa-transform               sa-app",
        language="text",
    )
    st.caption(
        "Cada capa corre con una identidad distinta. La cuenta que sirve el "
        "sitio no puede leer los datos crudos, solo la tabla ya calculada."
    )

    st.subheader("El catálogo")
    st.caption(
        "Un id copiado de un ejemplo de documentación no es un id verificado. "
        "Ninguna serie pasa a verificada sin que un humano haya visto qué "
        "devuelve realmente la API: nombre, unidad, frecuencia y último dato."
    )
    st.dataframe(
        pd.DataFrame(datos.catalogo_completo()),
        hide_index=True,
        use_container_width=True,
    )

    st.subheader("El SQL que produce los números")
    st.code(
        (RAIZ / "sql" / "gold_indicador.sql").read_text(encoding="utf-8"),
        language="sql",
    )

    st.subheader("Fuentes")
    st.markdown(
        "- [INEGI — Banco de Indicadores]"
        "(https://www.inegi.org.mx/servicios/api_indicadores.html)\n"
        "- [Banxico — SIE API]"
        "(https://www.banxico.org.mx/SieAPIRest/service/v1/doc/series)\n"
        "- [Repositorio completo](https://github.com/Bearable-23/nacelab)"
    )
