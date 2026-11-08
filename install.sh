#!/usr/bin/env bash
set -euo pipefail
umask 077
WIZARD_ARGS=()
for ARG in "$@"; do
  case "$ARG" in
    --non-interactive) WIZARD_ARGS=(--non-interactive);;
    --help|-h)
      echo '用法：sudo bash install.sh [--non-interactive]'
      echo '自动识别 systemd、已有 Supervisor 或容器独立 Supervisor。'
      echo '同一安装向导选择端口、管理员用户名、随机或自定义密码。'
      echo '已有部署自动复用源码和数据目录，保留密码、验证器、配置和节点。'
      echo '--non-interactive：首次使用 18888/admin/随机密码；升级保留设置。'
      echo '可选环境变量：ANTIGRAVITY_INSTALL_DIR、ANTIGRAVITY_SUITE_DATA、ANTIGRAVITY_SERVICE_MANAGER、ANTIGRAVITY_SUPERVISOR_CONFIG。'
      exit 0;;
    *) echo "未知选项：$ARG（使用 --help 查看用法）"; exit 1;;
  esac
done
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $EUID -ne 0 ]]; then echo '请使用 sudo bash install.sh'; exit 1; fi
if ! command -v apt-get >/dev/null 2>&1; then echo '目前自动安装依赖支持 Debian/Ubuntu 及其容器。'; exit 1; fi
export DEBIAN_FRONTEND=noninteractive
if ! command -v python3 >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends python3
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else "需要 Python 3.9 或更高版本")'
PLAN_FILE=$(mktemp /tmp/antigravity-suite-install-XXXXXX.json)
SERVICE_PLAN=$(mktemp /tmp/antigravity-suite-service-XXXXXX.json)
RECOVERY_DIR=$(mktemp -d /tmp/antigravity-suite-install-recovery-XXXXXX)
cp -- "$SOURCE_DIR/installation_service.py" "$SOURCE_DIR/service_control.py" "$SOURCE_DIR/config.py" "$RECOVERY_DIR/"
STOPPED=0
SUCCEEDED=0
BACKUP_DIR=''
cleanup() {
  RESULT=$?
  if [[ $RESULT -ne 0 && $STOPPED -eq 1 && $SUCCEEDED -eq 0 ]]; then
    echo '安装未完成，正在恢复原源码与服务，私有备份保留。'
    if [[ -n "$BACKUP_DIR" && -f "$BACKUP_DIR/source-state.json" ]]; then
      python3 - "$BACKUP_DIR" "$INSTALL_DIR" "$DATA_DIR" <<'PY'
import json,pathlib,shutil,sys
backup,root,data=map(pathlib.Path,sys.argv[1:])
for name,existed in json.loads((backup/'source-state.json').read_text()).items():
    target=root/name;original=backup/'source'/name
    if existed:
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
    else:target.unlink(missing_ok=True)
if (backup/'config.json').is_file():shutil.copy2(backup/'config.json',data/'config.json')
record=backup/'installation-service.json'
if record.is_file():shutil.copy2(record,data/'installation-service.json')
else:(data/'installation-service.json').unlink(missing_ok=True)
PY
    fi
    python3 "$RECOVERY_DIR/installation_service.py" restore --plan "$SERVICE_PLAN" || true
  fi
  rm -f -- "$PLAN_FILE" "$SERVICE_PLAN"
  rm -f -- "$RECOVERY_DIR/installation_service.py" "$RECOVERY_DIR/service_control.py" "$RECOVERY_DIR/config.py"
  # Python cache is confined to this freshly allocated temporary directory.
  if [[ "$RECOVERY_DIR" == /tmp/antigravity-suite-install-recovery-* ]]; then rm -rf -- "$RECOVERY_DIR"; fi
}
trap cleanup EXIT
SERVICE_ARGS=(--source "$SOURCE_DIR" --manager "${ANTIGRAVITY_SERVICE_MANAGER:-auto}")
if [[ -n ${ANTIGRAVITY_INSTALL_DIR:-} ]]; then SERVICE_ARGS+=(--install-dir "$ANTIGRAVITY_INSTALL_DIR"); fi
if [[ -n ${ANTIGRAVITY_SUITE_DATA:-} ]]; then SERVICE_ARGS+=(--data-dir "$ANTIGRAVITY_SUITE_DATA"); fi
if [[ -n ${ANTIGRAVITY_SUPERVISOR_CONFIG:-} ]]; then SERVICE_ARGS+=(--supervisor-config "$ANTIGRAVITY_SUPERVISOR_CONFIG"); fi
python3 "$SOURCE_DIR/installation_service.py" prepare --plan "$SERVICE_PLAN" "${SERVICE_ARGS[@]}"
mapfile -t SERVICE_VALUES < <(python3 - "$SERVICE_PLAN" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]));print(p['install_dir']);print(p['data_dir']);print(p['controller']['kind'])
PY
)
INSTALL_DIR=${SERVICE_VALUES[0]}
DATA_DIR=${SERVICE_VALUES[1]}
MANAGER=${SERVICE_VALUES[2]}
python3 "$SOURCE_DIR/configure_install.py" --data-dir "$DATA_DIR" --prepare "$PLAN_FILE" "${WIZARD_ARGS[@]}"
echo '正在准备依赖，已有后台继续运行。'
apt-get update
PACKAGES=(python3 xvfb x11-utils curl ca-certificates libnss3 libatk1.0-0 libatk-bridge2.0-0 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libgtk-3-0)
if [[ $MANAGER == supervisor ]]; then PACKAGES+=(supervisor); fi
apt-get install -y --no-install-recommends "${PACKAGES[@]}"
mkdir -p "$INSTALL_DIR"
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  if ! python3 -m venv "$INSTALL_DIR/.venv" > "$INSTALL_DIR/venv-bootstrap.log" 2>&1; then
    echo '系统未提供可用的 venv/ensurepip，使用 PyPA 官方 virtualenv 建立独立环境。'
    PYTHON_MINOR=$(python3 -c 'import sys; print("%s.%s" % sys.version_info[:2])')
    BOOTSTRAP=$(mktemp "$INSTALL_DIR/virtualenv-XXXXXX.pyz")
    curl -fSL --proto '=https' --proto-redir '=https' --max-time 120 "https://bootstrap.pypa.io/virtualenv/$PYTHON_MINOR/virtualenv.pyz" -o "$BOOTSTRAP"
    python3 "$BOOTSTRAP" "$INSTALL_DIR/.venv"
    rm -f -- "$BOOTSTRAP"
  fi
