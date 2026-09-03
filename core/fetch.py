"""Descarga de series desde las APIs del INEGI y de Banxico.

Este módulo solo trae datos crudos y los normaliza a un formato largo común:
    (serie_id, fecha, valor)

No escribe en ninguna base. Eso vive en core/load.py.

Nota de seguridad: el token de Banxico va en el header `Bmx-Token`, nunca en la
URL. Hay tokens de terceros indexados en Google por haberlos puesto como query
param — una URL con credencial acaba en logs, historiales y buscadores.
El INEGI, en cambio, sí exige el token dentro de la ruta; por eso nunca hay que
loguear la URL completa de una petición al INEGI (ver `_ocultar_token`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime

import requests

TIMEOUT = 30
BANXICO_BASE = "https://www.banxico.org.mx/SieAPIRest/service/v1"
INEGI_BASE = "https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml/INDICATOR"


@dataclass
class Observacion:
    serie_id: str
    fecha: date
    valor: float


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #

def _token(nombre: str) -> str:
    """Lee un token del entorno y falla con un mensaje útil si no está."""
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise RuntimeError(
            f"Falta la variable de entorno {nombre}.\n"
            f"  1. Copia .env.example a .env\n"
            f"  2. Pon tu token en {nombre}\n"
            f"  3. Vuelve a correr"
        )
    return valor


def hay_token(nombre: str) -> bool:
    """¿Está configurado este token? Permite saltar una fuente sin abortar todo."""
    return bool(os.environ.get(nombre, "").strip())


def _ocultar_token(url: str, token: str) -> str:
    """Para poder imprimir o loguear una URL sin filtrar la credencial."""
    return url.replace(token, "***TOKEN***")


# --------------------------------------------------------------------------- #
# Banxico — SIE
# --------------------------------------------------------------------------- #

def banxico_crudo(series_ids: list[str], oportuno: bool = False) -> dict:
    """Devuelve el JSON tal como lo entrega el SIE, sin interpretar.

    El SIE acepta varias series separadas por coma en una sola petición.
    Conviene usarlo: son menos llamadas y menos riesgo de rate limit.
    """
    token = _token("BANXICO_TOKEN")
    ids = ",".join(series_ids)
    sufijo = "/datos/oportuno" if oportuno else "/datos"
    url = f"{BANXICO_BASE}/series/{ids}{sufijo}"

    resp = requests.get(
        url,
        headers={"Bmx-Token": token, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def banxico_parsear(payload: dict, mapeo: dict[str, str]) -> list[Observacion]:
    """Convierte la respuesta del SIE a observaciones.

    `mapeo` traduce el id de Banxico (SF43718) al id del catálogo (fix).

    Formato esperado:
        {"bmx": {"series": [{"idSerie": "...", "titulo": "...",
                             "datos": [{"fecha": "dd/mm/aaaa", "dato": "17.12"}]}]}}
    """
    obs: list[Observacion] = []

    for serie in payload["bmx"]["series"]:
        serie_id = mapeo.get(serie["idSerie"], serie["idSerie"])

        # Una serie sin datos no trae la llave "datos" — no es un error.
        for punto in serie.get("datos", []):
            valor_txt = punto["dato"]

            # El SIE usa "N/E" para dato no disponible. Un hueco NO es un cero:
            # lo saltamos para que quede como nulo, no como observación falsa.
            if valor_txt in ("N/E", "", None):
                continue

            obs.append(
                Observacion(
                    serie_id=serie_id,
                    fecha=datetime.strptime(punto["fecha"], "%d/%m/%Y").date(),
                    valor=float(valor_txt.replace(",", "")),
                )
            )

    return obs


# --------------------------------------------------------------------------- #
# INEGI — Banco de Indicadores
# --------------------------------------------------------------------------- #

def inegi_crudo(
    indicador: str,
    idioma: str = "es",
    entidad: str = "00",
    dato_reciente: bool = False,
    banco: str = "BIE-BISE",
    version: str = "2.0",
) -> tuple[dict, str]:
    """Devuelve (json, url_sin_token) tal como lo entrega el Banco de Indicadores.

    `dato_reciente=False` trae la serie histórica completa;
    `True` trae únicamente la última observación.

    `banco` TIENE que coincidir con el banco de origen del indicador:
        BIE-BISE  indicadores económicos de coyuntura (INPC, IGAE, empleo...)
        BISE      censos y encuestas (población, vivienda...)

    Probado el 2026-09-02: el indicador 334452 responde con BIE-BISE y falla
    con BIE o con BISE a secas; el 1002000001 (población) es al revés.
    No existe un valor de `banco` que sirva para todo.
    """
    token = _token("INEGI_TOKEN")
    historica = "true" if dato_reciente else "false"

    url = (
        f"{INEGI_BASE}/{indicador}/{idioma}/{entidad}/{historica}"
        f"/{banco}/{version}/{token}?type=json"
    )
    url_segura = _ocultar_token(url, token)

    # El INEGI exige el token dentro de la ruta, así que CUALQUIER cosa que
    # exponga la URL lo filtra. `raise_for_status()` la mete en el texto de la
    # excepción, que después acaba en logs, en stderr y en reportes de error.
    # Por eso se re-lanza siempre con la URL enmascarada.
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise requests.RequestException(
            f"{type(e).__name__} al pedir el indicador {indicador}: "
            f"{_ocultar_token(str(e), token)}"
        ) from None  # from None: corta el encadenado, que también trae la URL

    return resp.json(), url_segura


def _fecha_inegi(periodo: str) -> date:
    """Convierte el TIME_PERIOD del INEGI a una fecha.

    El INEGI usa varios formatos según la frecuencia:
        "2024"       -> anual
        "2024/01"    -> mensual
        "2024/01/2"  -> quincenal (segunda quincena)
    Convención: se usa el primer día del periodo.
    """
    partes = periodo.strip().split("/")
    anio = int(partes[0])
    mes = int(partes[1]) if len(partes) > 1 else 1
    dia = 1
    if len(partes) > 2:  # quincenal: 1 -> día 1, 2 -> día 16
        dia = 1 if partes[2] == "1" else 16
    return date(anio, mes, dia)


def inegi_parsear(payload: dict, serie_id: str) -> list[Observacion]:
    """Convierte la respuesta del INEGI a observaciones.

    Estructura real, confirmada contra la API con el indicador 1002000001:

        {"Header": {...},
         "Series": [{"INDICADOR": "1002000001",
                     "FREQ": "7", "UNIT": "188", "UNIT_MULT": "...",
                     "TOPIC": "123", "NOTE": "1398", "SOURCE": "2,3,343,...",
                     "LASTUPDATE": "21/10/2024 12:00:00 a. m.",
                     "OBSERVATIONS": [{"TIME_PERIOD": "2020",
                                       "OBS_VALUE": "126014024.00000000000000000000",
                                       "OBS_EXCEPTION": None, "OBS_STATUS": "3",
                                       "OBS_SOURCE": "", "OBS_NOTE": "",
                                       "COBER_GEO": "0"}]}]}

    Dos cosas que no son obvias y que hay que manejar:
      - FREQ, UNIT, TOPIC, NOTE y SOURCE son CÓDIGOS de catálogo, no texto.
        `UNIT: 188` no dice "índice"; hay que resolverlo contra las tablas
        de metadatos del INEGI. Por eso no sirven para verificar un id a ojo.
      - Las observaciones llegan en orden DESCENDENTE (la más reciente primero).
        Se ordenan aquí para que quien consuma esto no dependa del orden de la API.
    """
    obs: list[Observacion] = []

    for serie in payload.get("Series", []):
        for punto in serie.get("OBSERVATIONS", []):
            # OBS_EXCEPTION marca un dato no disponible o suprimido.
            # Un hueco no es un cero: se omite para que quede nulo.
            if punto.get("OBS_EXCEPTION"):
                continue

            valor_txt = punto.get("OBS_VALUE")
            if valor_txt in (None, "", "N/A"):
                continue

            obs.append(
                Observacion(
                    serie_id=serie_id,
                    fecha=_fecha_inegi(punto["TIME_PERIOD"]),
                    valor=float(valor_txt),
                )
            )

    return sorted(obs, key=lambda o: o.fecha)
