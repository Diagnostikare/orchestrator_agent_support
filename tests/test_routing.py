"""Tests deterministas del routing y del parseo del perfil.

No hacen ninguna llamada al modelo: corren en milisegundos y no pueden fallar
por una respuesta distinta del LLM. Todo lo que dependa del modelo va en
evals/ con LLM-as-judge, no aqui.
"""

import pytest
from pydantic import ValidationError

from agent.agent import FALLBACK, ROUTES, TASK_ROUTES, root_agent
from agent.profile import TASK_TICKET, read_category, read_profile, read_task
from agent.ticket import TicketClassification, agent_ticket

STANDARD = "support_standard"
MEDICAL = "support_medical"
TICKET = "support_ticket_classifier"


# --- profile.py -------------------------------------------------------------

@pytest.mark.parametrize(
    "state,esperado",
    [
        ({"profile": {"category": "medical"}}, "medical"),
        ({"profile": {"category": "  MEDICAL  "}}, "medical"),  # normaliza
        ({"profile": {"category": "Standard"}}, "standard"),
        ({"profile": {"category": ""}}, ""),
        ({"profile": {}}, None),                  # perfil sin categoria
        ({}, None),                               # core-api no sembro nada
        ({"profile": "no-soy-un-dict"}, None),    # state corrupto
        ({"profile": {"category": 42}}, None),    # tipo inesperado
    ],
)
def test_read_category(state, esperado):
    assert read_category(state) == esperado


@pytest.mark.parametrize(
    "state,esperado",
    [
        ({"task": "ticket_classification"}, TASK_TICKET),
        ({"task": "  Ticket_Classification "}, TASK_TICKET),  # normaliza
        ({}, None),                          # conversacion: no manda task
        ({"task": ""}, None),
        ({"task": 7}, None),                 # tipo inesperado
    ],
)
def test_read_task(state, esperado):
    assert read_task(state) == esperado


def test_read_profile_ignora_tipos_invalidos():
    assert read_profile({"profile": ["a"]}) is None
    assert read_profile({}) is None
    assert read_profile({"profile": {"user_id": "1"}}) == {"user_id": "1"}


# --- router.py --------------------------------------------------------------

@pytest.mark.parametrize(
    "categoria,esperado",
    [
        ("medical", MEDICAL),
        ("standard", STANDARD),
        ("MEDICAL", MEDICAL),           # el normalizador la agarra
        ("clinica", STANDARD),          # categoria nueva sin ruta -> fallback
        ("", STANDARD),
    ],
)
def test_routing_por_categoria(categoria, esperado):
    state = {"profile": {"user_id": "1", "category": categoria}}
    assert root_agent.resolve_target_name(state) == esperado


def test_sin_state_cae_al_fallback():
    """core-api sin el diff de session_state: NO debe romper, debe degradar."""
    assert root_agent.resolve_target_name({}) == FALLBACK


def test_fallback_es_el_agente_de_menor_privilegio():
    """Un usuario no identificado nunca debe caer en el agente medico."""
    assert FALLBACK == STANDARD


def test_toda_ruta_apunta_a_un_subagente_existente():
    """Atrapa el typo de agregar una categoria y escribir mal el nombre."""
    existentes = {a.name for a in root_agent.sub_agents}
    assert set(ROUTES.values()) <= existentes
    assert FALLBACK in existentes


def test_los_nombres_de_agentes_son_unicos():
    """find_agent() busca en todo el subarbol: nombres duplicados lo hacen
    ambiguo."""
    nombres = [root_agent.name] + [a.name for a in root_agent.sub_agents]
    assert len(nombres) == len(set(nombres))


def test_las_claves_de_routes_estan_normalizadas():
    """ROUTES se compara contra la salida de read_category (ya en minusculas),
    asi que una clave con mayusculas seria codigo muerto."""
    for clave in ROUTES:
        assert clave == clave.strip().lower()


# --- routing por tarea ------------------------------------------------------

def test_ticket_va_al_clasificador():
    state = {"task": TASK_TICKET}
    assert root_agent.resolve_target_name(state) == TICKET


def test_la_tarea_gana_sobre_la_categoria():
    """El endpoint de tickets espera JSON: la categoria del cliente no puede
    desviarlo al agente conversacional."""
    state = {"task": TASK_TICKET, "profile": {"category": "medical"}}
    assert root_agent.resolve_target_name(state) == TICKET


def test_tarea_desconocida_cae_al_routing_por_categoria():
    state = {"task": "todavia_no_existe", "profile": {"category": "medical"}}
    assert root_agent.resolve_target_name(state) == MEDICAL


def test_toda_task_route_apunta_a_un_subagente_existente():
    existentes = {a.name for a in root_agent.sub_agents}
    assert set(TASK_ROUTES.values()) <= existentes


def test_las_claves_de_task_routes_estan_normalizadas():
    for clave in TASK_ROUTES:
        assert clave == clave.strip().lower()


# --- contrato de salida del clasificador ------------------------------------

def test_agent_output_respeta_el_contrato_con_core_api():
    """Los nombres de los campos los consume el serializer de core-api y el
    issue de GitHub: renombrar uno aqui rompe alla en silencio."""
    campos = TicketClassification.model_fields
    assert set(campos) == {
        "enhanced_subject",
        "enhanced_body",
        "classification",
        "priority",
    }
    assert all(campo.description for campo in campos.values())


def test_el_clasificador_fuerza_el_schema_y_lo_deja_en_el_state():
    assert agent_ticket.output_schema is TicketClassification
    assert agent_ticket.output_key == "agent_output"


def test_priority_solo_acepta_los_valores_del_sla():
    for valor in ("low", "medium", "high"):
        assert _clasificacion(priority=valor).priority == valor
    with pytest.raises(ValidationError):
        _clasificacion(priority="urgent")


def test_classification_solo_acepta_categorias_con_label_en_github():
    for valor in ("billing", "scheduling", "technical", "other"):
        assert _clasificacion(classification=valor).classification == valor
    with pytest.raises(ValidationError):
        _clasificacion(classification="facturacion")


def _clasificacion(**overrides):
    base = {
        "enhanced_subject": "No puedo agendar un estudio",
        "enhanced_body": "El boton de agendar no responde.",
        "classification": "technical",
        "priority": "medium",
    }
    return TicketClassification(**(base | overrides))
