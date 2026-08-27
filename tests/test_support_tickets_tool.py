"""Tests de las tools de tickets contra core-api.

Deterministas y sin red: se sustituye `_request`, que es la unica puerta al
exterior del modulo. Lo que se prueba aqui es lo que el modelo NO puede
arreglar solo — de donde sale la identidad, que se le oculta, y que ningun
fallo de core-api se convierta en una excepcion que aborte el turno.
"""

import json
import urllib.error

import pytest

from agent.tools import support_tickets as st

PROFILE = {"user_id": "36", "category": "standard", "name": "Victor", "site_id": 7}


class FakeContext:
    """Lo unico que las tools usan de ToolContext es `.state`."""

    def __init__(self, state=None):
        self.state = state if state is not None else {"profile": dict(PROFILE)}


@pytest.fixture
def configurado(monkeypatch):
    monkeypatch.setenv("CORE_API_BASE_URL", "https://core-api.test")
    monkeypatch.setenv("AGENT_API_SECRET", "s3cr3t")


class Llamadas(list):
    """Registro de lo que se le mando a core-api, con respuestas encoladas."""

    def __init__(self):
        super().__init__()
        self._respuestas = []

    def responder(self, respuesta):
        self._respuestas.append(respuesta)

    def __call__(self, metodo, ruta, *, params=None, cuerpo=None):
        self.append({"metodo": metodo, "ruta": ruta, "params": params, "cuerpo": cuerpo})
        return self._respuestas.pop(0) if self._respuestas else {"status": "ok", "data": None}


@pytest.fixture
def llamadas(monkeypatch):
    registro = Llamadas()
    monkeypatch.setattr(st, "_request", registro)
    return registro


# --- identidad --------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        {},                                    # core-api no sembro nada
        {"profile": {"category": "standard"}},  # perfil sin user_id
        {"profile": {"user_id": ""}},           # user_id vacio
        {"profile": "no-soy-un-dict"},          # state corrupto
    ],
)
def test_sin_perfil_no_toca_core_api(state, llamadas, configurado):
    for tool, args in (
        (st.consultar_mis_tickets, ()),
        (st.crear_ticket, ("No puedo entrar", "Me marca error")),
        (st.ver_ticket, (5,)),
    ):
        resultado = tool(*args, FakeContext(state))
        assert resultado["status"] == "sin_perfil"

    assert llamadas == []


def test_reporter_type_por_defecto_es_user(llamadas, configurado):
    st.crear_ticket("Asunto", "Detalle", FakeContext())

    assert llamadas[0]["cuerpo"]["reporter_type"] == "User"


def test_reporter_type_lo_manda_core_api_cuando_no_es_un_user(llamadas, configurado):
    state = {"profile": {**PROFILE, "reporter_type": "ApiUser"}}

    st.crear_ticket("Asunto", "Detalle", FakeContext(state))

    assert llamadas[0]["cuerpo"]["reporter_type"] == "ApiUser"


# --- consultar_mis_tickets --------------------------------------------------


def test_consultar_filtra_por_el_reporter_de_la_sesion(llamadas, configurado):
    llamadas.responder({"status": "ok", "data": [{"id": 1, "enhanced_subject": "A"}]})

    st.consultar_mis_tickets(FakeContext())

    assert llamadas[0]["params"] == {"reporter_type": "User", "reporter_id": "36"}


def test_consultar_oculta_los_campos_que_el_modelo_no_necesita(llamadas, configurado):
    llamadas.responder({"status": "ok", "data": [{
        "id": 1, "enhanced_subject": "A", "classification": "billing",
        "channel": "web", "pending_github_push": True, "created_at": "2026-08-27",
        "github_item_node_id": "PVTI_x", "metadata": {"flow_token": "eyJ..."},
    }]})

    ticket = st.consultar_mis_tickets(FakeContext())["tickets"][0]

    assert set(ticket) == set(st.CAMPOS_VISIBLES)
    assert "eyJ" not in json.dumps(ticket)


def test_consultar_recorta_el_historial(llamadas, configurado):
    llamadas.responder({"status": "ok", "data": [{"id": n} for n in range(20)]})

    assert len(st.consultar_mis_tickets(FakeContext())["tickets"]) == st.MAX_TICKETS


def test_consultar_sin_tickets_no_es_un_error(llamadas, configurado):
    llamadas.responder({"status": "ok", "data": []})

    assert st.consultar_mis_tickets(FakeContext())["status"] == "sin_tickets"


def test_consultar_sobrevive_una_respuesta_con_forma_inesperada(llamadas, configurado):
    llamadas.responder({"status": "ok", "data": {"error": "algo"}})

    assert st.consultar_mis_tickets(FakeContext())["status"] == "error"


# --- ver_ticket -------------------------------------------------------------