fi
"$INSTALL_DIR/.venv/bin/python" -m pip install -r "$SOURCE_DIR/requirements.txt"
echo '依赖已准备，正在保存现有后台并检查端口。'
python3 "$SOURCE_DIR/installation_service.py" stop --plan "$SERVICE_PLAN"
STOPPED=1
python3 "$SOURCE_DIR/configure_install.py" --data-dir "$DATA_DIR" --check-port "$PLAN_FILE"
BACKUP_DIR="$(dirname "$DATA_DIR")/antigravity-suite-backups/install-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$BACKUP_DIR" "$DATA_DIR"
if [[ -f "$DATA_DIR/config.json" ]]; then
  cp -- "$DATA_DIR/config.json" "$BACKUP_DIR/config.json"
  tar -czf "$BACKUP_DIR/private-data.tar.gz" --exclude='./updates' -C "$DATA_DIR" .
  echo "升级前已备份数据到 $BACKUP_DIR"
fi
if [[ -f "$DATA_DIR/installation-service.json" ]]; then cp -- "$DATA_DIR/installation-service.json" "$BACKUP_DIR/installation-service.json"; fi
python3 - "$SOURCE_DIR" "$INSTALL_DIR" "$BACKUP_DIR" <<'PY'
import json,pathlib,shutil,sys
src,dst,backup=map(pathlib.Path,sys.argv[1:])
allowed={'.gitattributes','.gitignore','LICENSE','VERSION'}
folders={'static','tools','docs','examples','tests','systemd','.github'}
files=[p for p in src.iterdir() if p.is_file() and (p.suffix in {'.py','.sh','.txt','.md','.ini'} or p.name in allowed)]
files += [p for folder in folders for p in (src/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.sp_' not in p.name]
state={}
for p in files:
    if p.is_symlink():continue
    name=p.relative_to(src).as_posix();target=dst/name;state[name]=target.is_file()
    if target.is_file():
        old=backup/'source'/name;old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,old)
(backup/'source-state.json').write_text(json.dumps(state))
for p in files:
    if p.is_symlink():continue
    target=dst/p.relative_to(src)
    if src.resolve()!=dst.resolve():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
PY
"$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/configure_install.py" --data-dir "$DATA_DIR" --apply "$PLAN_FILE"
HEALTH_URL=$(ANTIGRAVITY_SUITE_DATA="$DATA_DIR" "$INSTALL_DIR/.venv/bin/python" - <<'PY'
from config import load_config
cfg=load_config();host=cfg['listen_host']
host='127.0.0.1' if host=='0.0.0.0' else '::1' if host=='::' else host
if ':' in host:host='['+host+']'
print('http://%s:%s/?portal=1' % (host,cfg['listen_port']))
PY
)
python3 "$INSTALL_DIR/installation_service.py" register --plan "$SERVICE_PLAN"
python3 "$INSTALL_DIR/installation_service.py" start --plan "$SERVICE_PLAN"
HEALTHY=0
for ATTEMPT in {1..30}; do
  if python3 "$INSTALL_DIR/installation_service.py" running --plan "$SERVICE_PLAN" && curl -fsS --noproxy '*' --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then HEALTHY=1; break; fi
  sleep 1
done
if [[ $HEALTHY -ne 1 ]]; then
  echo "后台未通过启动检查，请检查 $DATA_DIR/suite.log 或服务日志。"
  exit 1
fi
SUCCEEDED=1
echo "后台已启动：$HEALTH_URL"
echo "服务日志：$DATA_DIR/suite.log（systemd 主机也可使用 journalctl）"
echo '反重力本体可在网页检测并安装。首次自定义密码使用刚才的输入；随机密码查看上方私有文件。'
echo '升级继续使用现有密码与监听设置。首次默认仅监听本机，可通过 SSH 转发或反向代理访问。'
if [[ $MANAGER == supervisor ]]; then
  python3 - "$SERVICE_PLAN" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
if p['owned_supervisor']:
    print('容器独立后台启动命令：sh '+p['boot_script'])
    print('已登记 s6 容器启动脚本。' if p.get('boot_link') else '无容器启动钩子：重启容器后执行此启动命令，或把它加入容器启动命令。')
else:print('已复用现有 Supervisor，仅管理本项目服务。')
print('重建容器时请持久化源码、数据目录和服务启动配置。')
PY
fi
