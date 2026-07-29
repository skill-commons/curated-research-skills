#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  run_dt4acc_host_smoke_test.sh CHECKOUT_ROOT OUTPUT_JSON [PYTHON_RUNNER_ARGS...]

Set DT4ACC_SMOKE_PYTHON to the interpreter from an already prepared environment.
The wrapper never installs packages or starts services.
EOF
}

if (( $# < 2 )); then
  usage
  exit 2
fi

checkout_root=$1
output_json=$2
shift 2

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
runner="$script_dir/dt4acc_host_smoke_test.py"
python_bin=${DT4ACC_SMOKE_PYTHON:-python3}

if [[ ! -f "$runner" ]]; then
  echo "Missing bundled runner: $runner" >&2
  exit 2
fi

if [[ "$python_bin" == */* ]]; then
  if [[ ! -x "$python_bin" ]]; then
    echo "DT4ACC_SMOKE_PYTHON is not executable: $python_bin" >&2
    exit 2
  fi
elif ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python interpreter not found: $python_bin" >&2
  exit 2
fi

exec "$python_bin" "$runner" \
  --repo-root "$checkout_root" \
  --output "$output_json" \
  "$@"
