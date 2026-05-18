#!/usr/bin/env bash

if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  echo "Source this script from the repo root: source scripts/model_presets/balanced-google.sh" >&2
  exit 1
fi

export AGENT_MODEL_BACKEND="litellm"
export AGENT_MODEL_DISPATCHER="openrouter/google/gemini-2.5-flash-lite"
export AGENT_MODEL_REACT="openrouter/google/gemini-2.5-flash"
export AGENT_MODEL_PLANNER="openrouter/google/gemini-2.5-flash"
export AGENT_MODEL_EXPLAINER="openrouter/google/gemini-2.5-flash"
export AGENT_MODEL_PRICING_JSON="$(cat infra/openrouter-pricing.example.json)"

echo "Activated model preset: balanced-google"
echo "  backend=${AGENT_MODEL_BACKEND}"
echo "  dispatcher=${AGENT_MODEL_DISPATCHER}"
echo "  react=${AGENT_MODEL_REACT}"
echo "  planner=${AGENT_MODEL_PLANNER}"
echo "  explainer=${AGENT_MODEL_EXPLAINER}"
