"""Panel de filtros de la barra lateral, sincronizado con la URL.

Por qué la URL y no solo el estado de sesión: el cuarto pilar del proyecto es
que cualquiera pueda comprobar un resultado. Si la vista que estás mirando no
tiene dirección propia, no la puedes citar ni mandar por correo — puedes decir
"entra y filtra por Banxico desde 2016", que es exactamente el tipo de
instrucción que el proyecto existe para evitar.

Esto ya estaba anticipado en datos.serie(): el serie_id se pasa como parámetro
de consulta y no interpolado, precisamente porque algún día vendría de la URL
y entonces sería input de usuario. Ese día es hoy. Todo lo que entra por query
param se valida contra el catálogo antes de tocar una consulta.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import streamlit as st


@dataclass
class Filtros:
    """Qué series pasan el filtro y desde qué año se dibujan."""

    fuentes: list[str] = field(default_factory=list)
    frecuencias: list[str] = field(default_factory=list)
    desde: int | None = None

    def pasa(self, serie_id: str, meta: dict) -> bool:
        """¿Esta serie sobrevive al filtro?"""
        m = meta.get(serie_id)
        if m is None:
            return False
        if self.fuentes and m["fuente"] not in self.fuentes:
            return False
        if self.frecuencias and m["frecuencia"] not in self.frecuencias:
            return False
        return True

    def visibles(self, ids: list[str], meta: dict) -> list[str]:
        return [i for i in ids if self.pasa(i, meta)]


def _lista_desde_url(clave: str, validos: set[str]) -> list[str] | None:
    """Lee una lista separada por comas de la URL, descartando lo que no exista.

    Un valor inventado en la URL no debe romper la página ni, mucho menos,
    llegar a una consulta. Se descarta en silencio y el filtro queda como si
    no se hubiera pedido.
    """
    crudo = st.query_params.get(clave)
    if not crudo:
        return None
    elegidos = [v.strip() for v in crudo.split(",") if v.strip() in validos]
    return elegidos or None


def barra_lateral(meta: dict) -> Filtros:
    """Dibuja el panel de filtros y devuelve lo elegido.

    Las opciones salen del catálogo, no de una lista escrita a mano: cuando
    entre Banxico, su opción aparece sola.
    """
    fuentes_disp = sorted({m["fuente"] for m in meta.values()})
    frecs_disp = sorted({m["frecuencia"] for m in meta.values()})

    # Inicialización desde la URL: solo la primera vez, cuando la clave aún no
    # existe en el estado. Después manda el widget, o los dos se pelearían en
    # cada rerun.
    if "f_fuentes" not in st.session_state:
        st.session_state.f_fuentes = _lista_desde_url("fuente", set(fuentes_disp)) or []
    if "f_frecuencias" not in st.session_state:
        st.session_state.f_frecuencias = _lista_desde_url("frec", set(frecs_disp)) or []
    if "f_desde" not in st.session_state:
        crudo = st.query_params.get("desde")
        st.session_state.f_desde = int(crudo) if (crudo or "").isdigit() else 0

    with st.sidebar:
        st.subheader("Filtros")

        st.multiselect(
            "Fuente",
            fuentes_disp,
            key="f_fuentes",
            format_func=str.upper,
            placeholder="Todas",
        )
        st.multiselect(
            "Frecuencia",
            frecs_disp,
            key="f_frecuencias",
            placeholder="Todas",
        )
        st.number_input(
            "Desde el año",
            min_value=0,
            max_value=2100,
            step=1,
            key="f_desde",
            help="0 = sin recorte. Afecta a las gráficas, no a las tarjetas: "
                 "una tarjeta siempre muestra el último dato disponible.",
        )

        if st.button("Limpiar filtros", use_container_width=True):
            st.session_state.f_fuentes = []
            st.session_state.f_frecuencias = []
            st.session_state.f_desde = 0
            st.rerun()

    f = Filtros(
        fuentes=list(st.session_state.f_fuentes),
        frecuencias=list(st.session_state.f_frecuencias),
        desde=st.session_state.f_desde or None,
    )
    _escribir_url(f)
    return f


def _escribir_url(f: Filtros) -> None:
    """Refleja el filtro activo en la barra de direcciones.

    Solo se escriben las claves con valor: una URL con `?fuente=&frec=&desde=`
    es ruido y además sugiere que hay filtros puestos cuando no los hay.
    """
    for clave, valor in (
        ("fuente", ",".join(f.fuentes)),
        ("frec", ",".join(f.frecuencias)),
        ("desde", str(f.desde) if f.desde else ""),
    ):
        if valor:
            st.query_params[clave] = valor
        elif clave in st.query_params:
            del st.query_params[clave]