def test_ver_ticket_devuelve_el_propio(llamadas, configurado):
    llamadas.responder({"status": "ok", "data": {
        "id": 9, "reporter_type": "User", "reporter_id": 36,
        "enhanced_subject": "A", "enhanced_body": "B",
    }})

    resultado = st.ver_ticket(9, FakeContext())

    assert resultado["status"] == "ok"
    assert resultado["ticket"]["enhanced_body"] == "B"


def test_ver_ticket_ajeno_se_comporta_como_inexistente(llamadas, configurado):
    llamadas.responder({"status": "ok", "data": {
        "id": 9, "reporter_type": "User", "reporter_id": 99,
        "enhanced_subject": "Ticket de alguien mas", "enhanced_body": "secreto",
    }})

    resultado = st.ver_ticket(9, FakeContext())

    assert resultado["status"] == "no_encontrado"
    assert "secreto" not in json.dumps(resultado)


def test_ver_ticket_404(llamadas, configurado):
    llamadas.responder({"status": "error", "codigo": 404, "detalle": "not found"})

    assert st.ver_ticket(9, FakeContext())["status"] == "no_encontrado"


# --- crear_ticket -----------------------------------------------------------


def test_crear_manda_el_ticket_con_la_identidad_de_la_sesion(llamadas, configurado):
    llamadas.responder({"status": "ok", "data": {"id": 12, "enhanced_subject": "No puedo entrar"}})

    resultado = st.crear_ticket("No puedo entrar", "Me marca error 500", FakeContext())

    assert resultado["ticket"]["id"] == 12
    assert llamadas[0]["cuerpo"] == {
        "subject": "No puedo entrar",
        "body": "Me marca error 500",
        "channel": "web",
        "reporter_type": "User",
        "reporter_id": "36",
        "contact_name": "Victor",
        "metadata": {"origen": "chat", "site_id": 7},
    }


def test_crear_sin_asunto_no_llega_a_core_api(llamadas, configurado):
    assert st.crear_ticket("   ", "detalle", FakeContext())["status"] == "invalido"
    assert llamadas == []


def test_crear_recorta_el_asunto_al_limite_de_la_columna(llamadas, configurado):
    st.crear_ticket("x" * 400, "detalle", FakeContext())

    assert len(llamadas[0]["cuerpo"]["subject"]) == 255


def test_crear_sin_detalle_manda_null_y_no_cadena_vacia(llamadas, configurado):
    st.crear_ticket("Asunto", "   ", FakeContext())

    assert llamadas[0]["cuerpo"]["body"] is None


def test_crear_rechazado_por_validacion(llamadas, configurado):
    llamadas.responder({"status": "error", "codigo": 422, "detalle": "invalid"})

    assert st.crear_ticket("Asunto", "Detalle", FakeContext())["status"] == "invalido"


def test_crear_con_core_api_caido_no_confirma_el_ticket(llamadas, configurado):
    llamadas.responder({"status": "error", "detalle": "connection refused"})

    resultado = st.crear_ticket("Asunto", "Detalle", FakeContext())

    assert resultado["status"] == "error"
    assert "ticket" not in resultado


# --- configuracion y transporte ---------------------------------------------


@pytest.mark.parametrize("faltante", ["CORE_API_BASE_URL", "AGENT_API_SECRET"])
def test_sin_configuracion_devuelve_no_configurado(faltante, monkeypatch, configurado):
    monkeypatch.delenv(faltante)

    assert st.consultar_mis_tickets(FakeContext())["status"] == "no_configurado"


def test_no_configurado_se_propaga_tal_cual_al_modelo(monkeypatch, configurado):
    """El modelo debe poder distinguir "no hay sistema" de "fallo la llamada"."""
    monkeypatch.delenv("AGENT_API_SECRET")

    resultado = st.crear_ticket("Asunto", "Detalle", FakeContext())

    assert resultado["status"] == "no_configurado"


def test_request_arma_url_y_headers(monkeypatch, configurado):
    capturado = {}

    class FakeResponse:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        capturado["url"] = request.full_url
        capturado["secret"] = request.get_header(st.SECRET_HEADER.capitalize())
        capturado["metodo"] = request.get_method()
        return FakeResponse()

    monkeypatch.setattr(st.urllib.request, "urlopen", fake_urlopen)

    resultado = st._request("GET", st.PATH, params={"reporter_id": "36", "channel": None})

    assert resultado == {"status": "ok", "data": {"ok": True}}
    assert capturado["url"] == "https://core-api.test/api/v1/agents/support_tickets?reporter_id=36"
    assert capturado["secret"] == "s3cr3t"
    assert capturado["metodo"] == "GET"


def test_request_no_deja_escapar_un_http_error(monkeypatch, configurado):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(st.urllib.request, "urlopen", fake_urlopen)

    assert st._request("GET", st.PATH) == {"status": "error", "codigo": 401, "detalle": ""}


def test_request_no_deja_escapar_un_fallo_de_red(monkeypatch, configurado):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(st.urllib.request, "urlopen", fake_urlopen)

    assert st._request("GET", st.PATH)["status"] == "error"
