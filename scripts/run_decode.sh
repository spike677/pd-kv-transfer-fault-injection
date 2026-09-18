#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
: "${PD_FAULT_DIR:?Set a private bind-mounted control directory}"
: "${PD_FAULT_OWNER:?Set an experiment owner}"
: "${PD_FAULT_ENGINE:?Use the exact D kv_transfer_config.engine_id}"
if [ "$#" -eq 0 ]; then echo 'Usage: run_decode.sh vllm serve MODEL [original D arguments...]' >&2; exit 2; fi
# Refuse to silently shadow an existing user/site observer bootstrap.
python -c 'import importlib.util; s=importlib.util.find_spec("sitecustomize"); assert s is None, "Existing sitecustomize: integrate explicitly, do not shadow it"'
export VLLM_SERVER_DEV_MODE=1
export PD_FAULT_ENABLE=1 PD_FAULT_SIDE=decode
export PYTHONPATH="$project_root/bootstrap:$project_root${PYTHONPATH:+:$PYTHONPATH}"
exec "$@"
