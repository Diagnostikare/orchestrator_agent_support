# Conectar core-api al clasificador de tickets

Instrucciones para el equipo de backend. Todo lo que sigue está verificado
contra el engine desplegado el 2026-08-27, no es de memoria.

---

## 0. Estado: listo de nuestro lado

El agente se redesplegó el **2026-08-27 a las 16:20** (hora local) sobre el
engine `6890865320911699968`, el que core-api ya tiene configurado. El
clasificador está arriba y verificado con un ticket real.

Prueba end-to-end, con `task: "ticket_classification"` sembrado en el
`sessionState`:

```
eventos: 3
  0 author=support_ticket_classifier  function_call  -> buscar_documentacion
  1 author=support_ticket_classifier  function_response
  2 author=support_ticket_classifier  text           -> el JSON
```

```json
{
  "enhanced_subject": "Solicitud de reagendamiento de asesoría marcada como no atendida",
  "enhanced_body": "### Reporte del Usuario\nEl usuario reporta que no recibió...",
  "classification": "scheduling",
  "priority": "low"
}
```

Las cuatro llaves, `JSON.parse` limpio. Pueden empezar a integrar.

Cómo confirman de su lado que están hablando con el clasificador y no con el
agente conversacional: en la respuesta, `author` debe decir
`support_ticket_classifier`. Si dice `support_standard`, el `sessionState` no
llegó (ver §2) — ya no es el deploy.

## 1. El contrato, en dos llamadas HTTP

No hay tools, ni webhook, ni SDK. Son dos POST a Vertex AI Agent Engine.

```
POST {BASE}/sessions          -> crea la sesión y siembra el contexto
POST {BASE}:streamQuery       -> manda el ticket y devuelve el JSON
```

```
BASE = https://us-central1-aiplatform.googleapis.com/v1
       /projects/77057647019/locations/us-central1
       /reasoningEngines/6890865320911699968
```

`us-central1` no es negociable: `AgentRegistry` la tiene hardcodeada. El
modelo corre en `global`, pero eso es interno del agente y no les afecta.

Auth: el mismo `fetch_access_token` de `Api::V1::Agents::BaseController`
(service account de `/gcp-config/key.json`, scope `cloud-platform`). No hace
falta nada nuevo.

---

## 2. Paso 1 — crear la sesión CON `sessionState`

Aquí está el cambio que sí les toca. Hoy `sessions_controller.rb:12` manda:

```ruby
req.body = { user_id: params[:user_id] }.to_json
```

Sin `sessionState`. El agente rutea por lo que encuentra en el state: sin
`task`, cae al agente conversacional `support_standard` y devuelve prosa. Es
la misma razón por la que hoy el chat nunca llega a `support_medical`.

Lo que hay que mandar:

```json
{
  "userId": "36",
  "sessionState": {
    "task": "ticket_classification",
    "profile": { "user_id": "36", "category": "standard" }
  }
}
```

- `task: "ticket_classification"` — **obligatorio**. Es lo único que separa un
  ticket de una conversación. Tiene precedencia sobre `profile.category`.
- `profile` — opcional para tickets (el clasificador no lo usa). Mándenlo
  igual: sirve para elegir el dataStore de documentación y para los logs.
- `userId` va en el body de la sesión y en el `input` del streamQuery, y
  **tienen que coincidir**.

Respuesta real (recortada). El `sessionState` se refleja de vuelta, úsenlo
para verificar que llegó:

```json
{
  "done": true,
  "response": {
    "name": ".../sessions/2242458397255401472",
    "sessionState": { "task": "ticket_classification", "profile": {...} },
    "userId": "smoke-ticket"
  }
}
```

El `session_id` es el último segmento de `response.name`. El
`extract_session_id` que ya tienen lo saca bien.

---

## 3. Paso 2 — mandar el ticket

```json
{
  "classMethod": "async_stream_query",
  "input": {
    "user_id": "36",
    "session_id": "2242458397255401472",
    "message": "<el ticket renderizado, ver §4>"
  }
}
```

`classMethod` es obligatorio. El `build_request` del
`StreamQueriesController` hoy no lo manda — funciona por default en el chat,
pero pónganlo explícito aquí.

