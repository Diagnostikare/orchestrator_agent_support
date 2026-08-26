"""Modelo con la location fijada en codigo.

Problema que resuelve: la region de DEPLOY y la region del MODELO son cosas
distintas y aca no coinciden.

  - Agent Engine: `AgentRegistry` en core-api arma la URL con `us-central1`
    hardcodeado, asi que el reasoningEngine tiene que vivir ahi.
  - Modelo: `gemini-3.5-flash` devuelve 404 en us-central1 y solo responde en
    `global` (verificado contra architecture-beta el 2026-08-26).

Si dejaramos que el modelo tome la location del ambiente, el agente desplegado
en us-central1 tiraria 404 en cada request. Fijandola aca, GOOGLE_CLOUD_LOCATION
queda libre para lo que Agent Engine necesite.

Patron documentado en adk/models/google_llm.py:102-113.
"""

from __future__ import annotations

from functools import cached_property

from google.adk.models import Gemini
from google.genai import Client

# OJO: `global` no garantiza residencia de datos. Ver la nota en .env; si
# compliance exige residencia, hay que bajar a gemini-2.5-flash en us-central1
# (que medimos peor en extraccion, pero es la unica alternativa hoy).
MODEL_LOCATION = "global"


class GlobalGemini(Gemini):
    """Gemini anclado a `MODEL_LOCATION`, ignore el ambiente."""

    @cached_property
    def api_client(self) -> Client:
        return Client(vertexai=True, location=MODEL_LOCATION)
