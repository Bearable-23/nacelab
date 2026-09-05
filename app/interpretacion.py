"""Traduce un número a una frase que alguien pueda entender.

Esta es la parte más barata de construir y la más difícil de copiar. Un
desarrollador pone el número; saber contra qué compararlo y qué significa
es criterio económico.

Reglas explícitas, sin modelo de lenguaje. Un LLM aquí sería más lento, más
caro, y podría inventar una interpretación. Estas reglas son auditables:
están escritas y se pueden discutir.
"""

from __future__ import annotations

# Objetivo de Banxico: 3% con un intervalo de variabilidad de +/- 1 punto.
OBJETIVO_BANXICO = 3.0
BANDA_BANXICO = 1.0


def sin_dato(x: float | None) -> bool:
    """¿Este valor es un hueco?

    Un hueco llega de DOS formas distintas y hay que reconocer las dos.
    BigQuery devuelve NULL, pero en qué se convierte depende del TIPO de la
    columna: una columna de objetos da `None`, una FLOAT64 da `NaN`. Desde
    que gold sirve FLOAT64 (ver el CAST en gold_indicador.sql), lo que llega
    aquí es NaN.

    La diferencia no es académica: `NaN is not None` es True, así que un
    `if x is not None` deja pasar el hueco y la página acaba imprimiendo
    "nan%". Justamente lo que pasó al castear gold a FLOAT64.

    `x != x` detecta NaN sin depender de pandas ni de numpy: NaN es el único
    valor que no es igual a sí mismo.
    """
    return x is None or x != x


def semaforo_inflacion(var_anual: float | None) -> tuple[str, str]:
    """Devuelve (emoji, frase) para una variación anual de precios."""
    if sin_dato(var_anual):
        return "⚪", "Sin dato comparable de hace un año."

    piso = OBJETIVO_BANXICO - BANDA_BANXICO
    techo = OBJETIVO_BANXICO + BANDA_BANXICO

    if piso <= var_anual <= techo:
        return "🟢", (
            f"Dentro del intervalo de variabilidad de Banxico "
            f"({piso:.0f}% a {techo:.0f}%)."
        )
    if var_anual > techo:
        return "🟡", (
            f"Por encima del intervalo de Banxico: "
            f"{var_anual - OBJETIVO_BANXICO:.2f} puntos sobre el objetivo de "
            f"{OBJETIVO_BANXICO:.0f}%."
        )
    return "🔵", (
        f"Por debajo del intervalo de Banxico. Una inflación muy baja no es "
        f"automáticamente buena: puede indicar debilidad de la demanda."
    )


def contexto_historico(percentil: float | None, n_obs: int) -> str:
    """Ubica el dato dentro de la historia de la propia serie."""
    if sin_dato(percentil):
        return ""
    pct = percentil * 100
    return (
        f"Más alta que el {pct:.0f}% de los {n_obs} periodos registrados."
        if pct >= 50
        else f"Más baja que el {100 - pct:.0f}% de los {n_obs} periodos registrados."
    )


def direccion(var_mensual: float | None) -> str:
    if sin_dato(var_mensual):
        return ""
    if abs(var_mensual) < 0.005:
        return "Sin cambio respecto al mes anterior."
    verbo = "subió" if var_mensual > 0 else "bajó"
    return f"{verbo.capitalize()} {abs(var_mensual):.2f}% respecto al mes anterior."
