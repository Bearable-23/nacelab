"""nacelab — observatorio económico de México.

Correr en local:
    .venv\\Scripts\\streamlit run app/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import altair as alt  # noqa: E402
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

    if fila.serie_id in ES_PRECIOS:
        emoji, frase = interpretacion.semaforo_inflacion(fila.var_anual)
        valor_grande = f"{fila.var_anual:.2f}%" if fila.var_anual is not None else "—"
        etiqueta = f"{emoji} {nombre} · variación anual"
    else:
        frase = ""
        valor_grande = f"{fila.valor:,.0f}"
        etiqueta = nombre

    delta = f"{fila.var_mensual:+.2f}% mensual" if fila.var_mensual is not None else None
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

    anios = st.slider("Años a mostrar", 1, 35, 10)
    corte = df["fecha"].max() - __import__("pandas").DateOffset(years=anios)
    vista = df[df["fecha"] >= corte.date()] if hasattr(corte, "date") else df

    ultimo = df.iloc[-1]

    izq, der = st.columns([3, 1])

    with izq:
        eje = "var_anual" if elegida in ES_PRECIOS else "valor"
        titulo = "Variación anual (%)" if elegida in ES_PRECIOS else "Nivel"

        grafica = (
            alt.Chart(vista)
            .mark_line()
            .encode(
                x=alt.X("fecha:T", title=None),
                y=alt.Y(f"{eje}:Q", title=titulo),
                tooltip=["fecha:T", alt.Tooltip(f"{eje}:Q", format=".2f")],
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

    with der:
        st.markdown(f"**{meta[elegida]['nombre']}**")
        st.markdown(f"Último dato: `{ultimo.fecha}`")
        st.markdown(f"Valor: `{ultimo.valor:,.4f}`")
        if ultimo.var_anual is not None:
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
