#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo '请使用 sudo bash uninstall.sh'; exit 1; fi
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SERVICE_PLAN=$(mktemp /tmp/antigravity-suite-uninstall-XXXXXX.json)
trap 'rm -f -- "$SERVICE_PLAN"' EXIT
ARGS=(--source "$SOURCE_DIR")
if [[ -n ${ANTIGRAVITY_SUITE_DATA:-} ]]; then ARGS+=(--data-dir "$ANTIGRAVITY_SUITE_DATA"); fi
if [[ -n ${ANTIGRAVITY_SUPERVISOR_CONFIG:-} ]]; then ARGS+=(--supervisor-config "$ANTIGRAVITY_SUPERVISOR_CONFIG"); fi
python3 "$SOURCE_DIR/installation_service.py" prepare --plan "$SERVICE_PLAN" "${ARGS[@]}"
mapfile -t VALUES < <(python3 - "$SERVICE_PLAN" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]));print(p['install_dir']);print(p['data_dir'])
PY
)
INSTALL_DIR=${VALUES[0]}
DATA_DIR=${VALUES[1]}
python3 "$SOURCE_DIR/installation_service.py" stop --plan "$SERVICE_PLAN"
if [[ -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  if [[ -f "$INSTALL_DIR/cli_proxy.py" ]]; then
    ANTIGRAVITY_SUITE_DATA="$DATA_DIR" "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/cli_proxy.py"
  fi
  "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/desktop_guard.py" --restore --data-dir "$DATA_DIR"
fi
python3 "$SOURCE_DIR/installation_service.py" remove --plan "$SERVICE_PLAN"
echo '本项目服务已移除，源码、账号、聊天记录与备份保留。'
echo "源码：$INSTALL_DIR；数据：$DATA_DIR"
