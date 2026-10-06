#!/bin/sh
set -eu
if [ "$#" -gt 1 ]; then
  echo 'Usage: sh REPRODUCE.sh [--offline|--help]' >&2
  exit 2
fi
case "${1-}" in
  ''|--offline) ;;
  --help)
    echo 'Usage: sh REPRODUCE.sh [--offline|--help]'
    echo 'Replay all six frozen sweeps in a disposable directory, without model or network calls.'
    exit 0 ;;
  *)
    echo 'This frozen publication release accepts --offline or --help only; live collection is disabled.' >&2
    exit 2 ;;
esac
TASK_PYTHON="${ABSORBERS_PYTHON:-python3}"
case "$TASK_PYTHON" in
  /*) ;;
  */*) TASK_PYTHON="$(pwd)/$TASK_PYTHON" ;;
esac
cd "$(dirname "$0")"
export PYTHONHASHSEED=0
exec "$TASK_PYTHON" tools/reproduce.py
