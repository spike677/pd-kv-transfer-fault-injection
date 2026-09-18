#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
: "${PD_FAULT_DIR:?Set a private bind-mounted control directory}"
: "${PD_FAULT_OWNER:?Set an experiment owner}"
if [ -n "${PD_FAULT_ENGINE_BASE:-}" ]; then
  : "${PD_FAULT_DEPLOYMENT_ID:?Set deployment ID}"
  : "${PD_FAULT_NODE_ID:?Set explicit node ID}"
  export PD_FAULT_SESSION_ID="$(python -c 'import uuid; print(uuid.uuid4().hex)')"
else
  : "${PD_FAULT_ENGINE:?Legacy TP1 exact engine ID required}"
fi
if [ "$#" -eq 0 ]; then echo 'Usage: run_decode.sh vllm serve MODEL [original D arguments...]' >&2; exit 2; fi
# Refuse to silently shadow an existing user/site observer bootstrap.
python -c 'import importlib.util; s=importlib.util.find_spec("sitecustomize"); assert s is None, "Existing sitecustomize: integrate explicitly, do not shadow it"'
export VLLM_SERVER_DEV_MODE=1
export PD_FAULT_ENABLE=1 PD_FAULT_SIDE=decode PD_FAULT_ROLE=decode
export PYTHONPATH="$project_root/bootstrap:$project_root${PYTHONPATH:+:$PYTHONPATH}"
exec "$@"
