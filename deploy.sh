#!/usr/bin/env bash
#
# Deploy del agente a un proyecto de GCP.
#
#   ./deploy.sh beta                 # tests + deploy a beta
#   ./deploy.sh production           # idem, a produccion
#   ./deploy.sh beta --env-only      # solo regenera agent/.env (para `adk web`)
#   ./deploy.sh beta --dry-run       # muestra el comando sin ejecutarlo
#   ./deploy.sh beta --skip-tests    # omite pytest
#   ./deploy.sh beta --yes           # sin confirmacion interactiva
#
# La config de cada ambiente vive en deploy/<ambiente>.env (versionado, sin
# secretos). El AGENT_API_SECRET se resuelve desde Secret Manager del proyecto
# destino en tiempo de deploy y se escribe en agent/.env, que es lo que
# `adk deploy` sube como environment del reasoningEngine.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

ADK=".venv/bin/adk"
PYTEST=".venv/bin/pytest"
AGENT_ENV="agent/.env"

die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
info() { printf '  %s\n' "$*"; }

# --- argumentos -------------------------------------------------------------

ENVIRONMENT=""
ENV_ONLY=0; SKIP_TESTS=0; DRY_RUN=0; ASSUME_YES=0

for arg in "$@"; do
  case "$arg" in
    --env-only)   ENV_ONLY=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    --dry-run)    DRY_RUN=1 ;;
    --yes|-y)     ASSUME_YES=1 ;;
    -h|--help)    sed -n '2,20p' "$0" | sed 's/^# \?//'; exit 0 ;;
    -*)           die "opcion desconocida: $arg" ;;
    *)            [ -n "$ENVIRONMENT" ] && die "solo un ambiente a la vez"
                  ENVIRONMENT="$arg" ;;
  esac
done

