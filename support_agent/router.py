"""Router determinista por categoria de cliente.

Por que un BaseAgent a mano y no `LlmAgent(sub_agents=[...])`:

La delegacion nativa de ADK es LLM-driven — el modelo raiz lee el `description`
de cada hijo y llama `transfer_to_agent`. Eso es lo correcto cuando hay que
INFERIR a quien le toca. Aca no hay nada que inferir: core-api ya sembro la
categoria en `session_state` (opcion B). Rutear con el LLM entonces solo agrega
un turno de latencia, costo de tokens, y una forma de fallar que no existiria.

Consecuencia: el `description` de los sub-agentes ya NO decide el routing.
Queda como documentacion y para el dia que un sub-agente tenga hijos propios.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import logging

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.utils.context_utils import Aclosing

from .profile import read_category

logger = logging.getLogger(__name__)


class CategoryRouter(BaseAgent):
    """Delega al sub-agente que corresponde a la categoria del cliente.

    Attributes:
      routes: categoria (normalizada, minusculas) -> nombre del sub-agente.
      fallback: sub-agente a usar si la categoria falta o es desconocida.
    """

    routes: dict[str, str] = {}
    fallback: str = ""

    def resolve_target_name(self, state: dict) -> str:
        """Decide a que sub-agente le toca. Puro: sin I/O y sin LLM.

        Separado de `_run_async_impl` a proposito, para que la decision de
        routing —que es la parte critica— sea testeable sin gastar una llamada
        al modelo ni depender de su respuesta.
        """
        category = read_category(state)
        target_name = self.routes.get(category or "", self.fallback)

        if category is None:
            # core-api no sembro el state, o el usuario es invitado sin perfil.
            # Se degrada al fallback en vez de cortar la conversacion, pero se
            # loguea como WARNING porque para un usuario logueado es un bug de
            # integracion, no un caso de negocio.
            logger.warning(
                "sin categoria en session.state; usando fallback %r", target_name
            )
        elif category not in self.routes:
            # Categoria nueva agregada en core-api pero no en este mapa.
            logger.warning(
                "categoria %r desconocida; usando fallback %r",
                category,
                target_name,
            )

        return target_name

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        target_name = self.resolve_target_name(ctx.session.state)

        target = self.find_agent(target_name)
        if target is None:
            raise ValueError(
                f"{self.name}: no existe el sub-agente {target_name!r}. "
                f"Disponibles: {[a.name for a in self.sub_agents]}"
            )

        logger.info("routing -> %s", target.name)

        async with Aclosing(target.run_async(ctx)) as agen:
            async for event in agen:
                yield event
