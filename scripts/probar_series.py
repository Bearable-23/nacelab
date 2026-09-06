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
from datetime import date, datetime
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


def alerta_serie_vieja(lastupdate: str | None, frecuencia: str, meses: int = 6) -> None:
    """Avisa si el INEGI no ha actualizado una serie que debería estar viva.

    El umbral tiene que depender de la frecuencia: un censo lleva años sin
    moverse y está perfectamente sano; un indicador mensual con seis meses
    de atraso, no.
    """
    if not lastupdate or frecuencia in ("irregular", "quinquenal", "anual"):
        return
    try:
        fecha = datetime.strptime(lastupdate.split(" ")[0], "%d/%m/%Y").date()
    except ValueError:
        return

    dias = (date.today() - fecha).days
    if dias > meses * 30:
        print(f"     ⚠  Sin actualizar desde hace {dias // 30} meses "
              f"({fecha}). ¿Serie descontinuada?")


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

    if not fetch.hay_token("BANXICO_TOKEN"):
        print("  ⊘  Sin BANXICO_TOKEN en .env — fuente saltada")
        return

    series = [s for s in series if s.lista]
    if not series:
        print("  (ninguna serie de Banxico tiene id todavía)")
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

    if not fetch.hay_token("INEGI_TOKEN"):
        print("  ⊘  Sin INEGI_TOKEN en .env — fuente saltada")
        return

    for serie in series:
        if not serie.lista:
            print(f"\n  [PENDIENTE] {serie.id} — sin id todavía")
            print(f"              {serie.nota.strip()}")
            continue

        estado = "verificado" if serie.verificado else "SIN VERIFICAR"
        print(f"\n  [{estado}] {serie.id} — id {serie.fuente_id} ({serie.banco})")
        print(f"  Esperamos que sea: {serie.nombre}")

        try:
            payload, url_segura = fetch.inegi_crudo(
                indicador=serie.fuente_id,
                idioma=defaults.get("idioma", "es"),
                entidad=defaults.get("entidad", "00"),
                dato_reciente=defaults.get("dato_reciente", False),
                banco=serie.banco,
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

        # Una serie descontinuada responde 200 y devuelve datos de aspecto normal.
        # Lo único que la delata es LASTUPDATE. Pasó con el indicador 628194,
        # cuya última actualización es de octubre de 2024.
        alerta_serie_vieja(meta.get("LASTUPDATE"), serie.frecuencia)

        resumen_observaciones(fetch.inegi_parsear(payload, serie.id), serie.id)


def interrogar_candidato(clave: str, defaults: dict) -> None:
    """Enseña qué hay detrás de una clave que todavía NO está en el catálogo.

    Sirve para el momento en que sacas una clave del constructor de consultas
    y quieres saber qué es antes de escribirla en el YAML.

    Prueba los DOS bancos porque cuál toca no se puede saber de antemano:
    BIE-BISE sirve para los económicos de coyuntura y BISE para censos y
    encuestas, y el que no toca responde 400. Probar ambos evita concluir
    que una clave no existe cuando lo que estaba mal era el banco.

    Ojo con lo que este informe NO te dice: FREQ, UNIT, TOPIC y SOURCE son
    CÓDIGOS de catálogo, no texto. `UNIT: 1012` no dice "índice". No sirven
    para identificar una serie a ojo; sirven para comparar contra una serie
    que ya tengas verificada. Lo que de verdad identifica es el ÚLTIMO VALOR
    contra la cifra que el INEGI publicó en su boletín.
    """
    print(f"\n  ── clave {clave} " + "─" * (54 - len(clave)))
    encontrada = False

    for banco in ("BIE-BISE", "BISE"):
        try:
            payload, _ = fetch.inegi_crudo(
                indicador=clave,
                idioma=defaults.get("idioma", "es"),
                entidad=defaults.get("entidad", "00"),
                dato_reciente=False,
                banco=banco,
                version=defaults.get("version", "2.0"),
            )
        except Exception:  # noqa: BLE001
            print(f"     banco {banco:<9} → no responde")
            continue

        bloques = payload.get("Series", [])
        if not bloques:
            print(f"     banco {banco:<9} → responde pero sin 'Series'")
            continue

        encontrada = True
        meta = bloques[0]
        obs = fetch.inegi_parsear(payload, clave)
        print(f"     banco {banco:<9} → OK")
        print(f"       FREQ={meta.get('FREQ')} UNIT={meta.get('UNIT')} "
              f"TOPIC={meta.get('TOPIC')} SOURCE={meta.get('SOURCE')}")
        print(f"       LASTUPDATE  {meta.get('LASTUPDATE')}")

        if not obs:
            print("       sin observaciones")
            continue

        print(f"       {len(obs)} obs · {obs[0].fecha} → {obs[-1].fecha}")
        print("       últimos 3: " + ", ".join(
            f"{o.fecha}={o.valor:,.4f}" for o in obs[-3:]))

        # El intervalo típico entre observaciones delata la frecuencia real
        # mejor que el código FREQ, que hay que resolver contra un catálogo.
        if len(obs) > 3:
            saltos = [(obs[i + 1].fecha - obs[i].fecha).days
                      for i in range(len(obs) - 1)]
            tipico = sorted(saltos)[len(saltos) // 2]
            nombre = {1: "diaria", 7: "semanal", 15: "quincenal", 30: "mensual",
                      31: "mensual", 90: "trimestral", 91: "trimestral",
                      92: "trimestral", 365: "anual", 366: "anual"}.get(tipico)
            print(f"       salto típico entre datos: {tipico} días"
                  f"{f'  (≈ {nombre})' if nombre else ''}")

        alerta_serie_vieja(meta.get("LASTUPDATE"), "mensual")

    if not encontrada:
        print("     ✗ Ningún banco responde. La clave no existe, o el "
              "indicador no está en el Banco de Indicadores.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba las series del catálogo")
    parser.add_argument("--crudo", action="store_true", help="imprime el JSON completo")
    parser.add_argument(
        "--id", action="append", metavar="CLAVE", default=[],
        help="interroga una clave suelta que aún no está en el catálogo "
             "(se puede repetir). No toca el catálogo ni BigQuery.",
    )
    args = parser.parse_args()

    series, defaults = cargar_catalogo()

    if args.id:
        linea("CANDIDATOS — claves sueltas, todavía fuera del catálogo")
        for clave in args.id:
            interrogar_candidato(clave, defaults)
        linea("QUÉ HACER CON ESTO")
        print("Compara el último valor con el que el INEGI publicó en su")
        print("boletín. Esa es la prueba que identifica una serie; los códigos")
        print("de FREQ y UNIT solo sirven para compararla con otra que ya")
        print("tengas verificada. Si cuadra, agrégala al catálogo con")
        print("`verificado: false` y vuelve a correr esto sin --id.")
        return 0

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
