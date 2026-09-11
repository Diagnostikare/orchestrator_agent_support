# Deploy a produccion (`architecture-production`)

Verificado contra `architecture-production` el 2026-09-11. Permisos revisados
de nuevo el 2026-09-11 despues de una primera concesion: ver *Por que se
necesita admin*.

Este documento es para quien tenga admin en `architecture-production`. El
README documenta el deploy a **beta**; produccion es otro proyecto y le faltan
piezas de infra que no se pueden crear con permisos de desarrollador.

## Contexto en una linea

El agente `Soporte Diagnostikare` (router por `session_state.task` +
`support_ticket_classifier`) vive solo en beta. Produccion nunca lo tuvo: hoy
ahi solo esta `cora-support-assistant` (`6733783592209481728`, del 2026-05-05),
el agente viejo que no conoce `task` y por eso nunca clasifica. Esto es un
**primer deploy**, no un redeploy.

## Decision tomada: engine nuevo, no sobrescribir

Se despliega un reasoningEngine **nuevo** en produccion y se deja
`cora-support-assistant` intacto. Motivo: `CORA_SUPPORT_AGENT_ID` la comparte
el chat de la PWA (`StreamQueriesController`), asi que sobrescribir el engine
viejo mueve todo el chat de produccion en caliente, sin posibilidad de validar
antes. Con un engine nuevo el cutover es un cambio de variable y el rollback
tambien.

## Estado verificado de `architecture-production`

Proyecto numero **642614916255**.

| Cosa | Estado |
| --- | --- |
| `aiplatform.googleapis.com` | habilitada |
| `discoveryengine.googleapis.com` | habilitada |
| reasoningEngine del agente nuevo | no existe (es lo que se va a crear) |
| `gs://architecture-production-orquestor-support-docs` | **creado**, con `sla_dxkare_v2.0.html` copiado de beta |
| dataStore `orquestor-support-collection_documents` | **falta** (paso 1) |
| app de busqueda `orquestor-support-search` | **falta** (paso 2) |
| IAM del service agent sobre el dataStore | **falta** (paso 4) |

Engines ya existentes en produccion, que no hay que tocar:
`cora-support-assistant`, `cora-psychology`, `cora-nutrition`, `saludgs-agent`.

## Por que se necesita admin

Permisos de `axel.tinoco@diagnostikare.com` en `architecture-production`,
medidos con `testIamPermissions` y confirmados contra la API:

| Permiso | Estado |
| --- | --- |
| `aiplatform.reasoningEngines.create` | si |
| `storage.buckets.create` | si |
| `serviceusage.services.use` | si — concedido el 2026-09-11 |
| `discoveryengine.dataStores.create` / `.get` / `.list` | **no** |
| `discoveryengine.engines.create` | **no** |
| `discoveryengine.documents.import` | **no** |
| `resourcemanager.projects.setIamPolicy` | **no** |

La concesion del 2026-09-11 fue solo `roles/serviceusage.serviceUsageConsumer`
(el paso 5, el "opcional"). Los pasos 1 a 4 siguen bloqueados: listar
dataStores, un `GET` al dataStore esperado y el `:search` que ejecuta la tool
devuelven los tres 403 por `discoveryengine.*` denegado.

Como el 403 dice "denied ... (or it may not exist)", desde esta cuenta **no se
puede saber si el dataStore ya existe**. Con permiso de lectura se resuelve en
un comando.

### Lo mas corto para desbloquear

Un solo rol habilita los pasos 1, 2 y 3 al desarrollador, y de paso el
mantenimiento de documentacion (que es el flujo recurrente):

```bash
gcloud projects add-iam-policy-binding architecture-production \
  --member="user:axel.tinoco@diagnostikare.com" \
  --role="roles/discoveryengine.admin"
```

Con eso, lo unico que queda para admin es el **paso 4**, que requiere
`setIamPolicy` y no se puede delegar.

### Que rol pedir, exactamente

"Admin" es ambiguo y aqui la diferencia cuesta una ronda de ida y vuelta:

| Rol | Pasos 1-3 | Paso 4 | Paso 7 |
| --- | --- | --- | --- |
| `roles/discoveryengine.admin` | si | no | si |
| `roles/editor` | si | **no** | si |
| `roles/owner` | si | si | si |
| `discoveryengine.admin` + `resourcemanager.projectIamAdmin` | si | si | si |

`roles/editor` —lo que se suele entender por "admin"— incluye Discovery Engine
y Storage pero **no puede modificar la politica IAM del proyecto**. Con Editor
se hacen los pasos 1 a 3 y el 4 sigue bloqueado.

