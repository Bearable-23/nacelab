"""Deriva los archivos de marca del sitio a partir del logotipo original.

POR QUE EXISTE ESTE ARCHIVO
---------------------------
Ninguno de los PNG originales tiene canal alfa: todos son RGB con fondo opaco,
negro o degradado. Puestos sobre el fondo del sitio (#12161C) se verian como
un recuadro pegado encima, no como marca integrada.

Como el logotipo es blanco puro sobre negro casi puro, la transparencia se
puede derivar sin recortar a mano: se usa la LUMINANCIA como canal alfa. Donde
el original es blanco queda opaco, donde es negro queda transparente, y los
bordes suavizados del original se convierten en bordes suavizados del alfa. El
resultado es mejor que un recorte manual y es reproducible.

El RGB se fija a blanco puro. Asi la marca se ve igual sobre cualquier fondo
oscuro y no arrastra el gris del degradado original.

PROVENIENCIA
------------
El original vive FUERA del repositorio, en la ruta de abajo. Por eso los PNG
derivados si se versionan: sin ellos el contenedor no tiene marca, y sin este
script nadie sabria de donde salieron. Si el original cambia, se vuelve a
correr esto.

Uso:
    python scripts/generar_marca.py
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
ORIGEN = Path(r"E:\WorkSpace\NaceLab\Logotipo\2048 x 1152.png")
DESTINO = RAIZ / "app" / "marca"

# Se elige esta variante y no las cuadradas porque las cuadradas tienen un
# degradado de fondo: la luminancia del degradado se colaria al alfa y dejaria
# un halo. Sobre negro plano la separacion es limpia.
#
# Umbral de "hay tinta aqui". El fondo del original es [1,1,2], no negro puro,
# asi que un umbral de 0 tomaria el lienzo entero.
UMBRAL = 40

ANCHO_LOGO = 600   # el lockup vertical original, por si hace falta
LADO_ICONO = 256   # solo la marca, cuadrada, para el favicon
ALTO_HORIZONTAL = 200  # el lockup horizontal, que es el que usa la app


def recuadro_del_logo(lum: np.ndarray) -> tuple[int, int, int, int]:
    """Ubica el logotipo dentro del banner, sin los adornos.

    El banner trae dos reglas horizontales de borde a borde y unas figuras
    decorativas a los lados. Las reglas se detectan porque ocupan casi todo el
    ancho, cosa que el logotipo nunca hace. Los adornos quedan fuera al mirar
    solo la banda central.
    """
    alto, ancho = lum.shape
    mascara = lum > UMBRAL
    reglas = np.where(mascara.sum(axis=1) > ancho * 0.8)[0]
    mascara[reglas, :] = False

    x_desde, x_hasta = int(ancho * 0.32), int(ancho * 0.68)
    ys, xs = np.where(mascara[:, x_desde:x_hasta])
    return (xs.min() + x_desde, ys.min(), xs.max() + x_desde + 1, ys.max() + 1)


def corte_marca_texto(lum: np.ndarray, caja: tuple[int, int, int, int]) -> int:
    """Devuelve la fila donde termina la marca y empieza el texto.

    Entre el simbolo y la palabra NACELAB hay una franja de filas totalmente
    vacias. Se busca la racha vacia mas larga: es el aire del lockup, mas ancho
    que el que separa NACELAB de ANALYTICS.
    """
    x0, y0, x1, y1 = caja
    filas = (lum[y0:y1, x0:x1] > UMBRAL).sum(axis=1)
    vacias = np.where(filas == 0)[0]

    rachas, actual = [], [vacias[0]]
    for v in vacias[1:]:
        if v == actual[-1] + 1:
            actual.append(v)
        else:
            rachas.append(actual)
            actual = [v]
    rachas.append(actual)

    mayor = max(rachas, key=len)
    return y0 + mayor[0]


def a_transparente(im: Image.Image) -> Image.Image:
    """Luminancia -> alfa, RGB -> blanco puro."""
    lum = np.array(im.convert("L"))
    blanco = np.full((*lum.shape, 3), 255, dtype=np.uint8)
    return Image.fromarray(np.dstack([blanco, lum]), mode="RGBA")


def recortar_ajustado(im: Image.Image) -> Image.Image:
    """Quita el margen transparente sobrante."""
    caja = im.getchannel("A").point(lambda v: 255 if v > UMBRAL else 0).getbbox()
    return im.crop(caja)


def main() -> int:
    if not ORIGEN.exists():
        print(f"✗ No encuentro el original en {ORIGEN}")
        print("  Los PNG derivados ya versionados siguen sirviendo; esto solo")
        print("  hace falta si el logotipo cambia.")
        return 1

    DESTINO.mkdir(parents=True, exist_ok=True)
    original = Image.open(ORIGEN).convert("RGB")
    lum = np.array(original.convert("L"))

    caja = recuadro_del_logo(lum)
    corte = corte_marca_texto(lum, caja)
    x0, y0, x1, y1 = caja
    print(f"  logotipo en el banner: x {x0}-{x1}, y {y0}-{y1}")
    print(f"  la marca termina en y={corte}; debajo va el texto")

    # ---------------------------------------------------------- completo --
    completo = recortar_ajustado(a_transparente(original.crop(caja)))
    alto = round(completo.height * ANCHO_LOGO / completo.width)
    completo = completo.resize((ANCHO_LOGO, alto), Image.LANCZOS)
    completo.save(DESTINO / "nacelab_logo.png")
    print(f"  → app/marca/nacelab_logo.png   {completo.size}")

    # -------------------------------------------------------------- icono --
    solo_marca = recortar_ajustado(a_transparente(original.crop((x0, y0, x1, corte))))

    # Cuadrado con la marca centrada: un favicon deformado se ve barato, y
    # Streamlit no respeta la proporcion si le pasas algo rectangular.
    lado = max(solo_marca.size)
    lienzo = Image.new("RGBA", (lado, lado), (255, 255, 255, 0))
    lienzo.paste(
        solo_marca,
        ((lado - solo_marca.width) // 2, (lado - solo_marca.height) // 2),
    )
    lienzo = lienzo.resize((LADO_ICONO, LADO_ICONO), Image.LANCZOS)
    lienzo.save(DESTINO / "nacelab_icono.png")
    print(f"  → app/marca/nacelab_icono.png  {lienzo.size}")

    # -------------------------------------------------------- horizontal --
    # El lockup ORIGINAL es vertical: símbolo arriba, texto abajo, casi
    # cuadrado. st.logo lo mete en la cabecera de la barra lateral, que tiene
    # altura fija, así que un logotipo cuadrado se encoge hasta ~32 px de alto
    # y la palabra NACELAB queda ilegible. Medido: renderizaba 31x32.
    #
    # Un lockup horizontal usa esa altura para el símbolo y gasta el ancho
    # sobrante —que sí hay— en el texto. Es la forma que ese hueco pide.
    texto = recortar_ajustado(a_transparente(original.crop((x0, corte, x1, y1))))

    alto = ALTO_HORIZONTAL
    marca_h = solo_marca.resize(
        (round(solo_marca.width * alto / solo_marca.height), alto), Image.LANCZOS
    )
    # El texto al 55% del alto del símbolo: al 100% pesaría más que la marca y
    # el conjunto se leería como una palabra con un adorno al lado.
    alto_texto = round(alto * 0.55)
    texto_h = texto.resize(
        (round(texto.width * alto_texto / texto.height), alto_texto), Image.LANCZOS
    )

    hueco = round(alto * 0.22)
    ancho = marca_h.width + hueco + texto_h.width
    horizontal = Image.new("RGBA", (ancho, alto), (255, 255, 255, 0))
    horizontal.paste(marca_h, (0, 0), marca_h)
    horizontal.paste(
        texto_h, (marca_h.width + hueco, (alto - texto_h.height) // 2), texto_h
    )
    horizontal.save(DESTINO / "nacelab_logo_h.png")
    print(f"  → app/marca/nacelab_logo_h.png {horizontal.size}")

    # ------------------------------------------------------------ control --
    # Se mide alfa <= 5, no alfa == 0. Tras el remuestreo Lanczos casi ningun
    # pixel queda en cero exacto: el filtro reparte valores minusculos por todo
    # el lienzo. Medir el cero exacto daba 2% y hacia fallar la comprobacion
    # sobre archivos que estaban perfectos.
    #
    # Lo que de verdad indica una extraccion limpia es que el histograma sea
    # BIMODAL: mucho casi-transparente, bastante casi-opaco y poco en medio. Un
    # fondo mal separado deja el lienzo lleno de valores intermedios.
    for archivo in ("nacelab_logo.png", "nacelab_icono.png", "nacelab_logo_h.png"):
        im = Image.open(DESTINO / archivo)
        a = np.array(im.getchannel("A")).astype(int)
        vacio = (a <= 5).sum() / a.size * 100
        tinta = (a >= 200).sum() / a.size * 100
        medio = 100 - vacio - tinta

        if im.mode != "RGBA" or vacio < 30 or tinta < 5 or medio > 25:
            print(f"  ✗ {archivo}: {vacio:.0f}% vacio, {tinta:.0f}% tinta, "
                  f"{medio:.0f}% intermedio. Se esperaba un histograma bimodal; "
                  f"demasiado valor intermedio sugiere que el fondo no se separo.")
            return 1
        print(f"  ✓ {archivo}: {vacio:.0f}% transparente, {tinta:.0f}% tinta sólida")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
