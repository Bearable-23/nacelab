"""nacelab — observatorio económico de México.

Correr en local:
    .venv\\Scripts\\streamlit run app/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from app import datos, interpretacion  # noqa: E402

load_dotenv()

st.set_page_config(page_title="nacelab", page_icon="📊", layout="wide")

ES_PRECIOS = {"inpc_general", "inpc_subyacente"}


def encabezado():
    st.title("nacelab")
    st.caption(
        "Datos económicos de México del INEGI y Banxico. "
        "Todo resultado puede seguirse hasta su origen."
    )


def tarjeta(fila, meta: dict):
    """Un indicador con su interpretación, no solo su número."""
    nombre = meta.get(fila.serie_id, {}).get("nombre", fila.serie_id)

    # `sin_dato` y no `is not None`: en una columna FLOAT64 un NULL de
    # BigQuery llega como NaN, y NaN pasa la prueba de `is not None`.
    if fila.serie_id in ES_PRECIOS:
        emoji, frase = interpretacion.semaforo_inflacion(fila.var_anual)
        falta = interpretacion.sin_dato(fila.var_anual)
        valor_grande = "—" if falta else f"{fila.var_anual:.2f}%"
        etiqueta = f"{emoji} {nombre} · variación anual"
    else:
        frase = ""
        valor_grande = f"{fila.valor:,.0f}"
        etiqueta = nombre

    delta = (
        None
        if interpretacion.sin_dato(fila.var_mensual)
        else f"{fila.var_mensual:+.2f}% mensual"
    )
    st.metric(etiqueta, valor_grande, delta)
    st.caption(f"Último dato: {fila.fecha}")
    if frase:
        st.caption(frase)


def main():
    encabezado()
    meta = datos.catalogo()
    ultimos = datos.ultimos()

    st.subheader("Qué está pasando")
    columnas = st.columns(max(len(ultimos), 1))
    for col, (_, fila) in zip(columnas, ultimos.iterrows()):
        with col:
            tarjeta(fila, meta)

    st.divider()

    # ------------------------------------------------------------ detalle --
    st.subheader("Serie histórica")

    opciones = list(meta.keys())
    elegida = st.selectbox(
        "Indicador",
        opciones,
        format_func=lambda s: meta[s]["nombre"],
    )

    df = datos.serie(elegida)
    if df.empty:
        st.warning("Sin datos para esta serie.")
        return

    # El rango del slider se adapta a la historia real de cada serie. Un tope
    # fijo de 35 años dejaba fuera un siglo de censos de población.
    historia = (df["fecha"].max() - df["fecha"].min()).days // 365 + 1
    anios = st.slider("Años a mostrar", 1, max(historia, 2), min(10, historia))

    corte = pd.Timestamp(df["fecha"].max()) - pd.DateOffset(years=anios)
    vista = df[df["fecha"] >= corte.date()]

    # Una serie irregular puede quedarse con un solo punto, y una línea
    # necesita dos: el resultado seria un lienzo en blanco sin explicacion.
    # Pasa con los censos, que van cada 5 o 10 años.
    recortada_de_mas = len(vista) < 2
    if recortada_de_mas:
        vista = df

    ultimo = df.iloc[-1]

    izq, der = st.columns([3, 1])

    with izq:
        eje = "var_anual" if elegida in ES_PRECIOS else "valor"
        titulo = "Variación anual (%)" if elegida in ES_PRECIOS else "Nivel"

        grafica = (
            alt.Chart(vista)
            # point=True dibuja cada observación además de la línea. Con
            # series de baja frecuencia es la diferencia entre ver los datos
            # y ver un lienzo vacío.
            .mark_line(point=True)
            .encode(
                x=alt.X("fecha:T", title=None),
                y=alt.Y(
                    f"{eje}:Q",
                    title=titulo,
                    # En una serie de nivel, anclar el eje en cero aplasta la
                    # variación: 112 a 126 millones se ve como línea plana
                    # pegada al techo. En una de inflación el cero sí
                    # significa algo, así que ahí se conserva.
                    scale=alt.Scale(zero=elegida in ES_PRECIOS),
                ),
                tooltip=["fecha:T", alt.Tooltip(f"{eje}:Q", format=",.2f")],
            )
            .properties(height=340)
        )
        if elegida in ES_PRECIOS:
            # La meta de Banxico como referencia visual: sin ella, la gráfica
            # no dice si el nivel es alto o bajo, solo si sube o baja.
            meta_linea = (
                alt.Chart(vista)
                .mark_rule(strokeDash=[6, 4], color="gray")
                .encode(y=alt.datum(interpretacion.OBJETIVO_BANXICO))
            )
            grafica = grafica + meta_linea
        st.altair_chart(grafica, use_container_width=True)

        if recortada_de_mas:
            st.caption(
                f"Se muestra la serie completa: con {anios} año(s) quedaba "
                f"menos de una observación. Esta serie es de frecuencia "
                f"**{meta[elegida]['frecuencia']}**."
            )

    with der:
        st.markdown(f"**{meta[elegida]['nombre']}**")
        st.markdown(f"Último dato: `{ultimo.fecha}`")
        st.markdown(f"Valor: `{ultimo.valor:,.4f}`")
        if not interpretacion.sin_dato(ultimo.var_anual):
            st.markdown(f"Variación anual: `{ultimo.var_anual:.2f}%`")
            st.caption(
                interpretacion.contexto_historico(
                    ultimo.percentil_var_anual, len(df)
                )
            )
        st.caption(interpretacion.direccion(ultimo.var_mensual))
        st.markdown(
            f"Fuente: **{meta[elegida]['fuente'].upper()}** · "
            f"frecuencia {meta[elegida]['frecuencia']}"
        )

    # El botón que sostiene el cuarto pilar: reproducibilidad.
    with st.expander("Ver el código que produce este dato"):
        st.markdown(
            "El valor viene de `gold_indicador`, calculado a partir de la "
            "descarga cruda de la API. Nada de esto se calcula en la página."
        )
        st.code(
            (Path(__file__).resolve().parent.parent / "sql" / "gold_indicador.sql")
            .read_text(encoding="utf-8"),
            language="sql",
        )
        st.markdown(
            "Repositorio completo: "
            "[github.com/Bearable-23/nacelab](https://github.com/Bearable-23/nacelab)"
        )


if __name__ == "__main__":
    main()
