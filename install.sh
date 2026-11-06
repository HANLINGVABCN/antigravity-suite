#!/usr/bin/env bash
set -euo pipefail
umask 077
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
INSTALL_DIR=${ANTIGRAVITY_INSTALL_DIR:-/opt/antigravity-suite-improved}
DATA_DIR=${ANTIGRAVITY_SUITE_DATA:-/var/lib/antigravity-suite-improved}
SERVICE_NAME=antigravity-suite-improved
if [[ $EUID -ne 0 ]]; then echo '请使用 sudo bash install.sh'; exit 1; fi
if [[ ! -d /run/systemd/system ]]; then
  echo '此脚本适用于 systemd 主机。容器或 Supervisor 部署请参阅 docs/deployment.md。'
  exit 1
fi
case "$INSTALL_DIR" in /opt/antigravity-suite*) ;; *) echo '安装目录必须位于 /opt/antigravity-suite*'; exit 1;; esac
case "$DATA_DIR" in /var/lib/antigravity-suite*) ;; *) echo '数据目录必须位于 /var/lib/antigravity-suite*'; exit 1;; esac
WAS_ACTIVE=0
if systemctl is-active --quiet "$SERVICE_NAME.service"; then
  WAS_ACTIVE=1
  systemctl stop "$SERVICE_NAME.service"
fi
trap 'if [[ $WAS_ACTIVE -eq 1 ]]; then systemctl start "$SERVICE_NAME.service" || true; fi' ERR
if [[ -f "$DATA_DIR/config.json" ]]; then
  BACKUP_DIR=/var/backups/antigravity-suite-improved
  mkdir -p "$BACKUP_DIR"
  tar -czf "$BACKUP_DIR/data-$(date +%Y%m%d-%H%M%S).tar.gz" -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"
  echo "升级前已备份数据到 $BACKUP_DIR"
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 xvfb x11-utils curl ca-certificates libnss3 libatk1.0-0 libatk-bridge2.0-0 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libgtk-3-0
mkdir -p "$INSTALL_DIR" "$DATA_DIR"
python3 - "$SOURCE_DIR" "$INSTALL_DIR" <<'PY'
import pathlib,shutil,sys
src,dst=map(pathlib.Path,sys.argv[1:])
if src.resolve()!=dst.resolve():
    for p in src.iterdir():
        if p.name in ('.git','.github','data','profiles','.venv','__pycache__','.pytest_cache'): continue
        target=dst/p.name
        if p.is_dir(): shutil.copytree(p,target,dirs_exist_ok=True)
        else: shutil.copy2(p,target)
PY
if ! python3 -m venv "$INSTALL_DIR/.venv" > "$INSTALL_DIR/venv-bootstrap.log" 2>&1; then
  echo '系统未提供可用的 venv/ensurepip，使用 PyPA 官方 virtualenv 建立独立环境。'
  PYTHON_MINOR=$(python3 -c 'import sys; print("%s.%s" % sys.version_info[:2])')
  BOOTSTRAP=$(mktemp "$INSTALL_DIR/virtualenv-XXXXXX.pyz")
  curl -fSL --proto '=https' --proto-redir '=https' --max-time 120 "https://bootstrap.pypa.io/virtualenv/$PYTHON_MINOR/virtualenv.pyz" -o "$BOOTSTRAP"
  python3 "$BOOTSTRAP" "$INSTALL_DIR/.venv"
  rm -f "$BOOTSTRAP"
fi
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"
cd "$INSTALL_DIR"
ANTIGRAVITY_SUITE_DATA="$DATA_DIR" "$INSTALL_DIR/.venv/bin/python" - <<'PY'
from config import load_config
cfg=load_config()
print('监听地址：%s:%s' % (cfg['listen_host'],cfg['listen_port']))
PY
cat > "/etc/systemd/system/$SERVICE_NAME.service" <<UNIT
[Unit]
Description=Antigravity Suite Improved
After=network.target
[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
Environment=ANTIGRAVITY_SUITE_DATA=$DATA_DIR
Environment=PYTHONUNBUFFERED=1
UMask=0077
ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/server.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=70
KillMode=mixed
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
trap - ERR
echo "服务已启动。首次密码保存在 $DATA_DIR/initial-credentials.txt；升级不会重置密码。"
echo '反重力本体可在网页检测并安装，管理登录和档案导入不依赖本体。'
echo '默认仅监听本机。通过 SSH 转发或你选择的反向代理访问。'