**No usen el `StreamQueriesController` para esto.** Ese controller es SSE
hacia el navegador del usuario. La clasificación es server-to-server, corre
dentro de `SupportTickets::PushToGithubJob` y nadie está mirando el stream.
Va en un service nuevo (`SupportTickets::Classify`) que hace la llamada y
espera la respuesta completa.

### Cómo leer la respuesta

La respuesta son **objetos JSON separados por newline**, no SSE con prefijo
`data:`. Un ticket produce ~3 eventos:

| # | `author` | contenido |
|---|---|---|
| 0 | `support_ticket_classifier` | `function_call` → `buscar_documentacion` (lee el SLA) |
| 1 | `support_ticket_classifier` | `function_response` con los pasajes |
| 2 | `support_ticket_classifier` | `text` → **el JSON que buscan** |

Reglas para extraerlo:

1. Quédense con el **último** evento que traiga `content.parts[].text`. Los
   dos primeros no tienen `text` y hay que ignorarlos.
2. Ignoren `thought_signature` — viene en el mismo `part` que el texto y no es
   contenido.
3. `JSON.parse` de ese texto. El agente responde con `output_schema`, así que
   el JSON está garantizado por el modelo, no por una instrucción de prompt.

⚠️ **Cuidado con un bug que ya existe en `handle_chunk`:**

```ruby
parsed = JSON.parse(chunk) rescue nil
return unless parsed
```

`chunk` es lo que trae la red, no una línea completa: un objeto JSON puede
llegar partido en dos chunks y ese `rescue nil` lo descarta **en silencio**.
En el chat se nota como texto faltante; aquí perderían el JSON entero y
parecería que el agente no contestó. Acumulen en un buffer y partan por
newline, o junten todo el body y recorran las líneas al final — que es lo
más simple, porque aquí no necesitan streamear.

---

## 4. El formato del `message`

El agente recibe **un string**, no el JSON del ticket. Ármenlo en Ruby: es
testeable con un spec y evita meterle campos que no debe ver.

```
Asunto: {subject}
Canal: {channel}
Contacto: {contact_name} ({contact_id})
Motivo: {metadata["motivo"]}
Submotivo: {metadata["submotivo"]}      # omitir la línea si no viene
Sitio: {metadata["site_slug"]}
Evidencias adjuntas: {metadata["evidencia_count"]}

{body}
```

