# Crea la infraestructura de BigQuery de nacelab.
#
# Es idempotente: puedes correrlo varias veces sin romper nada. Lo que ya
# existe se reporta y se omite.
#
# NO crea el proyecto ni los guardarraíles de costo. Eso va antes, a mano,
# y a propósito: el presupuesto y la cuota no deben depender de que alguien
# se acuerde de correr un script.
#
# Uso:  .\scripts\setup_gcp.ps1

$PROJECT        = "nacelab-prod"
$PROJECT_NUMBER = "314189642979"
$LOCATION       = "US"    # PERMANENTE. Un dataset no se puede mover de región.

$DATASETS = @("nacelab_bronze", "nacelab_silver", "nacelab_gold")

# Las tres identidades del proyecto. El principio: si comprometen la app
# pública, que el atacante solo pueda leer datos que ya son públicos.
$CUENTAS = @(
    @{ id = "sa-ingest";    desc = "Job de ingesta: escribe en bronze" }
    @{ id = "sa-transform"; desc = "dbt: lee bronze, escribe silver y gold" }
    @{ id = "sa-app";       desc = "Sitio publico: SOLO lee gold" }
)

# Permisos por dataset. Nota que sa-app no aparece en bronze ni en silver:
# no es un descuido, es el diseño.
$PERMISOS = @(
    @{ sa = "sa-ingest";    dataset = "nacelab_bronze"; rol = "roles/bigquery.dataEditor" }
    @{ sa = "sa-transform"; dataset = "nacelab_bronze"; rol = "roles/bigquery.dataViewer" }
    @{ sa = "sa-transform"; dataset = "nacelab_silver"; rol = "roles/bigquery.dataEditor" }
    @{ sa = "sa-transform"; dataset = "nacelab_gold";   rol = "roles/bigquery.dataEditor" }
    @{ sa = "sa-app";       dataset = "nacelab_gold";   rol = "roles/bigquery.dataViewer" }
)

function Titulo($texto) {
    Write-Host ""
    Write-Host ("-" * 68)
    Write-Host $texto
    Write-Host ("-" * 68)
}

# -------------------------------------------------------------------- 0. APIs
Titulo "0. APIs"

# Solo lo que se usa. Cada API activa es superficie de ataque.
# iamcredentials es la que permite suplantar service accounts (paso 7).
$APIS = @(
    "bigquery.googleapis.com"
    "iamcredentials.googleapis.com"
    "secretmanager.googleapis.com"
    "run.googleapis.com"
    "cloudscheduler.googleapis.com"
)
gcloud services enable @APIS --project=$PROJECT 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host "  + $($APIS.Count) APIs habilitadas" }
else { Write-Host "  ! fallo al habilitar APIs" }

# ---------------------------------------------------------------- 1. Datasets
Titulo "1. Datasets (region $LOCATION)"

foreach ($ds in $DATASETS) {
    $existe = bq --project_id=$PROJECT ls -d --format=json 2>$null | Out-String
    if ($existe -match "`"$ds`"") {
        Write-Host "  = $ds ya existe"
        continue
    }
    bq --project_id=$PROJECT mk --dataset --location=$LOCATION "${PROJECT}:${ds}" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "  + $ds creado" }
    else { Write-Host "  ! $ds FALLO" }
}

# -------------------------------------------------------- 2. Service accounts
Titulo "2. Service accounts"

foreach ($c in $CUENTAS) {
    $correo = "$($c.id)@${PROJECT}.iam.gserviceaccount.com"
    gcloud iam service-accounts describe $correo --project=$PROJECT 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  = $($c.id) ya existe"
        continue
    }
    gcloud iam service-accounts create $c.id --project=$PROJECT `
        --display-name=$c.id --description=$c.desc 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "  + $($c.id) creada" }
    else { Write-Host "  ! $($c.id) FALLO" }
}

# ------------------------------------------------- 3. Permiso para correr jobs
Titulo "3. bigquery.jobUser a nivel proyecto"

