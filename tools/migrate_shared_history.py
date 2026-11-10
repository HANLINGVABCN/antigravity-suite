"""One-time upgrade to fixed account archives and the original chat directory."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config import DATA_DIR
from profile_manager import ProfileManager
from discovery import engine_processes


def main():
    parser=argparse.ArgumentParser(description='停止后台和反重力后，一次性合并旧聊天，保留原账号存档')
    parser.add_argument('--data-dir',type=Path,default=DATA_DIR)
    args=parser.parse_args();data=args.data_dir.resolve()
    cfg=json.loads((data/'config.json').read_text(encoding='utf-8'))
    manager=ProfileManager(data,cfg.get('profiles_dir',data/'profiles'),cfg.get('runtime_home',data/'runtime-home'))
    native=manager.native_runtime.binding['native'] if manager.native_runtime.binding else manager.native_runtime.select()
    if not native:
        home=manager.legacy_home
        native=dict(native_home=str(home),native_uid=home.stat().st_uid)
    for instance in engine_processes():
        if instance['env'].get('HOME')==native['native_home']:parser.error('请先关闭正在使用该运行目录的反重力')
    for path in Path('/proc').iterdir() if Path('/proc').is_dir() else []:
        if not path.name.isdigit() or int(path.name)==os.getpid():continue
        try:command=(path/'cmdline').read_bytes().decode(errors='replace')
        except OSError:continue
        if 'server.py' in command and str(data.parent) in command:parser.error('请先停止此项目后台服务')
    changed=manager.migrate_account_layout_once(native)
    print('旧聊天已核对合并，账号目录保持原位' if changed else '已经完成升级，没有重复合并')
    print('固定聊天位置：'+str(manager.history.root))
    print('没有生成整份迁移备份，旧目录及原文件保留')


if __name__=='__main__':main()
