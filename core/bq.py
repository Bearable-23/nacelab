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
from google.auth import impersonated_credentials
from google.cloud import bigquery

ALCANCE = ["https://www.googleapis.com/auth/cloud-platform"]


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

    if sa and not os.environ.get("K_SERVICE"):  # K_SERVICE solo existe en Cloud Run
        os.environ["IMPERSONATE_SA"] = f"{sa}@{proyecto}.iam.gserviceaccount.com"

    # `quota_project_id` decide a qué proyecto se le factura el uso de las APIs,
    # que no es lo mismo que el proyecto al que accedes. Si no se fija, se usa
    # el que quedó en tus credenciales ADC globales —probablemente otro proyecto
    # tuyo— y la suplantación falla con "API has not been used in project X".
    # Fijarlo aquí evita tener que cambiar tu configuración global de gcloud.
    credenciales, _ = google.auth.default(scopes=ALCANCE, quota_project_id=proyecto)

    suplantar = os.environ.get("IMPERSONATE_SA", "").strip()
    if suplantar:
        credenciales = impersonated_credentials.Credentials(
            source_credentials=credenciales,
            target_principal=suplantar,
            target_scopes=ALCANCE,
        )

    return bigquery.Client(project=proyecto, credentials=credenciales)


def identidad() -> str:
    """Con qué identidad estamos corriendo. Útil para no depurar a ciegas."""
    suplantar = os.environ.get("IMPERSONATE_SA", "").strip()
    if suplantar:
        return f"suplantando a {suplantar}"
    credenciales, _ = google.auth.default(scopes=ALCANCE)
    correo = getattr(credenciales, "service_account_email", None)
    return f"credenciales por defecto ({correo or 'usuario'})"