# jobUser permite EJECUTAR consultas, no leer datos. El acceso a los datos
# se controla por dataset, abajo. Sin esto, ninguna cuenta puede correr nada.
foreach ($c in $CUENTAS) {
    $correo = "$($c.id)@${PROJECT}.iam.gserviceaccount.com"
    gcloud projects add-iam-policy-binding $PROJECT `
        --member="serviceAccount:$correo" `
        --role="roles/bigquery.jobUser" `
        --condition=None 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "  + jobUser -> $($c.id)" }
    else { Write-Host "  ! jobUser -> $($c.id) FALLO" }
}

# ----------------------------------------------------- 4. Permisos por dataset
Titulo "4. Acceso a datos, dataset por dataset"

# `bq add-iam-policy-binding -d` y `get-iam-policy` sobre datasets responden
# "This feature requires allowlisting": no estan disponibles en general.
# La via que si funciona es la clasica: leer el dataset como JSON, agregar
# entradas al arreglo `access`, y escribirlo de vuelta con `update --source`.
#
# Ese arreglo usa los roles heredados de BigQuery, no los de IAM:
#     READER = dataViewer     WRITER = dataEditor     OWNER = dataOwner

$ROL_LEGACY = @{
    "roles/bigquery.dataViewer" = "READER"
    "roles/bigquery.dataEditor" = "WRITER"
    "roles/bigquery.dataOwner"  = "OWNER"
}

# Se agrupa por dataset para hacer una sola lectura-escritura por cada uno.
# Hacerlo permiso por permiso seria leer y escribir el mismo dataset cinco
# veces, y la ultima escritura pisaria a las anteriores.
foreach ($ds in $DATASETS) {
    $delDataset = $PERMISOS | Where-Object { $_.dataset -eq $ds }
    if (-not $delDataset) {
        Write-Host "  = $ds sin permisos que agregar"
        continue
    }

    $json = bq --project_id=$PROJECT show --format=prettyjson "${PROJECT}:${ds}" | Out-String
    $obj = $json | ConvertFrom-Json
    $acceso = [System.Collections.ArrayList]@($obj.access)
    $cambios = 0

    foreach ($p in $delDataset) {
        $correo = "$($p.sa)@${PROJECT}.iam.gserviceaccount.com"
        $legacy = $ROL_LEGACY[$p.rol]

        $ya = $acceso | Where-Object { $_.userByEmail -eq $correo -and $_.role -eq $legacy }
        if ($ya) {
            Write-Host "  = $($ds.PadRight(16)) $legacy -> $($p.sa)"
            continue
        }

        [void]$acceso.Add([PSCustomObject]@{ role = $legacy; userByEmail = $correo })
        Write-Host "  + $($ds.PadRight(16)) $legacy -> $($p.sa)"
        $cambios++
    }

    if ($cambios -eq 0) { continue }

    # Sin BOM: bq no lo tolera, y Out-File -Encoding utf8 en PS 5.1 lo mete.
    $tmp = [System.IO.Path]::GetTempFileName()
    $cuerpo = [PSCustomObject]@{ access = $acceso } | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($tmp, $cuerpo, (New-Object System.Text.UTF8Encoding($false)))

    bq --project_id=$PROJECT update --source $tmp "${PROJECT}:${ds}" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "  ! $ds FALLO al escribir" }
    Remove-Item $tmp -Force
}

# ------------------------------------------------------------ 5. Verificación
Titulo "5. Estado final"

Write-Host "Datasets:"
bq --project_id=$PROJECT ls -d

Write-Host ""
Write-Host "Accesos por dataset (solo service accounts de nacelab):"
foreach ($ds in $DATASETS) {
    Write-Host "  $ds"
    $obj = (bq --project_id=$PROJECT show --format=prettyjson "${PROJECT}:${ds}" | Out-String | ConvertFrom-Json)
    $obj.access | Where-Object { $_.userByEmail -like "sa-*" } | ForEach-Object {
        Write-Host "    $($_.role.PadRight(8)) $($_.userByEmail.Split('@')[0])"
    }
}

# ----------------------------- 6. Quitar Editor a la cuenta por defecto de GCE
Titulo "6. Cuenta por defecto de Compute"

# GCP crea esta cuenta sola y le da roles/editor sobre TODO el proyecto:
# escritura en los tres datasets. Cloud Run la usa por defecto si no le
# indicas otra, asi que basta un despliegue distraido para que el sitio
# publico corra con permisos de escritura sobre bronze.
#
# Quitarle Editor hace que ese despliegue falle en vez de funcionar de mas.
# Fallar ruidosamente es el comportamiento correcto aqui.
#
# Es reversible: se vuelve a otorgar con add-iam-policy-binding.

$COMPUTE_SA = "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

$roles = gcloud projects get-iam-policy $PROJECT --flatten="bindings[].members" `
    --filter="bindings.members:${COMPUTE_SA}" `
    --format="value(bindings.role)" 2>$null

if ($roles -match "roles/editor") {
    gcloud projects remove-iam-policy-binding $PROJECT `
        --member="serviceAccount:${COMPUTE_SA}" `
        --role="roles/editor" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "  - roles/editor retirado" }
    else { Write-Host "  ! no se pudo retirar roles/editor" }
} else {
    Write-Host "  = ya no tiene roles/editor"
}

Write-Host ""
Write-Host "Roles que le quedan (lo ideal es ninguno):"
gcloud projects get-iam-policy $PROJECT --flatten="bindings[].members" `
    --filter="bindings.members:${COMPUTE_SA}" `
    --format="value(bindings.role)" 2>$null

# ---------------------------------------------- 7. Suplantacion para local
Titulo "7. Permitir suplantar las service accounts desde tu maquina"

# El usuario es Owner del proyecto, asi que correr la ingesta con sus propias
# credenciales funcionaria aunque los permisos de sa-ingest estuvieran mal.
# Eso deja el diseno sin probar hasta el despliegue.
#
# Con serviceAccountTokenCreator, el usuario pide un token temporal de la SA
# y corre CON SUS PERMISOS REALES. Sigue sin haber llaves descargadas: el
# token dura minutos y lo emite Google contra la identidad del usuario.

$USUARIO = "jn.dataworks@gmail.com"

foreach ($c in $CUENTAS) {
    $correo = "$($c.id)@${PROJECT}.iam.gserviceaccount.com"
    gcloud iam service-accounts add-iam-policy-binding $correo `
        --project=$PROJECT `
        --member="user:${USUARIO}" `
        --role="roles/iam.serviceAccountTokenCreator" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "  + puede suplantar a $($c.id)" }
    else { Write-Host "  ! fallo el permiso sobre $($c.id)" }
}
