"""nacelab — observatorio económico de México.

Este archivo solo arma el menú. Las páginas viven en app/vistas.py y las
piezas de dibujo en app/componentes.py.

El menú se GENERA del catálogo: hay una página por tema con series
verificadas. Cuando `fix` e `igae` pasen a verificadas, sus secciones
aparecen en la barra lateral sin tocar este archivo. Un tema sin series no
produce una página vacía; simplemente no existe.

Correr en local:
    .venv\\Scripts\\streamlit run app/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from app import datos, filtros, vistas  # noqa: E402

load_dotenv()

# La marca. Los PNG viven en app/marca/ y no en una carpeta de recursos aparte
# por una razón práctica: el Dockerfile copia carpetas una por una, y `app/` ya
# está en la lista. Una carpeta nueva en la raíz habría que acordarse de
# agregarla, y el sitio se desplegaría sin marca sin que nada fallara — que es
# exactamente lo que ya pasó una vez con .streamlit/config.toml.
MARCA = Path(__file__).resolve().parent / "marca"
# El horizontal, no el vertical. st.logo mete el logotipo en la cabecera de la
# barra lateral, que tiene altura fija: el lockup original, casi cuadrado, se
# encogía a 31x32 px y la palabra NACELAB quedaba ilegible. Comprobado en el
# navegador antes de cambiarlo.
LOGO = MARCA / "nacelab_logo_h.png"
ICONO = MARCA / "nacelab_icono.png"

st.set_page_config(
    page_title="nacelab",
    # El símbolo solo, sin la palabra: a 16 píxeles el texto de un logotipo no
    # se lee y solo ensucia la forma.
    page_icon=str(ICONO),
    layout="wide",
)


def main():
    # Va antes que cualquier otra cosa de la barra lateral: st.logo se ancla
    # arriba del todo, encima del menú que genera st.navigation.
    #
    # `icon_image` es lo que se ve cuando la barra lateral está plegada, donde
    # no cabe el lockup completo. Sin él, Streamlit encoge el logotipo entero
    # hasta que la palabra NACELAB queda ilegible.
    st.logo(str(LOGO), icon_image=str(ICONO), size="large")

    meta = datos.catalogo()
    secciones = datos.secciones()

    # serie_id -> tema, para que el detalle sepa si mostrar nivel o variación
    # sin volver a recorrer el catálogo.
    tema_de = {sid: tema for tema, ids in secciones for sid in ids}

    # Los filtros se dibujan ANTES de la navegación para que su valor ya esté
    # disponible cuando la página elegida se ejecute. La barra lateral es
    # compartida: el filtro sobrevive al cambio de página, que es justamente
    # lo que uno espera de un panel de filtros.
    f = filtros.barra_lateral(meta)

    paginas = [
        st.Page(
            lambda: vistas.resumen(meta, secciones, f),
            title="Resumen",
            icon=":material/dashboard:",
            url_path="resumen",
            default=True,
        )
    ]

    for tema, ids in secciones:
        # `tema=tema, ids=ids` como argumentos por defecto y no capturados por
        # cierre: sin eso, las tres lambdas compartirían la última vuelta del
        # bucle y todas las páginas mostrarían el mismo tema. Es el error
        # clásico de generar callbacks dentro de un for.
        paginas.append(
            st.Page(
                lambda tema=tema, ids=ids: vistas.pagina_tema(
                    tema, ids, meta, tema_de, f
                ),
                title=tema["nombre"],
                icon=":material/insights:",
                url_path=tema["id"],
            )
        )

    paginas.append(
        st.Page(
            lambda: vistas.comparar(meta, f),
            title="Comparar",
            icon=":material/compare_arrows:",
            url_path="comparar",
        )
    )
    paginas.append(
        st.Page(
            vistas.metodologia,
            title="Metodología",
            icon=":material/science:",
            url_path="metodologia",
        )
    )

    st.navigation(paginas, position="sidebar").run()


if __name__ == "__main__":
    main()
