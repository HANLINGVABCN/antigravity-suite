#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo '请使用 sudo bash uninstall.sh'; exit 1; fi
systemctl stop antigravity-suite-improved.service 2>/dev/null || true
suite_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$suite_root/.venv/bin/python" ]]; then
    "$suite_root/.venv/bin/python" "$suite_root/desktop_guard.py" --restore --data-dir "${ANTIGRAVITY_SUITE_DATA:-/var/lib/antigravity-suite-improved}"
fi
systemctl disable antigravity-suite-improved.service 2>/dev/null || true
rm -f /etc/systemd/system/antigravity-suite-improved.service
systemctl daemon-reload
echo '服务已移除。源码、档案、登录凭据和备份均保留。'
echo '源码：/opt/antigravity-suite-improved；数据：/var/lib/antigravity-suite-improved'
