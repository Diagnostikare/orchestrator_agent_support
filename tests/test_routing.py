"""Tests deterministas del routing y del parseo del perfil.

No hacen ninguna llamada al modelo: corren en milisegundos y no pueden fallar
por una respuesta distinta del LLM. Todo lo que dependa del modelo va en
evals/ con LLM-as-judge, no aca.
"""

import pytest

from support_agent.agent import FALLBACK, ROUTES, root_agent
from support_agent.profile import read_category, read_profile

STANDARD = "support_standard"
MEDICAL = "support_medical"


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