if [ -z "$ENVIRONMENT" ]; then
  echo "uso: ./deploy.sh <ambiente> [--env-only|--dry-run|--skip-tests|--yes]" >&2
  echo "ambientes disponibles:" >&2
  for f in deploy/*.env; do echo "  $(basename "$f" .env)" >&2; done
  exit 2
fi

CONFIG="deploy/${ENVIRONMENT}.env"
[ -f "$CONFIG" ] || die "no existe $CONFIG"

# --- cargar config ----------------------------------------------------------

DEPLOY_PROJECT=""; DEPLOY_REGION=""; DEPLOY_DISPLAY_NAME=""
DEPLOY_ENGINE_ID=""; DEPLOY_SECRET_AGENT_API=""
AGENT_VARS=()

while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|\#*) continue ;; esac
  [[ "$line" == *=* ]] || die "linea invalida en $CONFIG: $line"
  key="${line%%=*}"
  value="${line#*=}"
  case "$key" in
    DEPLOY_*) printf -v "$key" '%s' "$value" ;;
    *)        AGENT_VARS+=("${key}=${value}") ;;
  esac
done < "$CONFIG"

get_agent_var() {
  local k="$1" pair
  for pair in "${AGENT_VARS[@]}"; do
    [ "${pair%%=*}" = "$k" ] && { printf '%s' "${pair#*=}"; return; }
  done
}

# --- validaciones -----------------------------------------------------------

[ -n "$DEPLOY_PROJECT" ] || die "falta DEPLOY_PROJECT en $CONFIG"
[ -n "$DEPLOY_REGION" ]  || die "falta DEPLOY_REGION en $CONFIG"

for required in GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_LOCATION DOCS_DATASTORE_STANDARD CORE_API_BASE_URL; do
  [ -n "$(get_agent_var "$required")" ] \
    || die "$required esta vacio en $CONFIG. Sin el, el agente despliega roto."
done

# GOOGLE_CLOUD_PROJECT y --project apuntando a proyectos distintos es un deploy
# que arranca hablandole a la infra equivocada.
env_project="$(get_agent_var GOOGLE_CLOUD_PROJECT)"
[ "$env_project" = "$DEPLOY_PROJECT" ] \
  || die "GOOGLE_CLOUD_PROJECT ($env_project) != DEPLOY_PROJECT ($DEPLOY_PROJECT) en $CONFIG"

command -v gcloud >/dev/null || die "gcloud no esta instalado"
[ -x "$ADK" ] || die "falta $ADK — corre: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"

# --- secreto ----------------------------------------------------------------

AGENT_API_SECRET=""
if [ -n "$DEPLOY_SECRET_AGENT_API" ]; then
  echo "Leyendo $DEPLOY_SECRET_AGENT_API de Secret Manager ($DEPLOY_PROJECT)..."
  AGENT_API_SECRET="$(gcloud secrets versions access latest \
      --secret="$DEPLOY_SECRET_AGENT_API" --project="$DEPLOY_PROJECT" 2>/dev/null)" \
    || die "no se pudo leer el secreto '$DEPLOY_SECRET_AGENT_API' en $DEPLOY_PROJECT.
       Debe existir y valer lo mismo que AGENT_API_SECRET en el Cloud Run de
       core-api de ese ambiente; si no coincide, el header X-Agent-Secret no
       valida y todo el CRUD de tickets responde 401."
  [ -n "$AGENT_API_SECRET" ] || die "el secreto '$DEPLOY_SECRET_AGENT_API' esta vacio"
fi

# --- generar agent/.env -----------------------------------------------------

if [ -f "$AGENT_ENV" ]; then
  cp "$AGENT_ENV" "${AGENT_ENV}.bak"
  info "respaldo del anterior en ${AGENT_ENV}.bak"
fi

{
  echo "# ARCHIVO GENERADO por ./deploy.sh ${ENVIRONMENT} — no editar a mano."
  echo "# Fuente: ${CONFIG} (ahi estan los comentarios y ahi se hacen los cambios)."
  echo "# Ambiente: ${ENVIRONMENT}    Proyecto: ${DEPLOY_PROJECT}"
  echo
  for pair in "${AGENT_VARS[@]}"; do
    key="${pair%%=*}"; value="${pair#*=}"
    # el slash final es inofensivo (_config() hace rstrip) pero ensucia los logs
    [ "$key" = "CORE_API_BASE_URL" ] && value="${value%/}"
    echo "${key}=${value}"
  done
  [ -n "$AGENT_API_SECRET" ] && echo "AGENT_API_SECRET=${AGENT_API_SECRET}"
} > "$AGENT_ENV"
chmod 600 "$AGENT_ENV"
info "escrito $AGENT_ENV para '$ENVIRONMENT'"

if [ "$ENV_ONLY" = 1 ]; then
  echo
  echo "Listo. agent/.env apunta a '$ENVIRONMENT'. No se desplego nada."
  exit 0
fi

# --- tests ------------------------------------------------------------------

if [ "$SKIP_TESTS" = 1 ]; then
  info "pytest omitido (--skip-tests)"
else
  echo
  echo "Corriendo pytest..."
  "$PYTEST" -q || die "los tests fallaron; no se despliega"
fi

# --- comando de deploy ------------------------------------------------------

# El ultimo argumento es `agent`, el directorio del paquete, NO la raiz del
# repo: apuntando a la raiz, adk genera un requirements.txt con solo google-adk
# y el import de tools/docs_search.py revienta en runtime.
cmd=("$ADK" deploy agent_engine
     "--project=$DEPLOY_PROJECT"
     "--region=$DEPLOY_REGION"
     "--display_name=$DEPLOY_DISPLAY_NAME"
     --otel_to_cloud)
[ -n "$DEPLOY_ENGINE_ID" ] && cmd+=("--agent_engine_id=$DEPLOY_ENGINE_ID")
cmd+=(agent)

echo
echo "Ambiente : $ENVIRONMENT"
echo "Proyecto : $DEPLOY_PROJECT ($DEPLOY_REGION)"
if [ -n "$DEPLOY_ENGINE_ID" ]; then
  echo "Engine   : ACTUALIZA $DEPLOY_ENGINE_ID"
else
  echo "Engine   : CREA UNO NUEVO (DEPLOY_ENGINE_ID vacio en $CONFIG)."
  echo "           core-api seguira apuntando al viejo hasta el cutover manual."
fi
echo "Core API : $(get_agent_var CORE_API_BASE_URL)"
echo
printf '%q ' "${cmd[@]}"; echo

if [ "$DRY_RUN" = 1 ]; then
  echo
  echo "--dry-run: no se ejecuto nada."
  exit 0
fi

if [ "$ASSUME_YES" != 1 ]; then
  echo
  read -r -p "Escribe el nombre del ambiente para confirmar: " answer
  [ "$answer" = "$ENVIRONMENT" ] || die "cancelado"
fi

echo
exec "${cmd[@]}"
