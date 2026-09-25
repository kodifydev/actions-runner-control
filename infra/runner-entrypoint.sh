#!/usr/bin/env bash
set -euo pipefail
if ! IFS= read -r config || [[ -z "$config" ]]; then
  printf '%s\n' 'Missing ephemeral runner configuration' >&2
  exit 1
fi
# Do not enable shell tracing or print this credential-bearing configuration.
exec ./run.sh --jitconfig "$config"
