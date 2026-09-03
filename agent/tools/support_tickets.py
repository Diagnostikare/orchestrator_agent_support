"""Tickets de soporte contra core-api (`/api/v1/agents/support_tickets`).

Esto rompe a proposito la opcion B —"el agente nunca llama de vuelta a
core-api"— y conviene entender por que. El perfil sigue llegando sembrado en
`session_state`: es un dato que core-api YA tiene cuando abre la sesion, y
pedirlo de vuelta seria una llamada de red para algo que ya estaba resuelto.
Un ticket no: no existe cuando la sesion arranca, lo crea la conversacion. No
hay nada que sembrar.

Autenticacion: `X-Agent-Secret`, el mismo shared secret que valida
`AgentAuthenticated` del lado de core-api. Vertex AI Agent Engine no puede
sostener un token de devise, y estas rutas viven en un namespace aparte por
eso mismo.

Sin dependencias nuevas: `urllib.request` de la stdlib. El runtime desplegado
es exactamente `agent/requirements.txt` (ver el comentario de ese archivo, un
requirements mal armado ya rompio un deploy) y no vale la pena sumarle un
cliente HTTP para tres llamadas.

Que NO se expone, aunque el CRUD lo permita:

  * `DELETE` — un modelo no borra tickets. El endpoint existe para una consola
    interna, no para una conversacion.
  * `PATCH` — reescribir el `subject` de un ticket ya clasificado descuadraria
    el board sin dejar rastro de quien lo pidio.

La identidad NUNCA es un argumento de la tool: sale del `session_state`. Si el
modelo pudiera pasar un `contact_id` o un `reporter_id` cualquiera, bastaria
con que el usuario escribiera "muestrame los tickets del 5539706542" para leer
los de otra persona.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal

from google.adk.tools.tool_context import ToolContext

from ..profile import read_client_context, read_profile

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
PATH = "/api/v1/agents/support_tickets"
SECRET_HEADER = "X-Agent-Secret"

# Cuantos tickets devolverle al modelo. El endpoint pagina y ordena por
# `created_at DESC`: mas de esto solo infla el contexto con historia vieja.
MAX_TICKETS = 5

# Los campos que el modelo necesita para hablar del ticket. El serializer
# devuelve bastante mas (node ids de GitHub, timestamps, metadata): nada de eso
# le sirve para conversar y todo ocupa contexto.
CAMPOS_VISIBLES = (
    "id",
    "enhanced_subject",
    "classification",
    "channel",
    "pending_github_push",
    "created_at",
)


# La misma taxonomia del Flow de WhatsApp (`MOTIVO -> ASESORIA -> DETALLE`) y de
# los accesos rapidos de la burbuja de la PWA (`lib/support/quickActions.ts`).
# Los tres canales tienen que coincidir literal: es la llave con la que soporte
# compara "se corto la llamada" en WhatsApp contra el mismo caso en la app, y un
# `se_corto` de un lado contra un `llamada_cortada` del otro parte el reporte en
# dos sin que nadie se entere.
#
# En WhatsApp esto sale de botones y llega ya estructurado. En el chat no hay
# formulario, asi que lo llena el modelo desde la conversacion — de ahi que sea
# una lista cerrada y no texto libre: el valor sirve para agrupar en el board, y
# un enum que el modelo puede ampliar a voluntad no agrupa nada.
TAXONOMIA: dict[str, tuple[str, ...]] = {
    "asesoria": ("no_entra", "se_corto", "sin_atencion", "otro"),
    "sin_receta": (),
    "otro": (),
}

MOTIVO_POR_DEFECTO = "otro"

# Los mismos valores de TAXONOMIA, como `Literal` para que ADK los publique como
# `enum` en el schema de la tool y el modelo no pueda emitir otra cosa. No se
# derivan del dict porque `Literal` necesita constantes: `test_taxonomia.py` es
# quien impide que las dos definiciones se separen.
#
# `_clasificar` sigue validando igual. El enum cubre el valor suelto; lo que no
# puede expresar es la dependencia entre los dos campos —`se_corto` solo existe
# bajo `asesoria`— y esa es justo la equivocacion que un modelo comete.
Motivo = Literal["asesoria", "sin_receta", "otro"]
Submotivo = Literal["", "no_entra", "se_corto", "sin_atencion", "otro"]

# Claves de `client_context` que se copian a la metadata del ticket. Allowlist y
# no un `update()` del dict completo: el contexto lo arma el cliente, y aunque
# core-api ya lo sanea, la metadata termina renderizada en el issue de GitHub.
CONTEXTO_EN_METADATA = ("route", "app_version", "device", "chat_surface")


# --- configuracion ----------------------------------------------------------


def _config() -> tuple[str | None, str | None]:
    base_url = os.environ.get("CORE_API_BASE_URL", "").strip().rstrip("/")
    secret = os.environ.get("AGENT_API_SECRET", "").strip()
    return base_url or None, secret or None


def _no_configurado(faltante: str) -> dict:
    logger.warning("%s sin definir: no se puede hablar con core-api", faltante)
    return {
        "status": "no_configurado",
        "detalle": (
            "El sistema de tickets no esta disponible. Dile al usuario que no "
            "puedes registrarlo ahora y que lo intente mas tarde."
        ),
    }


# --- identidad --------------------------------------------------------------


def _identidad(state: dict[str, Any]) -> dict[str, Any] | None:
    """Quien es el usuario, segun lo que core-api sembro. None si no vino.

    `reporter_type` es parte del contrato de sembrado porque core-api valida
    contra una allowlist (`SupportTicket::REPORTER_TYPES`): el chat de la PWA
    es de `User`, pero BOA manda `ApiUser` y el agente no puede adivinarlo.
    """
    profile = read_profile(state)
    if not profile:
        return None

    user_id = profile.get("user_id")
    if user_id in (None, ""):
        return None

    reporter_type = profile.get("reporter_type") or "User"
    return {
        "reporter_type": str(reporter_type),
        "reporter_id": str(user_id),
        "contact_name": profile.get("name"),
        "site_id": profile.get("site_id"),
        # `SupportTicket.for_site` filtra por `metadata->>'site_slug'`, no por
        # site_id: sin el slug el ticket existe pero no aparece cuando soporte
        # filtra por sitio en el board.
        "site_slug": profile.get("site_slug"),
    }


def _sin_perfil() -> dict:
    # Ausente == error de integracion, igual que en profile.py. Un ticket sin
    # reporter y sin contact_id lo rechazaria la validacion de core-api de
    # todos modos, asi que se corta aca con un mensaje que el modelo pueda usar.
    logger.warning("session_state sin profile.user_id: no hay a quien atribuir el ticket")
    return {
        "status": "sin_perfil",
        "detalle": (
            "No se puede identificar al usuario en esta sesion. Pidele que "
            "escriba a soporte por su canal habitual."
        ),
    }


# --- HTTP -------------------------------------------------------------------


def _request(metodo: str, ruta: str, *, params: dict | None = None,
             cuerpo: dict | None = None) -> dict:
    """Llama a core-api. Nunca lanza: devuelve siempre un dict con `status`."""
    base_url, secret = _config()
    if not base_url:
        return _no_configurado("CORE_API_BASE_URL")
    if not secret:
        return _no_configurado("AGENT_API_SECRET")

    url = f"{base_url}{ruta}"
    if params:
        limpios = {k: v for k, v in params.items() if v not in (None, "")}
        if limpios:
            url = f"{url}?{urllib.parse.urlencode(limpios)}"

    data = json.dumps(cuerpo).encode() if cuerpo is not None else None
    request = urllib.request.Request(url, data=data, method=metodo)
    request.add_header(SECRET_HEADER, secret)
    request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            crudo = response.read().decode()
        return {"status": "ok", "data": json.loads(crudo) if crudo else None}
    except urllib.error.HTTPError as exc:
        # 401 aqui es un secret desalineado entre los dos lados, no algo que el
        # usuario pueda arreglar: se loguea distinto para que se note en triage.
        cuerpo_error = exc.read().decode(errors="replace")[:200]
        nivel = logger.error if exc.code in (401, 403) else logger.warning
        nivel("core-api %s %s -> %s: %s", metodo, ruta, exc.code, cuerpo_error)
        return {"status": "error", "codigo": exc.code, "detalle": cuerpo_error}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        # Una excepcion que suba de aqui aborta el turno del agente; un status
        # de error deja que el modelo se lo explique al usuario.
        logger.exception("core-api %s %s fallo", metodo, ruta)
        return {"status": "error", "detalle": str(exc)[:200]}


def _resumen(ticket: dict) -> dict:
    return {campo: ticket.get(campo) for campo in CAMPOS_VISIBLES}


def _fallo(respuesta: dict, accion: str) -> dict:
    """Traduce un fallo de transporte a algo que el modelo pueda decir."""
    if respuesta["status"] == "no_configurado":
        return respuesta
    return {
        "status": "error",
        "detalle": f"No se pudo {accion}. Dile al usuario que lo intente mas tarde.",
    }


# --- tools ------------------------------------------------------------------


def consultar_mis_tickets(tool_context: ToolContext) -> dict:
    """Consulta los tickets de soporte que ya abrio ESTE usuario.

    Usala cuando pregunte por el estado de un reporte previo ("como va mi
    ticket", "ya lo revisaron", "reporte esto ayer"). No pide argumentos: la
    identidad sale de la sesion.

    Returns:
      status: "ok" | "sin_tickets" | "sin_perfil" | "no_configurado" | "error".
      tickets: lista de {id, enhanced_subject, classification, channel,
        pending_github_push, created_at}, del mas reciente al mas viejo.
    """
    identidad = _identidad(tool_context.state)
    if not identidad:
        return _sin_perfil()

    respuesta = _request("GET", PATH, params={
        "reporter_type": identidad["reporter_type"],
        "reporter_id": identidad["reporter_id"],
    })
    if respuesta["status"] != "ok":
        return _fallo(respuesta, "consultar los tickets")

    tickets = respuesta.get("data") or []
    if not isinstance(tickets, list):
        logger.error("index devolvio %s, se esperaba una lista", type(tickets).__name__)
        return _fallo({"status": "error"}, "consultar los tickets")

    logger.info(
        "consultar_mis_tickets(%s#%s) -> %d",
        identidad["reporter_type"], identidad["reporter_id"], len(tickets),
    )
    if not tickets:
        return {
            "status": "sin_tickets",
            "tickets": [],
            "detalle": "Este usuario no tiene tickets abiertos.",
        }

    return {"status": "ok", "tickets": [_resumen(t) for t in tickets[:MAX_TICKETS]]}


def ver_ticket(ticket_id: int, tool_context: ToolContext) -> dict:
    """Consulta el detalle de UN ticket de este usuario, por su numero.

    Usala solo con un `ticket_id` que el propio usuario haya mencionado o que
    venga de `consultar_mis_tickets`. Nunca inventes un numero.

    Args:
      ticket_id: el numero del ticket (el campo `id`).

    Returns:
      status: "ok" | "no_encontrado" | "sin_perfil" | "no_configurado" | "error".
      ticket: {id, enhanced_subject, enhanced_body, classification, channel,
        pending_github_push, created_at}.
    """
    identidad = _identidad(tool_context.state)
    if not identidad:
        return _sin_perfil()

    respuesta = _request("GET", f"{PATH}/{int(ticket_id)}")
    if respuesta["status"] != "ok":
        if respuesta.get("codigo") == 404:
            return {"status": "no_encontrado", "detalle": "No existe ese ticket."}
        return _fallo(respuesta, "consultar el ticket")

    ticket = respuesta.get("data") or {}

    # El endpoint `show` no filtra por reporter: sin esta comprobacion, pedir
    # un id ajeno devolveria el ticket de otra persona. El filtro real
    # pertenece a core-api — esto es la segunda linea, no la primera.
    if (str(ticket.get("reporter_id")) != identidad["reporter_id"]
            or ticket.get("reporter_type") != identidad["reporter_type"]):
        logger.warning(
            "ticket %s no pertenece a %s#%s: no se devuelve",
            ticket_id, identidad["reporter_type"], identidad["reporter_id"],
        )
        return {"status": "no_encontrado", "detalle": "No existe ese ticket."}

    detalle = _resumen(ticket)
    detalle["enhanced_body"] = ticket.get("enhanced_body")
    return {"status": "ok", "ticket": detalle}


def _clasificar(motivo: str, submotivo: str) -> tuple[str, str | None]:
    """Encaja lo que dijo el modelo en TAXONOMIA. Nunca falla.

    Un motivo inventado degrada a "otro" y un submotivo que no pertenece al
    motivo se descarta. A proposito no rechaza el ticket: el reporte del usuario
    es el dato que importa y perderlo porque el modelo escribio "asesoría" con
    acento seria cambiar un ticket mal etiquetado por ningun ticket.
    """
    motivo = (motivo or "").strip().lower()
    submotivo = (submotivo or "").strip().lower()

    if motivo not in TAXONOMIA:
        if motivo:
            logger.warning("motivo fuera de la taxonomia: %r -> %s", motivo, MOTIVO_POR_DEFECTO)
        return MOTIVO_POR_DEFECTO, None

    if submotivo and submotivo not in TAXONOMIA[motivo]:
        logger.warning("submotivo %r no pertenece a %r: se descarta", submotivo, motivo)
        return motivo, None

    return motivo, submotivo or None


def _metadata(identidad: dict, state: dict, motivo: str, submotivo: str | None) -> dict:
    """Lo que acompaña al ticket en el board, sin claves vacias.

    `None` fuera en vez de dentro: la metadata se renderiza en el issue de
    GitHub, y un "Submotivo: —" hace pensar que el dato se perdio cuando lo que
    pasa es que ese motivo no tiene segundo nivel.
    """
    contexto = read_client_context(state)
    metadata = {
        "origen": "chat",
        "site_id": identidad["site_id"],
        "site_slug": identidad["site_slug"],
        "motivo": motivo,
        "submotivo": submotivo,
        **{k: contexto.get(k) for k in CONTEXTO_EN_METADATA},
    }
    return {k: v for k, v in metadata.items() if v not in (None, "")}


def crear_ticket(
    asunto: str, detalle: str, motivo: Motivo, submotivo: Submotivo, tool_context: ToolContext
) -> dict:
    """Abre un ticket de soporte para este usuario.

    Usala cuando no puedas resolver algo con la documentacion y el usuario
    quiera que alguien lo revise. Confirmale ANTES de crearlo, y despues dile
    el numero que devuelve esta tool.

    Args:
      asunto: una linea que resuma el problema, en las palabras del usuario.
      detalle: el problema completo, con lo que el usuario ya intento.
      motivo: uno de "asesoria" (algo paso con una asesoria), "sin_receta" (no
        llego la receta ni los resultados) u "otro". Usa "otro" si dudas.
      submotivo: solo cuando el motivo es "asesoria", uno de "no_entra" (no
        pudo entrar a la llamada), "se_corto" (la llamada se corto),
        "sin_atencion" (nadie lo atendio) u "otro". Cadena vacia en cualquier
        otro caso. No lo inventes: si el usuario no lo dijo, mandalo vacio.

    Returns:
      status: "ok" | "invalido" | "sin_perfil" | "no_configurado" | "error".
      ticket: {id, enhanced_subject, ...} del ticket creado.
    """
    identidad = _identidad(tool_context.state)
    if not identidad:
        return _sin_perfil()

    asunto = (asunto or "").strip()
    if not asunto:
        return {
            "status": "invalido",
            "detalle": "Falta el asunto. Preguntale al usuario que resuma su problema.",
        }

    motivo, submotivo = _clasificar(motivo, submotivo)

    # `channel: "web"` porque este agente atiende el chat. Los tickets de
    # WhatsApp los abre el Flow del lado de core-api, no pasan por aqui.
    cuerpo = {
        "subject": asunto[:255],
        "body": (detalle or "").strip() or None,
        "channel": "web",
        "reporter_type": identidad["reporter_type"],
        "reporter_id": identidad["reporter_id"],
        "contact_name": identidad["contact_name"],
        "metadata": _metadata(identidad, tool_context.state, motivo, submotivo),
    }

    respuesta = _request("POST", PATH, cuerpo=cuerpo)
    if respuesta["status"] != "ok":
        if respuesta.get("codigo") == 422:
            return {
                "status": "invalido",
                "detalle": "El ticket fue rechazado. Pide al usuario que reformule el problema.",
            }
        return _fallo(respuesta, "abrir el ticket")

    ticket = respuesta.get("data") or {}
    logger.info(
        "crear_ticket -> #%s para %s#%s",
        ticket.get("id"), identidad["reporter_type"], identidad["reporter_id"],
    )
    return {"status": "ok", "ticket": _resumen(ticket)}
