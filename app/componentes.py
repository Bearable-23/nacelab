"""Piezas de dibujo reutilizables.

Aquí no se decide QUÉ mostrar —eso lo deciden las vistas— sino CÓMO se ve
una tarjeta, una minigráfica o una serie con ejes. Nada de este módulo
consulta BigQuery ni lee el catálogo.

Regla del proyecto: si algo calcula, va en core/. Si algo dibuja, va aquí.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from app import interpretacion

POR_FILA = 3


# --------------------------------------------------------------------------- #
# Minigráfica
# --------------------------------------------------------------------------- #

def chispa(df: pd.DataFrame, eje: str) -> alt.Chart:
    """La minigráfica de una tarjeta: forma, sin ejes ni números.

    Deliberadamente sin escalas ni etiquetas. Su trabajo es responder "¿va
    subiendo o bajando?" de un vistazo; el número exacto ya está arriba en
    grande, y la gráfica con ejes está en el detalle. Una minigráfica con
    ejes ilegibles no informa, solo hace ruido.
    """
    return (
        alt.Chart(df)
        .mark_area(line={"strokeWidth": 2}, opacity=0.25, interpolate="monotone")
        .encode(
            x=alt.X("fecha:T", axis=None),
            # zero=False: en una chispa importa el relieve del tramo visible,
            # no dónde queda respecto al origen.
            y=alt.Y(f"{eje}:Q", axis=None, scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("fecha:T", title="Fecha"),
                alt.Tooltip(f"{eje}:Q", title="Valor", format=",.2f"),
            ],
        )
        .properties(height=52)
        .configure_view(strokeWidth=0)
    )


# --------------------------------------------------------------------------- #
# Tarjeta
# --------------------------------------------------------------------------- #

def tarjeta(fila, meta: dict, tema: dict, historia: pd.DataFrame):
    """Un indicador con su interpretación, no solo su número."""
    nombre = meta.get(fila.serie_id, {}).get("nombre", fila.serie_id)
    eje = "var_anual" if tema["variacion"] else "valor"

    with st.container(border=True):
        # El semáforo necesita un objetivo declarado contra el cual comparar.
        # `referencia` lo trae del catálogo (el 3% es la meta de Banxico, no
        # un número que hayamos elegido). Sin referencia no hay semáforo:
        # inventar un umbral sería exactamente lo que el proyecto evita.
        if tema["referencia"] is not None:
            emoji, frase = interpretacion.semaforo_inflacion(fila.var_anual)
        else:
            emoji, frase = "", ""

        if interpretacion.sin_dato(getattr(fila, eje)):
            valor_grande = "—"
        elif tema["variacion"]:
            valor_grande = f"{fila.var_anual:.2f}%"
        else:
            valor_grande = f"{fila.valor:,.0f}"

        etiqueta = f"{emoji} {nombre}".strip()
        if tema["variacion"]:
            etiqueta += " · variación anual"

        # `sin_dato` y no `is not None`: en una columna FLOAT64 un NULL de
        # BigQuery llega como NaN, y NaN pasa la prueba de `is not None`.
        delta = (
            None
            if interpretacion.sin_dato(fila.var_mensual)
            else f"{fila.var_mensual:+.2f}% mensual"
        )
        st.metric(etiqueta, valor_grande, delta)

        # Una línea necesita dos puntos. Con los censos, que van cada 5 o 10
        # años, un tramo corto puede quedarse con uno solo.
        if len(historia) >= 2:
            st.altair_chart(chispa(historia, eje), use_container_width=True)

        st.caption(f"Último dato: {fila.fecha}")
        if frase:
            st.caption(frase)


def rejilla(ids: list[str], meta: dict, tema: dict, ultimos: pd.DataFrame,
            chispas: pd.DataFrame):
    """Las tarjetas de un tema, en filas de POR_FILA."""
    presentes = [i for i in ids if i in set(ultimos.serie_id)]
    for inicio in range(0, len(presentes), POR_FILA):
        grupo = presentes[inicio:inicio + POR_FILA]
        # Siempre POR_FILA columnas, aunque el grupo traiga menos: así una
        # fila incompleta deja hueco a la derecha en vez de estirar dos
        # tarjetas a media pantalla.
        columnas = st.columns(POR_FILA)
        for col, serie_id in zip(columnas, grupo):
            with col:
                fila = ultimos[ultimos.serie_id == serie_id].iloc[0]
                tarjeta(fila, meta, tema, chispas[chispas.serie_id == serie_id])


# --------------------------------------------------------------------------- #
# Serie con ejes
# --------------------------------------------------------------------------- #

def grafica_serie(vista: pd.DataFrame, tema: dict) -> alt.Chart:
    """La serie completa, con ejes y línea de referencia si el tema la declara."""
    eje = "var_anual" if tema["variacion"] else "valor"
    titulo = "Variación anual (%)" if tema["variacion"] else "Nivel"

    grafica = (
        alt.Chart(vista)
        # point=True dibuja cada observación además de la línea. Con series de
        # baja frecuencia es la diferencia entre ver los datos y ver un
        # lienzo vacío.
        .mark_line(point=True)
        .encode(
            x=alt.X("fecha:T", title=None),
            y=alt.Y(
                f"{eje}:Q",
                title=titulo,
                # En una serie de nivel, anclar el eje en cero aplasta la
                # variación: 112 a 126 millones se ve como línea plana pegada
                # al techo. En una de variación el cero sí significa algo.
                scale=alt.Scale(zero=tema["variacion"]),
            ),
            tooltip=["fecha:T", alt.Tooltip(f"{eje}:Q", format=",.2f")],
        )
        .properties(height=340)
    )

    if tema["referencia"] is not None:
        # El objetivo declarado como referencia visual: sin él la gráfica no
        # dice si el nivel es alto o bajo, solo si sube o baja. El valor viene
        # del catálogo, no de la app.
        grafica = grafica + (
            alt.Chart(vista)
            .mark_rule(strokeDash=[6, 4], color="gray")
            .encode(y=alt.datum(tema["referencia"]))
        )
    return grafica


def panel_serie(df: pd.DataFrame, meta_serie: dict):
    """La ficha de texto que acompaña a la gráfica de detalle."""
    ultimo = df.iloc[-1]
    with st.container(border=True):
        st.markdown(f"**{meta_serie['nombre']}**")
        st.markdown(f"Último dato: `{ultimo.fecha}`")
        st.markdown(f"Valor: `{ultimo.valor:,.4f}`")
        if not interpretacion.sin_dato(ultimo.var_anual):
            st.markdown(f"Variación anual: `{ultimo.var_anual:.2f}%`")
            st.caption(
                interpretacion.contexto_historico(ultimo.percentil_var_anual, len(df))
            )
        st.caption(interpretacion.direccion(ultimo.var_mensual))
        st.markdown(
            f"Fuente: **{meta_serie['fuente'].upper()}** · "
            f"frecuencia {meta_serie['frecuencia']}"
        )
