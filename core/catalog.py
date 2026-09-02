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
    banco: str = "BISE"  # solo aplica a INEGI: BISE | BIE (BIE parece deprecado)
    verificado: bool = False
    nota: str = ""

    @property
    def lista(self) -> bool:
        """¿Se puede pedir esta serie a la API?"""
        return self.fuente_id is not None


def cargar_catalogo(ruta: Path = RUTA_CATALOGO) -> tuple[list[Serie], dict]:
    """Devuelve (series, parámetros por defecto del INEGI)."""
    with open(ruta, encoding="utf-8") as f:
        crudo = yaml.safe_load(f)

    series = [Serie(**s) for s in crudo["series"]]
    defaults = crudo.get("inegi_defaults", {})
    return series, defaults


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
