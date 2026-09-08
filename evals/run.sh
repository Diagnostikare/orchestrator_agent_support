#!/usr/bin/env bash
# Corre los dos eval sets con sus metricas propias.
#
# Estan separados a proposito: `adk eval` acepta un solo --config_file_path, y
# las metricas correctas difieren por tipo de caso. Aplicar hallucinations_v1 a
# un caso de rechazo lo hace fallar aunque la negativa sea perfecta.
set -euo pipefail
cd "$(dirname "$0")/.."

# El agente ancla su modelo a `global` en codigo (models.py), pero el JUEZ de
# `adk eval` se crea con la location del ambiente: con us-central1 —lo que pide
# .env para el deploy— cada llamada al juez tira 404 y los casos terminan en
# estado 3 (NOT_EVALUATED) sin un solo "FAILED" a la vista. Se veia igual que
# una corrida sana. Aqui la pisamos: es solo el proceso de eval, no el deploy.
export GOOGLE_CLOUD_LOCATION=global

echo "=== grounded (fundamentacion + acierto) ==="
.venv/bin/adk eval agent evals/grounded.evalset.json \
  --config_file_path evals/grounded.config.json "$@"

echo "=== scope (respeto de alcance) ==="
.venv/bin/adk eval agent evals/scope.evalset.json \
  --config_file_path evals/scope.config.json "$@"

echo "=== ticket (clasificacion estructurada) ==="
.venv/bin/adk eval agent evals/ticket.evalset.json \
  --config_file_path evals/ticket.config.json "$@"
