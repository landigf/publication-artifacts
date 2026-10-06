#!/bin/sh
set -eu
cd "$(dirname "$0")"
TASK_PYTHON="${BROWSETRACE_PYTHON:-python3}"
"$TASK_PYTHON" tools/verify_publication.py --recompute --output-dir reports/reproduction
