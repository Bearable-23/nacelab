"""Entrypoint del job programado: ingesta y reconstrucción de gold.

POR QUÉ UN ARCHIVO Y NO `a.py && b.py`
--------------------------------------
Encadenar con && corre los dos pasos, pero no deja nada útil cuando algo va
mal: hay que leer la salida entera para saber en qué punto se rompió, y no
hay forma de distinguir "la API del INEGI no respondió" de "BigQuery rechazó
el MERGE". Un job automático se mira poco, y cuando se mira es porque ya
falló, así que el momento de invertir en el mensaje es antes.

QUÉ COMPRUEBA ADEMÁS DE CORRER
------------------------------
Terminar sin excepción no significa que la corrida sirviera. Este job además
verifica que los datos que quedaron estén FRESCOS: si una fuente lleva
demasiado sin publicar, o si la carga no trajo nada nuevo cuando debería,
sale con código distinto de cero para que Cloud Run lo marque como fallo y la
alerta se dispare.

Esa es la diferencia entre automatizar y abandonar. Un job manual tiene un
humano que nota lo raro; uno automático que falla callado deja el sitio
sirviendo datos viejos sin que nadie se entere — que es peor que no
automatizar nada.

Uso:
    python scripts/job_diario.py
    python scripts/job_diario.py --tolerancia-dias 5
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

from core import bq  # noqa: E402
from core.catalog import cargar_catalogo  # noqa: E402

load_dotenv()

# Cuánto puede llevar una serie sin dato nuevo antes de considerarlo un
# problema, medido desde la FECHA de la observación.
#
# El primer intento puso 45 días para las mensuales y salto de inmediato con
# una falsa alarma. La razón: la fecha de una observación mensual no es la de
# su publicación. El INPC de julio se fecha 2026-07-01 y se publica el 9 de
# agosto; el de agosto no sale hasta el 9 de septiembre. Entre esas dos
# publicaciones, el dato más reciente que EXISTE tiene casi 70 días de fecha,
# y no hay nada roto.
#
# El margen tiene que cubrir tres cosas encadenadas:
#     el periodo que la observación cubre   (un mes)
#   + el rezago hasta que se publica         (~9 días en el INPC)
#   + la espera hasta la publicación siguiente (otro mes)
#
# De ahí los 75 días para mensual. Los demás siguen la misma lógica: una
# trimestral se publica unos 55 días después de cerrar el trimestre, así que
# el peor caso ronda los 145.
#
# Generosos a propósito. Una alerta que salta en un puente largo deja de
# leerse en dos semanas, y entonces no sirve el día que el problema sea real.
# Más vale tardar un ciclo en detectar una serie muerta que entrenar a nadie
# a ignorar el aviso.
MARGEN_DIAS = {
    "diaria": 7,       # día hábil + fin de semana largo con puente
    "quincenal": 45,   # quincena + rezago + siguiente quincena
    "mensual": 75,     # mes + ~9 días de rezago + mes siguiente
    "trimestral": 160,  # trimestre + ~55 de rezago + trimestre siguiente
    "anual": 500,
}


def linea(t: str = "") -> None:
    print(f"\n{'=' * 70}", flush=True)
    if t:
        print(t, flush=True)
        print("=" * 70, flush=True)


def paso(nombre: str, script: str, extra: list[str] | None = None) -> None:
    """Corre un script del pipeline. Si falla, revienta el job entero."""
    linea(nombre)
    inicio = time.monotonic()
    r = subprocess.run(
        [sys.executable, str(RAIZ / "scripts" / script), *(extra or [])],
        cwd=RAIZ,
    )
    segundos = time.monotonic() - inicio

    if r.returncode != 0:
        # No se sigue al paso siguiente. Construir gold sobre una ingesta a
        # medias produciría una tabla que se ve normal y miente.
        print(f"\n✗ {script} salió con código {r.returncode} "
              f"tras {segundos:.0f}s. Se aborta el job.", flush=True)
        raise SystemExit(r.returncode)

    print(f"\n✓ {script} terminó en {segundos:.0f}s", flush=True)


def revisar_frescura(margen_extra: int = 0) -> list[str]:
    """¿Quedó algún dato viejo? Devuelve la lista de problemas.

    Se mira gold y no la API: lo que importa no es que la fuente tenga datos
    nuevos, sino que hayan LLEGADO. Una ingesta que corre, no falla y no trae
    nada es exactamente el modo de falla silencioso que hay que cazar.
    """
    proyecto = os.environ["GCP_PROJECT"]
    cliente = bq.cliente(proyecto, sa="sa-transform")
    series, _ = cargar_catalogo()
    frecuencia = {s.id: s.frecuencia for s in series if s.verificado}

    filas = cliente.query(f"""
        SELECT serie_id, MAX(fecha) AS ultima
        FROM `{proyecto}.nacelab_gold.gold_indicador`
        GROUP BY serie_id
    """).result()

    hoy = date.today()
    problemas: list[str] = []
    print(f"  {'serie':<20}{'último dato':<14}{'días':>6}  estado", flush=True)

    vistas = set()
    for f in filas:
        vistas.add(f.serie_id)
        frec = frecuencia.get(f.serie_id)
        if frec is None:
            continue  # serie en gold que ya no está verificada; no es asunto de aquí
        dias = (hoy - f.ultima).days
        margen = MARGEN_DIAS.get(frec)

        if margen is None:  # irregular: un censo no tiene por qué moverse
            estado = "sin margen (irregular)"
        elif dias > margen + margen_extra:
            estado = f"✗ VIEJO (margen {margen + margen_extra}d)"
            problemas.append(
                f"{f.serie_id}: último dato {f.ultima}, hace {dias} días "
                f"(margen para frecuencia {frec}: {margen + margen_extra})"
            )
        else:
            estado = "✓"
        print(f"  {f.serie_id:<20}{str(f.ultima):<14}{dias:>6}  {estado}", flush=True)

    # Una serie verificada que no aparece en gold es un fallo silencioso:
    # la ingesta la saltó y nadie lo notaría mirando las que sí llegaron.
    for sid in frecuencia:
        if sid not in vistas:
            problemas.append(f"{sid}: verificada en el catálogo pero AUSENTE de gold")
            print(f"  {sid:<20}{'—':<14}{'—':>6}  ✗ AUSENTE de gold", flush=True)

    return problemas


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--tolerancia-dias", type=int, default=0,
        help="días extra de margen antes de considerar un dato viejo. "
             "Para periodos de mucho feriado, sin tocar el código.",
    )
    args = p.parse_args()

    arranque = datetime.now(timezone.utc)
    linea(f"nacelab · job diario · {arranque.isoformat(timespec='seconds')}")
    # Con qué identidad corre. En un Job de Cloud Run no existe K_SERVICE, así
    # que core/bq.py entra por la rama de suplantación: la cuenta adjunta pide
    # tokens prestados de sa-ingest y sa-transform. Se imprime porque un fallo
    # de permisos es mucho más fácil de leer sabiendo quién lo intentó.
    print(f"  identidad base: {bq.identidad()}", flush=True)

    paso("1. Ingesta a bronze", "cargar_bronze.py")
    # Sin la prueba de sa-app: este job corre como sa-job, que a propósito no
    # puede pedir el token de la cuenta del sitio. Darle ese permiso para
    # ejecutar una aserción sería ampliar privilegios por comodidad.
    paso("2. Construcción de gold", "construir_gold.py", ["--sin-prueba-sa-app"])

    linea("3. Frescura de los datos que quedaron")
    problemas = revisar_frescura(args.tolerancia_dias)

    linea("RESULTADO")
    if problemas:
        print("  ✗ El job corrió pero los datos NO están al día:\n", flush=True)
        for x in problemas:
            print(f"    · {x}", flush=True)
        print("\n  Se sale con error para que esto dispare una alerta en vez", flush=True)
        print("  de quedarse como una corrida verde con datos viejos.", flush=True)
        return 1

    total = (datetime.now(timezone.utc) - arranque).total_seconds()
    print(f"  ✓ Todo al día. Job completo en {total:.0f}s.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
