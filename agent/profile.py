"""Lectura del perfil del cliente desde el session state.

Opcion B de la arquitectura: core-api ya tiene el perfil en su propia DB
cuando crea la sesion, asi que lo siembra en `session_state` en el POST a
.../sessions. ADK lo mapea a `session.state` (ver ADK
sessions/vertex_ai_session_service.py:193 y :215). El agente NO hace una
llamada de vuelta a core-api para esto: el dato llega ya resuelto. Lo que si
viaja por HTTP son los tickets (tools/support_tickets.py), que no existen
todavia cuando se abre la sesion.

Contrato esperado en session_state:

    {"profile": {"user_id": "123",
                 "category": "<una de CATEGORIES>",
                 "name": "...",
                 "site_id": 7}}
"""

from __future__ import annotations

from typing import Any

# Clave unica en el state. Sin prefijo `user:`/`app:`/`temp:` a proposito:
# el perfil es un dato de la sesion, sembrado al crearla.
PROFILE_KEY = "profile"


def read_profile(state: dict[str, Any]) -> dict[str, Any] | None:
    """Devuelve el perfil sembrado por core-api, o None si no vino.

    Ausente == error de integracion (core-api no sembro el state), no un caso
    de negocio. El caller decide como degradar.
    """
    profile = state.get(PROFILE_KEY)
    return profile if isinstance(profile, dict) else None


def read_category(state: dict[str, Any]) -> str | None:
    """Categoria del cliente, normalizada, o None si no se pudo determinar."""
    profile = read_profile(state)
    if not profile:
        return None
    category = profile.get("category")
    return category.strip().lower() if isinstance(category, str) else None


# Que le estan pidiendo a la sesion. La conversacion de soporte no manda nada
# (ausente == charla normal); el endpoint de tickets siembra
# session_state["task"] = "ticket_classification" al crear la sesion, y eso es
# lo unico que separa un turno de clasificacion de uno conversacional.
TASK_KEY = "task"
TASK_TICKET = "ticket_classification"


def read_task(state: dict[str, Any]) -> str | None:
    """Tarea sembrada por core-api, normalizada, o None si es conversacion."""
    task = state.get(TASK_KEY)
    return task.strip().lower() or None if isinstance(task, str) else None
