#!/usr/bin/env bash

if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  echo "Source this script from the repo root: source scripts/model_presets/balanced-openai.sh" >&2
  exit 1
fi

export AGENT_MODEL_BACKEND="litellm"
export AGENT_MODEL_DISPATCHER="openrouter/openai/gpt-5.4-nano"
export AGENT_MODEL_REACT="openrouter/openai/gpt-5.4-mini"
export AGENT_MODEL_PLANNER="openrouter/openai/gpt-5.4-mini"
export AGENT_MODEL_EXPLAINER="openrouter/openai/gpt-5.4-mini"
export AGENT_MODEL_PRICING_JSON="$(cat infra/openrouter-pricing.example.json)"

echo "Activated model preset: balanced-openai"
echo "  backend=${AGENT_MODEL_BACKEND}"
echo "  dispatcher=${AGENT_MODEL_DISPATCHER}"
echo "  react=${AGENT_MODEL_REACT}"
echo "  planner=${AGENT_MODEL_PLANNER}"
echo "  explainer=${AGENT_MODEL_EXPLAINER}"
