# orquestor_support

Agente de soporte de Diagnostikare, construido con [Google ADK](https://google.github.io/adk-docs/)
y desplegado en **Vertex AI Agent Engine**. No se expone al usuario final:
lo consume `core-api`, que es quien crea la sesion y siembra el contexto.

```
cliente -> core-api -> Vertex AI Agent Engine -> este agente
             sessions_controller       POST .../sessions      {user_id, session_state}
             stream_queries_controller POST ...:streamQuery   {message, user_id, session_id}
```

El perfil y la categoria del cliente llegan **sembrados en `session_state`**
(opcion B de la arquitectura): para leer al usuario, el agente nunca llama de
vuelta a core-api. La excepcion son los tickets, que no existen cuando la
sesion arranca — ver *Tickets* mas abajo.

## Arquitectura

```
CategoryRouter  (support_orquestador)   router determinista, sin LLM
├── task == "ticket_classification" ──> support_ticket_classifier   (JSON estructurado)
└── si no, por profile.category
    ├── "standard" ─────────────────── support_standard             (conversacion)
    ├── "medical"  ─────────────────── support_medical              (conversacion)
    └── ausente / desconocida ───────> support_standard  (fallback, con WARNING)
```

| Archivo | Responsabilidad |
| --- | --- |
| `agent/agent.py` | `root_agent`: mapas `ROUTES` / `TASK_ROUTES` y `FALLBACK` |
| `agent/router.py` | `CategoryRouter`: enruta por `state`, sin gastar un turno de LLM |
| `agent/profile.py` | Contrato de lectura de `session_state` (`profile`, `task`) |
| `agent/sub_agents.py` | Agentes conversacionales `standard` y `medical` |
| `agent/ticket.py` | Clasificador de tickets con `output_schema` -> `agent_output` |
| `agent/models.py` | `GlobalGemini`: modelo anclado a la location `global` |
| `agent/tools/docs_search.py` | `buscar_documentacion` sobre Vertex AI Search |
| `agent/tools/support_tickets.py` | Tickets contra core-api: consultar, ver y crear |

Decisiones no obvias (por que un router a mano y no delegacion LLM, por que
una function tool y no `VertexAiSearchTool`, por que segments y no answers)
estan documentadas en el docstring de cada modulo.

### Routing determinista

La delegacion nativa de ADK es LLM-driven. Aqui no hay nada que inferir —la
categoria ya viene en el state—, asi que rutear con el modelo solo sumaria
latencia, tokens y una forma nueva de fallar. `resolve_target_name()` es pura
y se testea sin tocar el modelo.

### Clasificacion de tickets

Cuando `session_state["task"] == "ticket_classification"`, el mensaje es un
ticket raw y la respuesta es **solo** el JSON de `TicketClassification`
(`enhanced_subject`, `enhanced_body`, `classification`, `priority`), que
core-api persiste en la columna `agent_output` (jsonb) y usa para abrir el
issue en GitHub. La prioridad se decide leyendo la matriz de severidad del SLA
desde la documentacion, no de memoria.

### Fundamentacion

Ambos agentes conversacionales y el clasificador comparten
`buscar_documentacion`, que consulta el dataStore de Discovery Engine que
corresponde a la categoria. La busqueda se ejecuta client-side a proposito:
asi queda como `tool_use` + `tool_response` y es auditable por `adk eval`.

### Tickets: la unica llamada de vuelta a core-api

El perfil llega sembrado en `session_state` (opcion B) porque core-api ya lo
tiene cuando abre la sesion. Un ticket no existe todavia cuando la sesion
arranca —lo crea la conversacion—, asi que no hay nada que sembrar: para eso
si hay tres llamadas HTTP contra
`/api/v1/agents/support_tickets`, autenticadas con `X-Agent-Secret`.

| Tool | Endpoint | Para que |
| --- | --- | --- |
| `consultar_mis_tickets` | `GET /` filtrado por reporter | "como va lo que reporte" |
| `ver_ticket` | `GET /:id` | detalle de un ticket que el usuario menciono |
| `crear_ticket` | `POST /` | abrir uno cuando la documentacion no alcanzo |

`PATCH` y `DELETE` existen en el CRUD y **no** se exponen a proposito: un
modelo no borra tickets ni reescribe el asunto de uno ya clasificado.

**La identidad nunca es un argumento de la tool**: sale del `session_state`.
Si el modelo pudiera pasar un `reporter_id` cualquiera, "muestrame los tickets
del usuario 12" leeria los de otra persona. Por la misma razon `ver_ticket`
comprueba que el ticket devuelto sea del reporter de la sesion — el filtro de
verdad le toca a core-api, esto es la segunda linea.

Sin dependencias nuevas (`urllib.request` de la stdlib): el runtime desplegado
es exactamente `agent/requirements.txt` y no vale sumarle un cliente HTTP por
tres llamadas.

## Setup local

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

`agent/.env` (no versionado) con:

| Variable | Valor | Nota |
| --- | --- | --- |
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` | |
| `GOOGLE_CLOUD_PROJECT` | `architecture-beta` | |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | region del reasoningEngine; el modelo va anclado a `global` en `models.py` |
| `DOCS_DATASTORE_STANDARD` | `orquestor-support-collection_documents` | |
| `DOCS_DATASTORE_MEDICAL` | igual al anterior | hoy no hay docs restringidas a medicos |
| `DATASTORE_LOCATION` | `global` (default) | opcional |
| `CORE_API_BASE_URL` | `https://<host de core-api>` | sin slash final; sin esto las tools de tickets responden `no_configurado` |
| `AGENT_API_SECRET` | el mismo valor que en core-api | shared secret del header `X-Agent-Secret` |

Credenciales:

```bash
gcloud auth application-default login
gcloud config set project architecture-beta
```

Probar en el navegador:

```bash
.venv/bin/adk web .
```

En `adk web` el state arranca vacio, asi que cae al fallback `standard`. Para
probar `medical` o el clasificador de tickets hay que sembrar el state de la
sesion (`profile.category` / `task`) desde la UI.

## Tests y evals

```bash
.venv/bin/pytest                 # routing y parseo de perfil: deterministas, sin LLM
./evals/run.sh                   # LLM-as-judge: fundamentacion y respeto de alcance
```

`evals/run.sh` pisa `GOOGLE_CLOUD_LOCATION=global` porque el juez de `adk eval`
toma la location del ambiente y con `us-central1` tira 404 silencioso
(los casos quedan en `NOT_EVALUATED`, sin un solo FAILED visible).

## Deploy

Se despliega a **Vertex AI Agent Engine** con `adk deploy agent_engine`. Un
deploy normal es actualizar la instancia que ya existe, no crear una nueva.

Instancia en uso (la que core-api tiene configurada):

```
projects/77057647019/locations/us-central1/reasoningEngines/6890865320911699968
```

### 1. Prerrequisitos (una sola vez por maquina)

```bash
gcloud auth application-default login
gcloud config set project architecture-beta
```

- APIs habilitadas en el proyecto: `aiplatform.googleapis.com` y
  `discoveryengine.googleapis.com`.
- `agent/.env` presente y completo (ver la tabla de *Setup local*).
  `adk deploy` lo lee **del directorio del agente** y sube esas variables como
  environment del reasoningEngine: lo que falte ahi, falta en produccion.
- El service agent de Agent Engine con permiso de lectura sobre el dataStore
  (ver *Permisos del service agent*, mas abajo).

### 2. Checks antes de subir

```bash
.venv/bin/pytest        # routing: deterministas, segundos
./evals/run.sh          # LLM-as-judge: fundamentacion y alcance (opcional, cuesta tokens)
```

### 3. Deploy (actualizar la instancia existente)

```bash
.venv/bin/adk deploy agent_engine \
  --project=architecture-beta \
  --region=us-central1 \
  --agent_engine_id=6890865320911699968 \
  --display_name="Soporte Diagnostikare" \
  --otel_to_cloud \
  agent
```

Tres cosas que hay que respetar en ese comando:

- **`--agent_engine_id` no es opcional.** Sin el, `adk deploy` crea un
  reasoningEngine nuevo, el deploy "funciona", y core-api sigue hablandole al
  viejo — o sea, el cambio no llega a produccion.
- **`--region=us-central1`.** `AgentRegistry` en core-api arma la URL con esa
  region hardcodeada. No afecta al modelo: ese esta anclado a `global` en
  `agent/models.py`.
- **El ultimo argumento es `agent`**, el directorio del paquete (donde viven
  `.env`, `requirements.txt` y el `root_agent`), no la raiz del repo. Apuntando
  a la raiz, `adk` genera un requirements con solo `google-adk` y el import de
  `tools/docs_search.py` falla en runtime.

Tarda varios minutos: construye la imagen y espera a que la instancia quede
activa. Al terminar imprime el resource name del engine.

### 4. Verificar

```bash
# La instancia y su estado
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/architecture-beta/locations/us-central1/reasoningEngines/6890865320911699968" \
  | python3 -m json.tool

# Un turno real end-to-end: crear sesion sembrando el state...
curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/77057647019/locations/us-central1/reasoningEngines/6890865320911699968/sessions" \
  -d '{"userId": "smoke-test", "sessionState": {"profile": {"category": "standard"}}}'

# ...y mandarle un mensaje (con el sessionId que devolvio el paso anterior)
curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/77057647019/locations/us-central1/reasoningEngines/6890865320911699968:streamQuery?alt=sse" \
  -d '{"classMethod": "async_stream_query", "input": {"user_id": "smoke-test", "session_id": "<SESSION_ID>", "message": "que incluye el servicio?"}}'
```

En la respuesta debe aparecer un `functionCall` a `buscar_documentacion`: si el
agente contesta sin llamar a la tool, o la tool devuelve `status: "error"`,
revisar los permisos del service agent.

Logs del engine:

```bash
gcloud logging read \
  'resource.type="aiplatform.googleapis.com/ReasoningEngine" AND resource.labels.reasoning_engine_id="6890865320911699968"' \
  --project=architecture-beta --limit=50 --freshness=1h
```

Ahi salen los WARNING del router (`sin categoria en session.state`,
`categoria %r desconocida`), que son la señal de que core-api dejo de sembrar
bien el state.

### Crear una instancia nueva

Solo para un ambiente nuevo, no para actualizar. Es el mismo comando **sin**
`--agent_engine_id`; imprime el ID nuevo, que hay que darle al equipo de
core-api para que lo configure:

```bash
.venv/bin/adk deploy agent_engine \
  --project=architecture-beta \
  --region=us-central1 \
  --display_name="Soporte Diagnostikare" \
  --description="Agente de soporte con routing por categoria de cliente" \
  --otel_to_cloud \
  agent
```

Listar todos los engines del proyecto (para recuperar un ID):

```bash
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/architecture-beta/locations/us-central1/reasoningEngines" \
  | python3 -m json.tool
```

(Tambien sirve `gcloud alpha ai reasoning-engines list`, pero requiere el
componente `alpha` instalado.)

### Permisos del service agent

El agente desplegado NO corre con tus credenciales: corre como el service
agent de Agent Engine del proyecto.

```
service-77057647019@gcp-sa-aiplatform-re.iam.gserviceaccount.com
```

`buscar_documentacion` ejecuta la busqueda client-side contra Discovery
Engine, asi que ese principal necesita leer el dataStore. Sin el rol, la tool
no truena el turno: devuelve `status: "error"` en cada llamada y el agente
contesta que no pudo verificar nada — se ve como un problema de contenido, no
de permisos.

```bash
gcloud projects add-iam-policy-binding architecture-beta \
  --member="serviceAccount:service-77057647019@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/discoveryengine.viewer"
```

Es idempotente: si el binding ya existe, no cambia nada. Verificar:

```bash
gcloud projects get-iam-policy architecture-beta \
  --flatten="bindings[].members" \
  --filter="bindings.members:service-77057647019@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --format="value(bindings.role)"
```

Un dataStore nuevo (por ejemplo, al separar la documentacion `medical`) hereda
este binding porque esta a nivel proyecto; si en cambio se otorga a nivel
dataStore, hay que repetirlo por cada uno.

### Rollback

Agent Engine no versiona el codigo desplegado: para volver atras se
redespliega el commit anterior sobre el mismo `--agent_engine_id`.

```bash
git checkout <COMMIT_ANTERIOR>
.venv/bin/adk deploy agent_engine --project=architecture-beta --region=us-central1 \
  --agent_engine_id=6890865320911699968 --display_name="Soporte Diagnostikare" \
  --otel_to_cloud agent
git checkout main
```

Las sesiones vivas sobreviven al redeploy (el session service es de Agent
Engine, no del contenedor).

### Que NO requiere deploy

- **Sumar o actualizar documentacion**: se sube el archivo al bucket
  `gs://architecture-beta-orquestor-support-docs` y se reimporta el dataStore.
  El agente no cambia.
- **Cambiar la matriz de severidad del SLA**: el agente la lee de la
  documentacion en cada clasificacion, asi que basta reimportar el documento.
  (En cambio el mapeo S1-S4 -> `low/medium/high` vive en el prompt de
  `agent/ticket.py`: eso si requiere deploy.)
- **Sumar una categoria de cliente** si requiere deploy: hay que agregar el
  `Agent` en `agent/sub_agents.py`, su entrada en `ROUTES`, y el dataStore en
  `DATASTORE_ENV_BY_CATEGORY` + `.env`.