Los pasos **6 y 9 no se resuelven con ningun rol de GCP**: viven en el Cloud
Run de core-api de produccion y son de backend.

---

## Paso 1. Crear el dataStore

Replica exacta del de beta (`GENERIC` / `SOLUTION_TYPE_SEARCH` /
`CONTENT_REQUIRED`, parsing digital por defecto). El ID **debe** ser
`orquestor-support-collection_documents`: es el valor que va en
`deploy/production.env` y sigue el patron del proyecto (`<agente>-collection_documents`).

```bash
curl -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "x-goog-user-project: architecture-production" \
  "https://discoveryengine.googleapis.com/v1/projects/architecture-production/locations/global/collections/default_collection/dataStores?dataStoreId=orquestor-support-collection_documents" \
  -d '{
    "displayName": "documents",
    "industryVertical": "GENERIC",
    "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
    "contentConfig": "CONTENT_REQUIRED"
  }'
```

## Paso 2. Crear la app de busqueda

No es opcional. `buscar_documentacion` pega contra
`dataStores/<id>/servingConfigs/default_search` (ver
`agent/tools/docs_search.py:_serving_config`), y ese servingConfig lo crea la
app, no el dataStore. Sin app, la tool responde error en cada llamada.

```bash
curl -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "x-goog-user-project: architecture-production" \
  "https://discoveryengine.googleapis.com/v1/projects/architecture-production/locations/global/collections/default_collection/engines?engineId=orquestor-support-search" \
  -d '{
    "displayName": "Search Engine App",
    "solutionType": "SOLUTION_TYPE_SEARCH",
    "industryVertical": "GENERIC",
    "dataStoreIds": ["orquestor-support-collection_documents"],
    "searchEngineConfig": {"searchTier": "SEARCH_TIER_ENTERPRISE"}
  }'
```

## Paso 3. Importar la documentacion

El bucket ya esta creado y con el documento dentro.

```bash
curl -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "x-goog-user-project: architecture-production" \
  "https://discoveryengine.googleapis.com/v1/projects/architecture-production/locations/global/collections/default_collection/dataStores/orquestor-support-collection_documents/branches/0/documents:import" \
  -d '{
    "gcsSource": {
      "inputUris": ["gs://architecture-production-orquestor-support-docs/*"],
      "dataSchema": "content"
    },
    "reconciliationMode": "INCREMENTAL"
  }'
```

Devuelve una operacion de larga duracion; la indexacion tarda unos minutos.

## Paso 4. Permiso del service agent

El agente desplegado **no** corre con las credenciales de quien despliega:
corre como el service agent de Agent Engine del proyecto. Ojo, el numero de
proyecto cambia respecto a beta, asi que **no** es el mismo principal que el
del README.

```bash
gcloud projects add-iam-policy-binding architecture-production \
  --member="serviceAccount:service-642614916255@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/discoveryengine.viewer"
```

Es idempotente. Verificar:

```bash
gcloud projects get-iam-policy architecture-production \
  --flatten="bindings[].members" \
  --filter="bindings.members:service-642614916255@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --format="value(bindings.role)"
```

**Este es el paso que falla en silencio.** Sin el rol, `buscar_documentacion`
no truena el turno: devuelve `status: "error"` en cada llamada y el agente
contesta que no pudo verificar la informacion. Se lee como un problema de
contenido o de prompt, no de permisos.

## Paso 5. Permisos para el desarrollador — HECHO (2026-09-11)

Ya concedido. Queda documentado porque es el unico de los cinco que se hizo,
y porque explica por que el resto sigue bloqueado.

```bash
gcloud projects add-iam-policy-binding architecture-production \
  --member="user:axel.tinoco@diagnostikare.com" \
  --role="roles/serviceusage.serviceUsageConsumer"
```

Con eso puede verificar el dataStore y reimportar documentos (que es el flujo
normal de "actualizar la documentacion", y no requiere deploy del agente).

## Paso 6. Variables que tiene que dar backend

Van en `deploy/production.env`, de donde `./deploy.sh production` arma el
`agent/.env` que `adk deploy` sube como environment del reasoningEngine. Lo que
falte ahi, falta en produccion.

| Variable | Valor en produccion |
| --- | --- |
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` |
| `GOOGLE_CLOUD_PROJECT` | `architecture-production` |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` |
| `DOCS_DATASTORE_STANDARD` | `orquestor-support-collection_documents` |
| `DOCS_DATASTORE_MEDICAL` | igual al anterior |
| `CORE_API_BASE_URL` | **falta**: host de core-api de produccion, sin slash final |
| `AGENT_API_SECRET` | no va en el archivo: sale del secreto `AGENT_API_SECRET` de Secret Manager de `architecture-production` |

