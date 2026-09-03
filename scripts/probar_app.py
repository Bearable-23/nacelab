"""Prueba la capa de datos de la app sin abrir un navegador.

Streamlit solo ejecuta el script cuando alguien se conecta, así que un
servidor que arranca no prueba nada. Esto sí: ejecuta las mismas consultas
y la misma lógica de interpretación que va a correr la página.

`.__wrapped__()` salta el caché de Streamlit para llamar la función real
fuera de un contexto de Streamlit.

Uso:
    python scripts/probar_app.py
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app import datos, interpretacion  # noqa: E402


def linea(t=""):
    print(f"\n{'─' * 68}")
    if t:
        print(t)
        print("─" * 68)


def main() -> int:
    linea("1. Catálogo (nombres desde series.yml, no desde BigQuery)")
    meta = datos.catalogo.__wrapped__()
    for k, v in meta.items():
        print(f"  {k:<18} {v['nombre']}")

    linea("2. Portada: última observación de cada serie")
    u = datos.ultimos.__wrapped__()
    print(u[["serie_id", "fecha", "valor", "var_mensual", "var_anual"]]
          .to_string(index=False))

    linea("3. Serie completa (consulta parametrizada)")
    s = datos.serie.__wrapped__("inpc_general")
    print(f"  inpc_general: {len(s)} filas, {s.fecha.min()} → {s.fecha.max()}")

    linea("4. Interpretación — lo que el usuario lee")
    fila = u[u.serie_id == "inpc_general"].iloc[0]
    emoji, frase = interpretacion.semaforo_inflacion(fila.var_anual)
    print(f"\n  INPC general · variación anual {fila.var_anual:.2f}%  {emoji}")
    print(f"  {frase}")
    print(f"  {interpretacion.contexto_historico(fila.percentil_var_anual, len(s))}")
    print(f"  {interpretacion.direccion(fila.var_mensual)}")

    sub = u[u.serie_id == "inpc_subyacente"].iloc[0]
    e2, f2 = interpretacion.semaforo_inflacion(sub.var_anual)
    print(f"\n  Subyacente · variación anual {sub.var_anual:.2f}%  {e2}")
    print(f"  {f2}")

    linea("OK")
    print("  La app puede leer gold y producir texto interpretado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
