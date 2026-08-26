#!/usr/bin/env bash
# Corre los dos eval sets con sus metricas propias.
#
# Estan separados a proposito: `adk eval` acepta un solo --config_file_path, y
# las metricas correctas difieren por tipo de caso. Aplicar hallucinations_v1 a
# un caso de rechazo lo hace fallar aunque la negativa sea perfecta.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== grounded (fundamentacion + acierto) ==="
.venv/bin/adk eval support_agent evals/grounded.evalset.json \
  --config_file_path evals/grounded.config.json "$@"

echo "=== scope (respeto de alcance) ==="
.venv/bin/adk eval support_agent evals/scope.evalset.json \
  --config_file_path evals/scope.config.json "$@"