`CORE_API_BASE_URL` esta vacio en `deploy/production.env` y el script se
detiene antes de subir nada mientras siga asi. El secreto no se escribe en
ningun archivo del repo: `deploy.sh` lo lee de Secret Manager en cada deploy, y
si no existe ahi, aborta. Lo que backend tiene que confirmar es que ese secreto
valga **lo mismo** que el `AGENT_API_SECRET` del Cloud Run de core-api de
produccion: si no coincide, el header `X-Agent-Secret` no valida, todo el CRUD
de tickets responde 401 y el agente le dice al usuario que no puede registrar
su reporte.

`GOOGLE_CLOUD_LOCATION` es `us-central1` porque `AgentRegistry` en core-api
arma la URL con esa region hardcodeada. No afecta al modelo: ese esta anclado
a `global` en `agent/models.py`.

## Paso 7. Deploy

Con los pasos 1-6 hechos:

```bash
./deploy.sh production --dry-run   # revisar a donde va antes de nada
./deploy.sh production
```

Corre los 83 tests, genera el `agent/.env` de produccion y despliega **sin**
`--agent_engine_id` (porque `DEPLOY_ENGINE_ID` esta vacio en
`deploy/production.env`), que es justo lo que se quiere: un engine nuevo, sin
tocar `cora-support-assistant`. El resumen previo lo dice explicitamente y pide
escribir `production` para confirmar. Equivale a:

```bash
.venv/bin/adk deploy agent_engine \
  --project=architecture-production \
  --region=us-central1 \
  --display_name="Soporte Diagnostikare" \
  --otel_to_cloud \
  agent
```

El ultimo argumento es `agent`, el directorio del paquete (donde viven `.env`,
`requirements.txt` y el `root_agent`), no la raiz del repo. Apuntando a la
raiz, `adk` genera un `requirements.txt` con solo `google-adk` y el import de
`tools/docs_search.py` falla en runtime.

Tarda varios minutos. Al terminar imprime el resource name del engine nuevo.
Ese ID va en dos lados: en `DEPLOY_ENGINE_ID` de `deploy/production.env` (para
que el siguiente deploy actualice esta instancia en vez de crear otra) y en el
paso 9.

## Paso 8. Verificar antes del cutover

Reemplazar `<ENGINE_NUEVO>` por el ID del paso 7.

```bash
# Estado de la instancia
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/architecture-production/locations/us-central1/reasoningEngines/<ENGINE_NUEVO>" \
  | python3 -m json.tool
```

Y un turno real end-to-end (ver el detalle de los curl de sesion y
`streamQuery` en la seccion *Verificar* del README, cambiando proyecto y
engine). Lo que hay que confirmar:

- Una pregunta de producto **cita documentacion** y no responde "no pude
  verificar" — eso valida los pasos 1 a 4 de una sola vez.
- Con `session_state.task` de clasificacion, la respuesta trae
  `agent_output` y no viene con `author: support_standard`.

El flujo completo de tickets conviene probarlo en beta (servicio `core-api`,
rama `develop`), que es el unico ambiente con `WHATSAPP_FLOW_TOKEN_SECRET` y
con el clasificador ya funcionando.

## Paso 9. Cutover (backend)

En core-api de produccion:

```
CORA_SUPPORT_AGENT_ID = <ENGINE_NUEVO>
```

Dos advertencias:

- Esa variable **la comparte el chat de la PWA** (`StreamQueriesController`).
  Cambiarla mueve tambien el chat de produccion al agente nuevo. Es lo
  correcto —el nuevo trae `standard`, `medical` y el clasificador— pero es un
  cambio de comportamiento, no solo de configuracion.
- `STACK_ID` debe quedar en el valor de produccion, porque de ahi sale la URL
  con la que core-api arma el llamado al engine.

## Rollback

Agent Engine no versiona el codigo desplegado, pero con engine nuevo el
rollback no es un redeploy: es devolver `CORA_SUPPORT_AGENT_ID` a
`6733783592209481728` (`cora-support-assistant`). Instantaneo y sin tocar el
agente.

Para volver a una version anterior del codigo del agente nuevo, si: se
redespliega el commit anterior sobre el mismo `--agent_engine_id`. Las
sesiones vivas sobreviven al redeploy (el session service es de Agent Engine,
no del contenedor).
