# Imagen de la app de Streamlit para Cloud Run.

FROM python:3.13-slim

WORKDIR /app

# Las dependencias van primero y en su propia capa: Docker la cachea y no
# la reconstruye cuando solo cambia el código. Un deploy pasa de minutos
# a segundos.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/    ./core/
COPY app/     ./app/
COPY catalog/ ./catalog/
COPY sql/     ./sql/
# El tema. Sin esta linea el contenedor no lo lleva, y el sitio publicado
# vuelve a seguir la preferencia del sistema operativo de cada visitante:
# se veria distinto en produccion que en local, y sin ningun error visible.
COPY .streamlit/ ./.streamlit/

ENV GCP_PROJECT=nacelab-prod
ENV PYTHONUNBUFFERED=1

# Deliberadamente NO se define IMPERSONATE_SA.
#
# En tu máquina esa variable hace que suplantes a una service account. Aquí
# no hace falta: la cuenta va adjunta al servicio y google.auth.default() la
# toma sola. core/bq.py lo detecta mirando K_SERVICE, que Cloud Run define.
#
# Si se definiera, el contenedor intentaria suplantar a una cuenta desde otra
# cuenta y fallaria con un error de permisos poco obvio.

# Cloud Run inyecta PORT. El valor por defecto es solo para correr local.
# `--server.address=0.0.0.0` es obligatorio: sin él Streamlit escucha en
# localhost y Cloud Run no puede alcanzarlo.
EXPOSE 8080
CMD streamlit run app/main.py \
    --server.port=${PORT:-8080} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.maxUploadSize=5 \
    --browser.gatherUsageStats=false
