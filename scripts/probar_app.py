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

    linea("4. Tipos que llegan a la gráfica")
    # Esta prueba existe por un bug que estuvo en produccion sin que nadie lo
    # viera: un NUMERIC de BigQuery llega a pandas como decimal.Decimal, y
    # Streamlit manda los dataframes por Arrow, donde eso se codifica como
    # decimal128 con escala 9. El navegador leia el entero sin escalar y una
    # inflacion de 3.95% se dibujaba como 3,948,334,100.
    #
    # No basta con revisar el numero en pantalla: las tarjetas usan f-strings,
    # que formatean un Decimal perfectamente. El texto decia "3.95%" al lado
    # de un eje que llegaba a 8,000,000,000. Solo se rompia la grafica.
    #
    # Por eso la comprobacion es sobre el ESQUEMA ARROW, que es exactamente lo
    # que viaja al navegador, y no sobre el valor formateado.
    import pyarrow as pa

    esquema = pa.Table.from_pandas(s).schema
    malas = [f.name for f in esquema if "decimal" in str(f.type)]
    for f in esquema:
        print(f"  {f.name:<22} {f.type}")
    if malas:
        print(f"\n  ✗ Estas columnas viajan como decimal128: {', '.join(malas)}")
        print("    La gráfica las va a dibujar multiplicadas por 10^9.")
        print("    Arréglalo con CAST(... AS FLOAT64) en sql/gold_indicador.sql")
        return 1
    print("\n  ✓ Ninguna columna viaja como decimal128.")

    linea("5. Interpretación — lo que el usuario lee")
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
