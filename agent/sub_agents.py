"""Agentes especialistas por categoria de cliente.

Arquitectura monolitica: todos viven en el mismo deployment y comparten el
mismo `session.state`. Lo que cambia entre ellos es el prompt, el modelo y
(mas adelante) el set de tools.
"""

from __future__ import annotations

from google.adk.agents import Agent

from .models import GlobalGemini

from .tools.docs_search import buscar_documentacion

# Flash alcanza para FAQs y guia a documentacion.
MODEL_STANDARD = GlobalGemini(model="gemini-3.5-flash")
# El agente medico se separa para poder subirle el tier sin tocar al resto.
MODEL_MEDICAL = GlobalGemini(model="gemini-3.5-flash")

# Una sola tool para ambos: elige el dataStore segun la categoria del state
# (ver DATASTORE_ENV_BY_CATEGORY en tools/docs_search.py).

# Regla de citado, compartida: sin esto el modelo mezcla lo recuperado con su
# conocimiento parametrico y en soporte eso es indistinguible de inventar.
_GROUNDING_RULES = (
    "\nAntes de afirmar cualquier dato sobre el producto, precios, politicas "
    "o alcances del servicio, llama a `buscar_documentacion`.\n"
    "- Responde SOLO con lo que digan los `pasajes` devueltos.\n"
    "- Cita el `titulo` del pasaje del que sacaste cada dato.\n"
    "- Si el status no es \"ok\", o los pasajes no contienen el dato, deci que "
    "no lo encontraste y ofrece abrir un ticket. No lo completes de memoria."
)
agent_standard = Agent(
    model=MODEL_STANDARD,
    name="support_standard",
    description=(
        "Soporte para clientes estandar: preguntas frecuentes, guia a la "
        "documentacion oficial y generacion de tickets de soporte."
    ),
    instruction=(
        "Sos el agente de soporte estandar de Diagnostikare.\n"
        "Respondes en el idioma del usuario, breve y concreto.\n"
        "Alcance: dudas de uso del producto, FAQs, y derivar a documentacion "
        "oficial o a un ticket de soporte.\n"
        "NO das consejo medico ni interpretas resultados clinicos. Si te "
        "preguntan eso, aclara que no es tu alcance.\n"
        "Para dudas de producto, busca primero en la documentacion oficial."
    ) + _GROUNDING_RULES,
    tools=[buscar_documentacion],
)

agent_medical = Agent(
    model=MODEL_MEDICAL,
    name="support_medical",
    description=(
        "Soporte para medicos: dudas sobre el uso clinico de la plataforma, "
        "interpretacion de los flujos de diagnostico y escalamiento tecnico."
    ),
    instruction=(
        "Sos el agente de soporte de Diagnostikare para personal medico.\n"
        "Tu interlocutor es un profesional de la salud: podes usar "
        "terminologia clinica sin simplificar.\n"
        "Alcance: como usar la plataforma en contexto clinico, que significan "
        "los flujos de diagnostico, y escalar problemas tecnicos.\n"
        "NO emitis diagnosticos ni recomendaciones de tratamiento para un "
        "paciente concreto: sos soporte de producto, no una segunda opinion.\n"
        "Para dudas de producto, busca primero en la documentacion oficial."
    ) + _GROUNDING_RULES,
    tools=[buscar_documentacion],
)