Ejemplo real (ticket #2):

```
Asunto: Algo pasó con mi asesoría — Se cortó la llamada
Canal: whatsapp
Contacto: Victor (+525539706542)
Motivo: asesoria
Submotivo: se_corto
Sitio: saludgs
Evidencias adjuntas: 0

Nunca recibi una llamada del doctor, estuve esperando y nada. acabo de ver
que mi asesoria se marco como no atendida, como puedo programar otra? O
reagendar ?
```

**No manden** `flow_token` (es un JWT con el teléfono dentro; no aporta y
mete PII al contexto del modelo), ni `github_item_*`, ni los timestamps, ni
el `id`.

Si cambian este formato, avísennos: el eval del agente
(`evals/ticket.evalset.json`) mide contra él, y si se desalinean estamos
evaluando un input que no existe.

---

## 5. Qué devuelve y dónde va

```json
{
  "enhanced_subject": "Reagendación de asesoría marcada como no atendida",
  "enhanced_body": "El usuario reporta que no recibió la llamada...",
  "classification": "scheduling",
  "priority": "low",
  "user_summary": "Ya registramos que no recibiste la llamada del médico..."
}
```

Cinco campos, siempre los cinco. Vocabularios cerrados:

- `classification`: `billing` | `scheduling` | `technical` | `other`
- `priority`: `low` | `medium` | `high`

### `user_summary`: el mismo ticket, para el otro lector (2026-09-07)

Los cuatro primeros campos los lee el **equipo de soporte** en el board:
`enhanced_body` es la nota de trabajo y tiene que conservar cada dato del raw,
incluidos los "Datos faltantes". `user_summary` lo lee el **usuario** en el
chat de la PWA, que es una persona sin contexto del producto: dos o tres frases
cálidas, en segunda persona, sin markdown, sin jerga (nada de S1-S4, SLA ni
"incidencia") y sin prometer plazos.

Están separados a propósito. Un solo texto de tono intermedio le baja precisión
al triage para que el paciente lo entienda, y eso lo paga el board.

Del lado de core-api hay que hacer **una cosa** para que llegue a la PWA: que
el serializer de `SupportTicket` exponga `agent_output["user_summary"]` como
`user_summary`, igual que ya hace con `enhanced_subject`/`enhanced_body`. Sin
eso el campo se persiste en el jsonb y nadie lo ve. Dos consumidores:

- La `SupportTicketCard` de la burbuja, para mostrarle al usuario lo que se
  registró en vez del body técnico.
- La tool `ver_ticket` del agente, que ya lo lee del `show` si viene y lo
  prefiere sobre `enhanced_body` cuando le cuenta el ticket al usuario. Si no
  viene —ticket viejo, o todavía sin clasificar— el agente lo resume él mismo.

`enhanced_body` sigue siendo el body del issue de GitHub: ahí no cambia nada.

Eso se guarda **verbatim** en `support_tickets.agent_output` (jsonb). Al ser
jsonb, si más adelante agregamos un campo no hay migración: aparece solo.

Del lado de core-api ya está todo escrito y esperando. En
`app/services/support_tickets/push_to_github.rb` el hueco es literal:

```ruby
def classify!
  # no-op until the agent is implemented
end
```

Se llena con lo que el propio comentario de arriba ya declara:

```ruby
@ticket.update!(agent_output: SupportTickets::Classify.call(ticket: @ticket))
```

Y de ahí para abajo no hay que tocar nada: `SupportTicket#enhanced_subject`,
`#enhanced_body` y `#classification` ya leen de `agent_output` con fallback al
raw, y `board_fields` ya mapea `priority` al single-select del board.

### Manejo de errores

El comentario del código ya fija la política y estamos de acuerdo: **nunca
bloquear el flujo**. Si el agente falla (timeout, 404, JSON inválido), dejen
`agent_output` en `NULL`, loguéenlo, y que el item se cree con el texto raw.
Los fallbacks del modelo hacen que todo lo de abajo siga funcionando.

Un `priority` que el campo del board no ofrezca se loguea y se salta, nunca
se levanta excepción — eso también ya está resuelto en `board_fields`.

---

## 6. Probarlo a mano, sin escribir código

```bash
BASE="https://us-central1-aiplatform.googleapis.com/v1/projects/77057647019/locations/us-central1/reasoningEngines/6890865320911699968"
TOKEN=$(gcloud auth print-access-token)

# 1) sesión con el state sembrado
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$BASE/sessions" \
  -d '{"userId":"smoke","sessionState":{"task":"ticket_classification"}}'

# 2) el ticket (con el sessionId del paso anterior)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "${BASE}:streamQuery?alt=sse" \
  -d '{"classMethod":"async_stream_query","input":{"user_id":"smoke","session_id":"<SESSION_ID>","message":"Asunto: prueba\nCanal: whatsapp\nContacto: Test (+520000000000)\nMotivo: asesoria\nSitio: saludgs\nEvidencias adjuntas: 0\n\nNo me llamo el doctor"}}'
```

En zsh, `${BASE}:streamQuery` con llaves: sin ellas, `$BASE:s` se come el
`:streamQuery` como modificador de historial y el curl pega contra la URL
base — da un 404 que parece del engine y no lo es.

### Checklist de diagnóstico

| Síntoma | Causa |
|---|---|
| `author: "support_standard"`, respuesta en prosa | no llegó `sessionState.task` (§2) |
| 404 en el `:streamQuery` | la URL perdió el `:streamQuery` (llaves en zsh) o el engine id está mal |
| `JSON.parse` truena | tomaron el primer evento en vez del último, o el chunk venía partido (§3) |
| `status: "error"` en el `function_response` | permisos del service agent sobre el dataStore — es nuestro, avísennos |
| respuesta vacía | `user_id` del streamQuery ≠ `userId` de la sesión |

---

## 7. Resumen de lo que le toca a cada quien

**Equipo del agente (nosotros)** — hecho

1. ~~Redesplegar el engine `6890865320911699968` con el clasificador.~~
   Desplegado y verificado el 2026-08-27 16:20.

**Equipo de backend (ustedes)** — hecho, salvo un punto

- ~~`SupportTickets::Classify` — service nuevo, server-to-server, sin SSE.~~
  Escrito sobre `feat/cora-support`, con specs. Ver §8.
- ~~`push_to_github.rb`: llenar `classify!`.~~ Hecho, con el fallback a texto
  raw que el propio comentario pedía.
- ~~Confirmarnos el formato final del `message` (§4).~~ Queda fijado en
  `SupportTickets::AgentPrompt`, con un spec que compara contra el bloque
  literal de §4.
- **Pendiente:** `sessions_controller`, aceptar y mandar `sessionState`. La
  clasificación ya no lo necesita (Classify abre su propia sesión), pero el
  chat sí: sin él nunca se llega a `support_medical`.

---

## 8. Lo que quedó escrito del lado de core-api

Rama `feat/cora-support`. Cuatro archivos nuevos y un hueco lleno:

| Archivo | Qué hace |
| --- | --- |
| `app/services/cora/client.rb` | Token del service account y URLs, vía `AgentRegistry` para no abrir una segunda fuente de verdad. Falla explícito si `CORA_SUPPORT_AGENT_ID` no está. |
| `app/services/support_tickets/agent_prompt.rb` | Renderiza el ticket como el string de §4. Sin `flow_token`, sin ids de GitHub. |
| `app/services/support_tickets/classify.rb` | Las dos llamadas HTTP. Junta el body y parsea NDJSON al final (también tolera `data:` por si algún día se usa `alt=sse`). |
| `push_to_github.rb#classify!` | `agent_output` o `NULL`, nunca una excepción hacia arriba. |

Decisiones que conviene que conozcan:

- **`user_id`**: el `reporter_id` del ticket, o `support-ticket-<id>` cuando el
  número es desconocido. Va idéntico en la sesión y en el `streamQuery`.
- **Vocabulario abierto en la lectura**: si `classification` o `priority` traen
  un valor fuera de la lista, se guarda **verbatim** y solo se loguea un
  warning. Lo que sí es fatal es que falte una de las cuatro llaves.
- **`author` inesperado**: si la respuesta viene de `support_standard`, se
  loguea con ese nombre antes de fallar — el `JSON::ParserError` a secas no
  decía nada y es el síntoma de §2.
- **No se reclasifica** un ticket que ya trae `agent_output`, así que un job
  reintentado no vuelve a pagar el agente.

⚠️ **Discrepancia de vocabulario detectada.** La factory de core-api
(`spec/factories/support_tickets.rb`) usa `classification: "prescriptions"`, que
no está en el enum que declara §5 (`billing | scheduling | technical | other`).
El clasificador no lo puede devolver hoy. O el agente amplía el vocabulario o
backend corrige la factory — pero uno de los dos está equivocado.

---

## 9. La otra direccion: el agente escribiendo tickets

Los commits `3541843` / `8bca66f` abrieron
`/api/v1/agents/support_tickets` con `X-Agent-Secret`. Ya esta consumido
desde `agent/tools/support_tickets.py`, y los agentes conversacionales
(`support_standard` y `support_medical`) lo tienen enchufado:

| Tool | Endpoint |
| --- | --- |
| `consultar_mis_tickets` | `GET /api/v1/agents/support_tickets?reporter_type=&reporter_id=` |
| `ver_ticket` | `GET /api/v1/agents/support_tickets/:id` |
| `crear_ticket` | `POST /api/v1/agents/support_tickets` |

`PATCH` y `DELETE` quedan sin exponer a proposito.

### Lo que necesitamos de ustedes

1. **`AGENT_API_SECRET` compartido.** Nosotros lo leemos de la misma variable,
   y va en `agent/.env` (de ahi sube al environment del reasoningEngine). Si
   los dos valores no coinciden, todo responde 401 y el agente le dice al
   usuario que no puede registrar su reporte. Pasenoslo por el canal de
   siempre, no por aqui.
2. **`CORE_API_BASE_URL`** por ambiente (beta y produccion). Sin slash final.
3. **`profile.contact_id` para el invitado.** La burbuja de la PWA le pide el
   telefono o el correo antes de empezar y lo manda en el `POST .../sessions`;
   siembrenlo como `profile.contact_id` **solo cuando no haya usuario
   autenticado** —con sesion la identidad ya la resuelven ustedes y un
   `contact_id` del cuerpo solo podria contradecirla—. Sin el, `SupportTicket`
   rechaza el ticket del invitado (`contact_id` es obligatorio cuando falta
   `reporter_type`) y el agente corta con `sin_perfil`.

   Es identidad **afirmada, no verificada**: nadie mando un OTP a ese numero.
   Por eso el agente la acepta para CREAR un ticket pero no para listarlos
   (`consultar_mis_tickets` y `ver_ticket` devuelven `requiere_sesion`): el
   `index` filtra por `contact_id`, y con una identidad que el cliente elige,
   escribir el telefono de otro seria leer sus reportes.

4. **`profile.reporter_type` en el `sessionState`.** Hoy sembramos `user_id` y
   `category`; para `create` hace falta saber si es `User` o `ApiUser`, que es
   lo que valida `SupportTicket::REPORTER_TYPES`. Asumimos `User` por default,
   asi que el chat de la PWA funciona sin cambios — pero BOA no, y el agente no
   lo puede adivinar.

### Un hueco de autorizacion que nos toca a ustedes

`show`, `update` y `destroy` cargan con `SupportTicket.find(params[:id])`, sin
filtrar por reporter. El shared secret autentica al **agente**, no al usuario
que esta conversando con el: con ese header, cualquier id es accesible.

Del lado del agente `ver_ticket` ya descarta un ticket cuyo `reporter` no sea
el de la sesion, y ninguna tool acepta la identidad como argumento del modelo
(sale del `session_state`). Pero eso es una mitigacion en el cliente: alcanza
para que el modelo no filtre datos por su cuenta, no para que el endpoint sea
seguro. El filtro de verdad va en el controller.

Lo mismo aplica al `index` sin filtros: hoy devuelve tickets de todos los
usuarios. Nosotros siempre mandamos `reporter_type` + `reporter_id`, pero nada
en el endpoint lo obliga.

---

## 10. Configuracion por ambiente (lo que le toca al backend)

Verificado contra `architecture-beta` el 2026-08-27.

### A que engine debe apuntar

```
CORA_SUPPORT_AGENT_ID = 6890865320911699968
```

Ese es `Soporte Diagnostikare`, el que tiene el router por `task` y el
`support_ticket_classifier`.

Hoy **los dos servicios de beta apuntan a otro**:

| Servicio | Rama | Engine configurado |
| --- | --- | --- |
| `core-api` | `develop` | `4243666832227041280` — `cora-support-assistant`, del 2026-05-05 |
| `core-api-stage` | `stage` | `4243666832227041280` — el mismo |

`cora-support-assistant` es el agente viejo: no conoce `session_state.task`, asi
que contesta como conversacion. El sintoma es el de §2 — `author:
support_standard`, `agent_output` en NULL y el item creado con el texto raw. No
falla ruidosamente, simplemente nunca clasifica.

⚠️ Esa variable la comparte el chat (`StreamQueriesController`). Cambiarla mueve
tambien el chat de beta al engine nuevo. Es lo correcto —el nuevo trae
`standard`, `medical` y el clasificador— pero es un cambio de comportamiento,
no solo de configuracion.

**En produccion no existe el clasificador todavia**: `architecture-production`
solo tiene `cora-support-assistant` (`6733783592209481728`, del 2026-05-06).
Esto se prueba en beta o no se prueba.

### Variables

| Variable | Valor | Si falta |
| --- | --- | --- |
| `CORA_SUPPORT_AGENT_ID` | `6890865320911699968` | clasifica el agente viejo: `agent_output` NULL |
| `STACK_ID` | `beta` (ya esta) | la URL se arma contra `architecture-production` |
| `AGENT_API_SECRET` | generar, mismo valor que en `agent/.env` | el CRUD del agente responde 401 |
| `WHATSAPP_FLOW_TOKEN_SECRET` | ya esta en `core-api`; **falta en `core-api-stage`** | `ENV.fetch` lanza `KeyError` al firmar el token: el Flow de soporte no abre |
| `GITHUB_ACCESS_TOKEN` | PAT o token de App con acceso a Projects | el push falla, el ticket queda en `pending_github_push` |
| `GITHUB_PROJECT_OWNER` | `Diagnostikare` | idem |
| `GITHUB_PROJECT_NUMBER` | el numero del board | idem |
| `GITHUB_PROJECT_INITIAL_STATUS` | opcional, default `To triage` | si el board no tiene una opcion con ese nombre exacto, el item se crea **sin** Status (se loguea, no truena) |

Del lado del agente, en `agent/.env` (que `adk deploy` sube como environment del
reasoningEngine): `CORE_API_BASE_URL` y el **mismo** `AGENT_API_SECRET`.

### Donde probar

En el servicio **`core-api`** de beta (rama `develop`), no en `core-api-stage`:
es el unico que ya tiene `WHATSAPP_FLOW_TOKEN_SECRET`, y sin eso el Flow —que
es la puerta de entrada del ticket— ni siquiera abre.

---

## 11. La sesión conversacional de la PWA (implementado, 2026-09-03)

Todo lo anterior es la sesión de **clasificación**, la que abre
`SupportTickets::Classify` para un ticket que ya existe. Esta sección es la
otra: la que abre el chat de la PWA, y que hasta ahora mandaba solo
`{ user_id }`.

El efecto de esa omisión no era cosmético. Sin `profile` en el `sessionState`:

- el router cae siempre a `support_standard` (por eso el chat nunca llegaba a
  `support_medical`), y
- `crear_ticket` responde `sin_perfil` y **corta antes de tocar core-api**: no
  hay a quién atribuir el ticket. Es decir, desde la PWA el agente no podía
  abrir un ticket, y la `SupportTicketCard` del front nunca podía dispararse.

### Lo que manda ahora `Api::V1::Agents::SessionsController`

```json
{
  "user_id": "<id de la sesión de Vertex>",
  "sessionState": {
    "profile": {
      "user_id": "36",
      "reporter_type": "User",
      "category": "standard",
      "name": "Victor",
      "site_id": 7,
      "site_slug": "saludgs"
    },
    "client_context": {
      "route": "/saludgs/es/appointments",
      "app_version": "1.15.0",
      "device": "Mozilla/5.0 (iPhone; …)",
      "chat_surface": "support_bubble_app"
    }
  }
}
```

Tres decisiones que conviene no deshacer:

1. **`profile` sale de `current_user`, nunca del body.** El agente usa
   `profile.user_id` como `reporter_id` del ticket y como filtro de "mis
   tickets". Con ese id en manos del cliente, un `user_id` ajeno en el POST
   leería los reportes de otra persona. `params[:user_id]` sigue siendo el id
   de la sesión de Vertex —para el invitado, un uuid anónimo— y no sirve para
   esto.
2. **El invitado no manda `profile`.** Sin sesión no hay a quién atribuir nada,
   y el agente ya degrada a `sin_perfil` sin romper la conversación. El cuerpo
   queda como estaba.
3. **`client_context` va aparte y con allowlist** (`CLIENT_CONTEXT_KEYS`,
   recortado a 200 caracteres). Es telemetría que arma el navegador: no se
   puede creer, y termina renderizada en el issue de GitHub. Del lado del
   agente hay una segunda allowlist (`CONTEXTO_EN_METADATA`) por lo mismo.

`site_slug` importa más de lo que parece: `SupportTicket.for_site` filtra por
`metadata->>'site_slug'`, así que sin él el ticket se crea pero no aparece
cuando soporte filtra por sitio en el board.

### Del lado de la PWA

`createSession` manda los headers de Devise (`access-token` / `client` / `uid`)
y el `client_context`. Sin esos headers `current_user` es `nil` y la sesión se
comporta como la de un invitado, aunque el usuario tenga sesión iniciada: es el
primer lugar donde mirar si un ticket sale sin reporter.

Ojo con un efecto secundario de mandarlos: `change_headers_on_each_request`
está en su default (`true`), así que esta llamada **rota** el token. La PWA
guarda el rotado de los headers de la respuesta, igual que hace su `ApiClient`.
Sin eso, abrir el chat invalidaría el token guardado y la siguiente petición de
la app daría 401 — es decir, abrir soporte cerraría la sesión.
