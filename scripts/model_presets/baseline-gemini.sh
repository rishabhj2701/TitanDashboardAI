#!/usr/bin/env bash

if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  echo "Source this script from the repo root: source scripts/model_presets/baseline-gemini.sh" >&2
  exit 1
fi

unset AGENT_MODEL_DISPATCHER
unset AGENT_MODEL_REACT
unset AGENT_MODEL_PLANNER
unset AGENT_MODEL_EXPLAINER

export AGENT_MODEL_BACKEND="google"
export AGENT_MODEL_DEFAULT="gemini-2.5-flash"
export AGENT_MODEL_PRICING_JSON="$(cat infra/openrouter-pricing.example.json)"

echo "Activated model preset: baseline-gemini"
echo "  backend=${AGENT_MODEL_BACKEND}"
echo "  default_model=${AGENT_MODEL_DEFAULT}"
