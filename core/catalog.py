"""Lectura del catálogo de series.

El catálogo (catalog/series.yml) es la única fuente de verdad sobre qué series
existen y de dónde vienen. Nada en el proyecto debe tener un id de serie
escrito a mano fuera de ese archivo.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CATALOGO = RAIZ / "catalog" / "series.yml"


@dataclass
class Serie:
    """Una serie del catálogo."""

    id: str
    nombre: str
    fuente: str  # 'inegi' | 'banxico'
    fuente_id: str | None  # None = todavía no sabemos el id real
    frecuencia: str
    unidad: str
    tema: str = "otros"  # id de un tema; agrupa la serie en el tablero
    # Cómo colapsar a mensual: fin_de_mes | promedio | suma. Solo tiene efecto
    # en series más finas que mensual. None = usar el default del catálogo.
    agregacion: str | None = None
    banco: str = "BISE"  # solo aplica a INEGI: BISE | BIE (BIE parece deprecado)
    verificado: bool = False
    nota: str = ""

    @property
    def lista(self) -> bool:
        """¿Se puede pedir esta serie a la API?"""
        return self.fuente_id is not None


@dataclass
class Tema:
    """Una sección del tablero.

    El tablero se dibuja recorriendo los temas en el orden del catálogo, así
    que este objeto es a la vez agrupación y layout. Poner esto aquí y no en
    la app es lo que permite que agregar un indicador sea editar YAML.
    """

    id: str
    nombre: str
    descripcion: str = ""
    variacion: bool = False  # True: mostrar variación anual en vez de nivel
    referencia: float | None = None  # línea punteada (p. ej. la meta de Banxico)


def cargar_catalogo(ruta: Path = RUTA_CATALOGO) -> tuple[list[Serie], dict]:
    """Devuelve (series, parámetros por defecto del INEGI)."""
    with open(ruta, encoding="utf-8") as f:
        crudo = yaml.safe_load(f)

    series = [Serie(**s) for s in crudo["series"]]
    defaults = crudo.get("inegi_defaults", {})
    return series, defaults


AGREGACIONES = {"fin_de_mes", "promedio", "suma"}


def cargar_agregacion_default(ruta: Path = RUTA_CATALOGO) -> str:
    """Regla de colapso a mensual para las series que no declaran una."""
    with open(ruta, encoding="utf-8") as f:
        crudo = yaml.safe_load(f)
    return crudo.get("agregacion_por_defecto", "fin_de_mes")


def agregacion_de(serie: Serie, por_defecto: str) -> str:
    """Cómo se colapsa esta serie a mensual, validando el valor.

    Un `agregacion` mal escrito en el YAML no debe convertirse en un método
    silencioso. En el SQL, un método desconocido caería en el ELSE y usaría
    fin de mes sin avisar: la regresión saldría con otros números y nadie
    sabría por qué.
    """
    valor = serie.agregacion or por_defecto
    if valor not in AGREGACIONES:
        raise ValueError(
            f"La serie '{serie.id}' declara agregacion '{valor}', que no existe. "
            f"Válidas: {', '.join(sorted(AGREGACIONES))}"
        )
    return valor


def cargar_tolerancias(ruta: Path = RUTA_CATALOGO) -> dict[str, int]:
    """Días que se puede retroceder para hallar la observación de referencia.

    Mapea frecuencia -> días. Lo que no esté declarado vale 0, es decir, fecha
    exacta: el default seguro es el estricto, no el permisivo. Una frecuencia
    nueva que nadie configuró se comporta como siempre se comportó todo.
    """
    with open(ruta, encoding="utf-8") as f:
        crudo = yaml.safe_load(f)
    return crudo.get("tolerancia_dias", {}) or {}


def cargar_temas(ruta: Path = RUTA_CATALOGO) -> list[Tema]:
    """Devuelve los temas en el orden en que aparecen en el catálogo."""
    with open(ruta, encoding="utf-8") as f:
        crudo = yaml.safe_load(f)
    return [Tema(**t) for t in crudo.get("temas", [])]


def agrupar_por_tema(
    series: list[Serie], temas: list[Tema]
) -> list[tuple[Tema, list[Serie]]]:
    """Empareja cada tema con sus series, en el orden del catálogo.

    Los temas sin series se omiten: mientras `fix` o `igae` sigan sin
    verificar, sus secciones no existen. El tablero crece solo, y nunca
    enseña un hueco esperando a que alguien lo llene.

    Una serie con un `tema` que no está declarado arriba se pierde en
    silencio, y eso es peor que un error. Por eso `sin_tema` lo reporta.
    """
    declarados = {t.id for t in temas}
    huerfanas = [s.id for s in series if s.tema not in declarados]
    if huerfanas:
        raise KeyError(
            f"Estas series tienen un tema que no existe en el catálogo: "
            f"{', '.join(huerfanas)}. Temas declarados: {', '.join(sorted(declarados))}"
        )

    return [
        (t, [s for s in series if s.tema == t.id])
        for t in temas
        if any(s.tema == t.id for s in series)
    ]


def por_fuente(series: list[Serie], fuente: str) -> list[Serie]:
    return [s for s in series if s.fuente == fuente]


def buscar(series: list[Serie], id_serie: str) -> Serie:
    """Busca por id. Lanza error si no existe: los ids son allowlist, no texto libre.

    Esto importa para seguridad: cuando la app reciba un id por query param,
    tiene que resolverse contra el catálogo, nunca interpolarse en un query.
    """
    for s in series:
        if s.id == id_serie:
            return s
    disponibles = ", ".join(s.id for s in series)
    raise KeyError(f"No existe la serie '{id_serie}'. Disponibles: {disponibles}")
