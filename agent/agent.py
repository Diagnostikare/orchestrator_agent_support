"""Agente de soporte Diagnostikare — raiz del arbol.

Flujo completo (ver core-api app/controllers/api/v1/agents/):
    cliente -> core-api -> Vertex AI Agent Engine -> este agente
      sessions_controller    POST .../sessions     {user_id, session_state}
      stream_queries_ctrl    POST ...:streamQuery  {message, user_id, session_id}

El perfil y la categoria del cliente llegan sembrados en `session_state`;
este agente no consulta core-api de vuelta (opcion B).
"""

from __future__ import annotations

from .router import CategoryRouter
from .sub_agents import agent_medical, agent_standard

# Mapa categoria -> sub-agente. Las claves deben coincidir EXACTAMENTE (ya
# normalizadas a minusculas por read_category) con lo que core-api siembra en
# session_state["profile"]["category"].
# Para sumar la tercera categoria: agregar el Agent en sub_agents.py, y una
# entrada aca mas su nombre en la lista `sub_agents` de abajo.
ROUTES = {
    "standard": agent_standard.name,
    "medical": agent_medical.name,
}

# Fallback intencionalmente `standard`: si no sabemos quien es el usuario, el
# camino seguro es el de menor privilegio, no el especializado.
FALLBACK = agent_standard.name

root_agent = CategoryRouter(
    name="support_orquestador",
    description="Punto de entrada de soporte para clientes de Diagnostikare.",
    sub_agents=[agent_standard, agent_medical],
    routes=ROUTES,
    fallback=FALLBACK,
)
