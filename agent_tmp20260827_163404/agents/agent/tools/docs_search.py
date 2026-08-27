"""Busqueda en documentacion sobre Vertex AI Search (Discovery Engine).

Por que una function tool propia y no `VertexAiSearchTool` de ADK:

`VertexAiSearchTool` inyecta un `types.Tool(retrieval=...)` y el retrieval pasa
DENTRO del modelo, del lado de Google. Funciona y cita bien, pero no deja
rastro: `intermediate_data.tool_uses` queda vacio. Consecuencia medida con
`adk eval`: los jueces (hallucinations_v1, rubricas) no pueden auditar de donde
salio cada afirmacion y castigan respuestas CORRECTAS. Cuanto mejor
fundamentada la respuesta, peor el score — 0.0 en el caso mejor citado.

Ejecutando la busqueda nosotros:
  - queda registrada como tool_use + tool_response => evaluable y observable,
  - el texto recuperado es el "contexto" que las metricas necesitan,
  - controlamos el dataStore por categoria y podemos filtrar.

Patron de infra del proyecto: un bucket GCS de docs por agente + un dataStore
`<agente>-collection_documents` que lo indexa.
"""

from __future__ import annotations

import logging
import os

from google.adk.tools.tool_context import ToolContext
from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as discoveryengine

from ..profile import read_category

logger = logging.getLogger(__name__)

# Los dataStores viven en su propia location, independiente de la del modelo.
DATASTORE_LOCATION = os.environ.get("DATASTORE_LOCATION", "global")

# Cuantos pasajes devolver. Alto no es mejor: medimos que mas contexto
# recuperado no da mejor respuesta (gemini-2.5-flash con 4 chunks contesto peor
# que 3.5 con 1).
MAX_RESULTS = 5

# Categoria -> env var con el ID del dataStore. Espeja ROUTES en agent.py.
DATASTORE_ENV_BY_CATEGORY = {
    "standard": "DOCS_DATASTORE_STANDARD",
    "medical": "DOCS_DATASTORE_MEDICAL",
}
DEFAULT_DATASTORE_ENV = "DOCS_DATASTORE_STANDARD"


def _serving_config(datastore_id: str) -> str:
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    return (
        f"projects/{project}/locations/{DATASTORE_LOCATION}"
        f"/collections/default_collection/dataStores/{datastore_id}"
        "/servingConfigs/default_search"
    )


def _client() -> discoveryengine.SearchServiceClient:
    # `global` usa el endpoint por defecto; cualquier otra location necesita el
    # endpoint regional explicito.
    options = (
        None
        if DATASTORE_LOCATION == "global"
        else ClientOptions(
            api_endpoint=f"{DATASTORE_LOCATION}-discoveryengine.googleapis.com"
        )
    )
    return discoveryengine.SearchServiceClient(client_options=options)


def _datastore_for(state: dict) -> tuple[str | None, str]:
    """Devuelve (datastore_id, env_var) segun la categoria del cliente."""
    category = read_category(state) or ""
    env_var = DATASTORE_ENV_BY_CATEGORY.get(category, DEFAULT_DATASTORE_ENV)
    return os.environ.get(env_var, "").strip() or None, env_var


def buscar_documentacion(consulta: str, tool_context: ToolContext) -> dict:
    """Busca en la documentacion oficial de Diagnostikare.

    Usala SIEMPRE antes de afirmar un dato sobre el producto, precios,
    politicas, privacidad o alcances del servicio. No respondas de memoria.

    Args:
      consulta: la pregunta o los terminos a buscar, en lenguaje natural.

    Returns:
      status: "ok" | "sin_resultados" | "no_configurado" | "error".
      pasajes: lista de {titulo, texto, fuente} para citar. Cita el `titulo`.
    """
    # `tool_context` lo inyecta ADK y no se le muestra al modelo (se detecta
    # por la anotacion de tipo, ver adk/tools/function_tool.py:139).
    datastore_id, env_var = _datastore_for(tool_context.state)

    if not datastore_id:
        logger.warning("%s sin definir: no hay donde buscar", env_var)
        return {
            "status": "no_configurado",
            "pasajes": [],
            "detalle": (
                "La busqueda en documentacion no esta configurada. Di que no "
                "puedes verificarlo y ofrece abrir un ticket."
            ),
        }

    request = discoveryengine.SearchRequest(
        serving_config=_serving_config(datastore_id),
        query=consulta,
        page_size=MAX_RESULTS,
        content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
            # SEGMENTS, no answers. Los extractive *answers* son fragmentos
            # cortos: vienen con `<b>` de resaltado y se cortan con "..." a
            # ~380 chars. En un dato que vive en una tabla eso es peor que no
            # traer nada — pidiendo la matriz de severidad del SLA volvia
            # "Critico (S1) ... Alto (S2) Funcionalidad ..." y el modelo
            # asignaba prioridad sin haber visto S3 ni S4.
            # Los *segments* son el bloque completo del documento (~2.6k chars
            # en ese caso), en texto plano y sin truncar: la tabla entera.
            # `num_previous/next_segments` quedan en 0: el segmento propio ya
            # trae su seccion completa y el contexto vecino solo infla tokens.
            extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                max_extractive_segment_count=2,
                # Answers como respaldo: si un documento no produce segments,
                # es preferible un fragmento corto a no citar nada.
                max_extractive_answer_count=3,
            )
        ),
    )

    try:
        response = _client().search(request)
    except Exception as exc:  # noqa: BLE001 - la tool nunca debe tirar
        # Una excepcion aqui aborta el turno del agente. Devolver un status de
        # error deja que el modelo se lo explique al usuario.
        logger.exception("busqueda fallida en %s", datastore_id)
        return {"status": "error", "pasajes": [], "detalle": str(exc)[:200]}

    pasajes = []
    for result in response.results:
        data = dict(result.document.derived_struct_data or {})
        # Por documento: los segments si los hay, y solo si no, los answers.
        # Nunca los dos — el answer suele ser un recorte del mismo segment y
        # mandarlo duplicado solo repite el dato en el contexto del modelo.
        fragmentos = data.get("extractive_segments") or data.get(
            "extractive_answers", []
        )
        for fragmento in fragmentos:
            texto = dict(fragmento).get("content", "").strip()
            if texto:
                pasajes.append(
                    {
                        "titulo": data.get("title") or "(sin titulo)",
                        "texto": texto,
                        "fuente": data.get("link", ""),
                    }
                )

    logger.info(
        "buscar_documentacion(%r) en %s -> %d pasajes",
        consulta,
        datastore_id,
        len(pasajes),
    )
    if not pasajes:
        return {
            "status": "sin_resultados",
            "pasajes": [],
            "detalle": "No hay nada en la documentacion sobre eso.",
        }
    return {"status": "ok", "pasajes": pasajes}
