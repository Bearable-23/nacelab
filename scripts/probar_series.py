"""Paso 4 del plan: bajar las series del catálogo e imprimirlas. Sin BigQuery.

Este script hace dos cosas distintas:

  1. VERIFICA IDs. Para cada serie marcada como `verificado: false`, consulta la
     API y te enseña qué devuelve realmente, para que confirmes que el id
     corresponde al indicador que crees. Un id copiado de un ejemplo de
     documentación no es un id verificado.

  2. MUESTRA LA ESTRUCTURA CRUDA de la respuesta antes de parsearla. Así el
     parser se escribe contra lo que la API manda de verdad, no contra lo que
     suponemos que manda.

Uso:
    python scripts/probar_series.py
    python scripts/probar_series.py --crudo     # imprime el JSON completo
"""

import argparse
import json
import sys
from pathlib import Path

# La consola de Windows usa cp1252 por defecto y revienta con acentos y símbolos.
# Sin esto, el script falla en Windows y funciona en Linux — justo el tipo de
# diferencia que rompe un pipeline al desplegarlo en Cloud Run (o al revés).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from core import fetch  # noqa: E402
from core.catalog import cargar_catalogo, por_fuente  # noqa: E402

load_dotenv()


def linea(titulo: str = "") -> None:
    print(f"\n{'─' * 72}")
    if titulo:
        print(titulo)
        print("─" * 72)


def resumen_observaciones(obs: list, nombre: str) -> None:
    """Imprime el estado de una serie: cuántos datos, rango, últimos valores."""
    if not obs:
        print(f"  ⚠  {nombre}: la API respondió pero sin observaciones")
        return

    obs = sorted(obs, key=lambda o: o.fecha)
    print(f"  ✓  {nombre}")
    print(f"     {len(obs)} observaciones · {obs[0].fecha} → {obs[-1].fecha}")
    print("     últimos 5:")
    for o in obs[-5:]:
        print(f"       {o.fecha}   {o.valor:>14,.4f}")


def probar_banxico(series, crudo: bool) -> None:
    linea("BANXICO — SIE")

    if not series:
        print("  (no hay series de Banxico en el catálogo)")
        return

    ids = [s.fuente_id for s in series]
    mapeo = {s.fuente_id: s.id for s in series}
    print(f"  Pidiendo en una sola petición: {', '.join(ids)}")

    payload = fetch.banxico_crudo(ids)

    if crudo:
        print(json.dumps(payload, indent=2, ensure_ascii=False)[:3000])

    # Lo que la API dice que es cada serie. Esto es la verificación del id.
    print("\n  Títulos según la API:")
    for s in payload["bmx"]["series"]:
        print(f"    {s['idSerie']:<12} {s.get('titulo', '(sin título)')}")

    print()
    observaciones = fetch.banxico_parsear(payload, mapeo)
    for serie in series:
        propias = [o for o in observaciones if o.serie_id == serie.id]
        resumen_observaciones(propias, f"{serie.id} — {serie.nombre}")


def probar_inegi(series, defaults: dict, crudo: bool) -> None:
    linea("INEGI — Banco de Indicadores")

    if not series:
        print("  (no hay series del INEGI en el catálogo)")
        return

    for serie in series:
        estado = "verificado" if serie.verificado else "SIN VERIFICAR"
        print(f"\n  [{estado}] {serie.id} — id {serie.fuente_id}")
        print(f"  Esperamos que sea: {serie.nombre}")

        try:
            payload, url_segura = fetch.inegi_crudo(
                indicador=serie.fuente_id,
                idioma=defaults.get("idioma", "es"),
                entidad=defaults.get("entidad", "00"),
                serie_historica=defaults.get("serie_historica", False),
                banco=defaults.get("banco", "BIE"),
                version=defaults.get("version", "2.0"),
            )
        except Exception as e:  # noqa: BLE001
            print(f"  ✗  Falló la petición: {e}")
            continue

        print(f"  URL: {url_segura}")

        if crudo:
            print(json.dumps(payload, indent=2, ensure_ascii=False)[:3000])

        # Metadatos que devuelve el INEGI. Aquí es donde confirmas el id:
        # si UNIT dice "Pesos" y esperabas un índice, el id está mal.
        bloques = payload.get("Series", [])
        if not bloques:
            print("  ⚠  La respuesta no trae 'Series'. Estructura recibida:")
            print(f"     llaves de primer nivel: {list(payload.keys())}")
            continue

        meta = bloques[0]
        for campo in ("INDICADOR", "FREQ", "UNIT", "TOPIC", "LASTUPDATE", "SOURCE"):
            if campo in meta:
                print(f"     {campo:<12} {meta[campo]}")

        resumen_observaciones(fetch.inegi_parsear(payload, serie.id), serie.id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba las series del catálogo")
    parser.add_argument("--crudo", action="store_true", help="imprime el JSON completo")
    args = parser.parse_args()

    series, defaults = cargar_catalogo()
    print(f"Catálogo: {len(series)} series")

    sin_verificar = [s for s in series if not s.verificado]
    if sin_verificar:
        print(f"⚠  {len(sin_verificar)} sin verificar: {', '.join(s.id for s in sin_verificar)}")

    try:
        probar_banxico(por_fuente(series, "banxico"), args.crudo)
        probar_inegi(por_fuente(series, "inegi"), defaults, args.crudo)
    except RuntimeError as e:  # token faltante
        linea()
        print(e)
        return 1

    linea("SIGUIENTE PASO")
    print("Confirma que cada indicador es el que esperabas (nombre, unidad,")
    print("frecuencia, último dato). Solo entonces marca `verificado: true`")
    print("en catalog/series.yml. Un id sin verificar contamina todo lo que")
    print("construyas encima.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
