"""Tests de la normalizacion del JSON final del clasificador (agent/ticket.py).

Deterministas: no llaman al modelo. Cubren lo que el after_model_callback
arregla de forma mecanica antes de que core-api lea la respuesta.
"""

import json

import pytest
from google.adk.models import LlmResponse
from google.genai import types

from agent.ticket import _normalizar_respuesta, normalizar_salida, normalizar_sla


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("≤ 8 horas hábiles", "8 horas hábiles"),
        ("8 horas hábiles", "8 horas hábiles"),
        ("≤ 3 días hábiles", "3 días hábiles"),
        ("hasta 1 día hábil", "1 día hábil"),
        ("8 horas habiles", "8 horas hábiles"),  # copiado del prompt, sin acento
        ("3 dias habiles", "3 días hábiles"),
        ("≤ 6 horas", "6 horas"),                 # S1: horas de reloj, sin "hábiles"
        ("Según prioridad", ""),                 # S4: no es un plazo
        ("", ""),
    ],
)
def test_normalizar_sla(valor, esperado):
    assert normalizar_sla(valor) == esperado


def test_body_con_saltos_escritos_como_texto():
    salida = normalizar_salida({"enhanced_body": "- **Canal:** web\\n- **Sitio:** saludgs"})
    assert salida["enhanced_body"] == "- **Canal:** web\n- **Sitio:** saludgs"


def test_body_con_saltos_reales_no_se_toca():
    # Si ya hay saltos reales, un "\n" literal es contenido del ticket.
    body = "### Reporte\nEl error dice: C:\\new\\folder"
    assert normalizar_salida({"enhanced_body": body})["enhanced_body"] == body


def _respuesta(texto, partial=False):
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=texto)]),
        partial=partial,
    )


def test_callback_reescribe_el_json_final():
    r = _normalizar_respuesta(None, _respuesta(json.dumps({"sla_resolution": "≤ 8 horas hábiles"})))
    assert json.loads(r.content.parts[0].text)["sla_resolution"] == "8 horas hábiles"


@pytest.mark.parametrize("texto", ["no es json", "[1, 2]"])
def test_callback_deja_pasar_lo_que_no_es_el_json(texto):
    assert _normalizar_respuesta(None, _respuesta(texto)) is None


def test_callback_ignora_chunks_parciales():
    assert _normalizar_respuesta(None, _respuesta("{}", partial=True)) is None


def test_callback_ignora_llamadas_a_tools():
    r = LlmResponse(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name="buscar_documentacion", args={}))
    ]))
    assert _normalizar_respuesta(None, r) is None
