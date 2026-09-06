"""Cliente de BigQuery.

Dos entornos, un solo código y CERO llaves de service account:

  En tu laptop   tu usuario suplanta a una service account. Google emite un
                 token temporal (minutos) contra tu identidad. Corres con los
                 permisos REALES de la cuenta, no con los tuyos de Owner.

  En Cloud Run   la service account va adjunta al servicio. `google.auth.default()`
                 la toma sola. No hay nada que suplantar.

La diferencia la hace la variable IMPERSONATE_SA: presente en tu .env local,
ausente en Cloud Run. El código no distingue entornos, solo mira la variable.

Por qué importa suplantar en local: eres Owner del proyecto. Si corrieras con
tus credenciales, todo funcionaría aunque los permisos por dataset estuvieran
mal puestos, y el error aparecería hasta el despliegue.
"""

from __future__ import annotations

import os

import google.auth
import requests
from google.auth import impersonated_credentials
from google.cloud import bigquery

ALCANCE = ["https://www.googleapis.com/auth/cloud-platform"]

# El servidor de metadatos de Google, que solo responde dentro de GCP.
METADATOS = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/email"
)


def sa_adjunta() -> str:
    """Con qué service account viene corriendo este contenedor. "" fuera de GCP.

    Se pregunta en vez de deducirlo de variables de entorno, y esa distinción
    costó un job fallido.
    """
    try:
        r = requests.get(METADATOS, headers={"Metadata-Flavor": "Google"}, timeout=2)
        return r.text.strip() if r.ok else ""
    except requests.RequestException:
        return ""  # no estamos en GCP: en una laptop no hay servidor de metadatos


def cliente(proyecto: str | None = None, sa: str | None = None) -> bigquery.Client:
    """Devuelve un cliente de BigQuery ya autenticado.

    `sa` permite pedir una cuenta concreta ("sa-transform") en vez de la de
    IMPERSONATE_SA. Cada paso del pipeline corre con la identidad mínima que
    necesita: ingesta escribe en bronze, transformación escribe en gold, la
    app solo lee. En Cloud Run se ignora: allá la cuenta va adjunta al servicio.
    """
    proyecto = proyecto or os.environ.get("GCP_PROJECT", "").strip()
    if not proyecto:
        raise RuntimeError("Falta la variable de entorno GCP_PROJECT.")

    # A quién queremos ser: la cuenta pedida, o la de IMPERSONATE_SA si no se
    # pidió ninguna (que es como funciona en la laptop).
    objetivo = (
        f"{sa}@{proyecto}.iam.gserviceaccount.com"
        if sa
        else os.environ.get("IMPERSONATE_SA", "").strip()
    )

    # Se suplanta solo si NO somos ya esa cuenta.
    #
    # La versión anterior decidía esto mirando K_SERVICE, con el razonamiento
    # de que "en Cloud Run la cuenta va adjunta, así que no hay nada que
    # suplantar". Eso era cierto mientras el único despliegue fuera el sitio,
    # donde la cuenta adjunta (sa-app) ES la que se necesita.
    #
    # Dejó de serlo con el job programado: ahí la cuenta adjunta es sa-job,
    # que a propósito no tiene ningún permiso en BigQuery y solo sirve para
    # pedir tokens prestados. Con la regla vieja el job NO suplantaba, corría
    # como sa-job y moría con un 403 sobre bronze.
    #
    # Preguntar la identidad real al servidor de metadatos acierta en los tres
    # entornos sin configurar nada:
    #     laptop  adjunta=""        objetivo=sa-ingest    -> suplanta
    #     sitio   adjunta=sa-app    objetivo=sa-app       -> no suplanta
    #     job     adjunta=sa-job    objetivo=sa-ingest    -> suplanta
    suplantar = objetivo if objetivo and objetivo != sa_adjunta() else ""
    if suplantar:
        os.environ["IMPERSONATE_SA"] = suplantar

    # `quota_project_id` decide a qué proyecto se le factura el uso de las APIs,
    # que no es lo mismo que el proyecto al que accedes. Si no se fija, se usa
    # el que quedó en tus credenciales ADC globales —probablemente otro proyecto
    # tuyo— y la suplantación falla con "API has not been used in project X".
    # Fijarlo aquí evita tener que cambiar tu configuración global de gcloud.
    credenciales, _ = google.auth.default(scopes=ALCANCE, quota_project_id=proyecto)

    if suplantar:
        credenciales = impersonated_credentials.Credentials(
            source_credentials=credenciales,
            target_principal=suplantar,
            target_scopes=ALCANCE,
        )

    return bigquery.Client(project=proyecto, credentials=credenciales)


def identidad() -> str:
    """Con qué identidad estamos corriendo. Útil para no depurar a ciegas.

    Dice también cuál es la cuenta ADJUNTA, no solo a quién se suplanta. Sin
    ese dato, el log de un job fallido decía "credenciales por defecto" y no
    había forma de saber cuáles eran: la pregunta era justo esa.
    """
    adjunta = sa_adjunta()
    suplantar = os.environ.get("IMPERSONATE_SA", "").strip()
    if suplantar:
        desde = adjunta or "credenciales locales"
        return f"suplantando a {suplantar} desde {desde}"
    if adjunta:
        return f"cuenta adjunta {adjunta} (sin suplantar)"
    credenciales, _ = google.auth.default(scopes=ALCANCE)
    correo = getattr(credenciales, "service_account_email", None)
    return f"credenciales por defecto ({correo or 'usuario'})"
