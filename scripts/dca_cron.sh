#!/bin/bash
# launchd가 호출하는 래퍼. 프로젝트로 이동 후 적립 매수 자동 실행기를 돈다.
# 인자는 그대로 전달(예: --execute). 로그는 run_dca.py가 data/dca.log에 직접 기록한다.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH=src
PY="${DCA_PYTHON:-/usr/local/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" scripts/run_dca.py --auto "$@"
