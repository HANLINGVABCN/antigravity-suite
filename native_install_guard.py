"""Ask before closing running native clients, then recheck before changing locked files."""
import argparse
import json
import os
import sys
from pathlib import Path

from config import atomic_json
from discovery import config_dir_for,engine_processes,process_info,validate_binary
from process_tracking import ExistingProcess,stop_process


def instances():
    result=[]
    for item in engine_processes():
        if item['state']=='Z':continue
        # Do not treat an unrelated command with the same filename as this Electron app.
        if not validate_binary(item['executable'],check_libraries=False)['usable']:continue
        result.append(dict(pid=item['pid'],start_time=item['start_time'],uid=item['uid'],
            binary=str(Path(item['executable']).resolve()),config_dir=str(config_dir_for(item))))
    return sorted(result,key=lambda p:p['pid'])


def scope(item):return (item['binary'],item['uid'],item['config_dir'])


def prepare(non_interactive=False,close_requested=False,reader=input,writer=print):
    found=instances()
    if not found:
        writer('已检查反重力：当前没有运行中的实例。')
        return dict(version=1,approved=False,instances=[])
    writer('检测到 %d 个正在运行的反重力实例，可能持有配置目录、数据库或本体资源的独占锁。'%len(found))
    for item in found:
        writer('  PID %s，用户 UID %s，配置目录：%s'%(item['pid'],item['uid'],item['config_dir']))
    writer('安装需要先关闭这些实例。请先保存正在编辑的内容；关闭程序保留登录文件和聊天记录，不执行退出登录。')
    if close_requested:
        writer('已通过 --close-antigravity 明确允许关闭以上反重力实例。')
    elif non_interactive:
        raise ValueError('发现反重力正在运行，本次安装尚未继续。请先自行关闭，或添加 --close-antigravity 明确允许安装器关闭。')
    else:
        while True:
            answer=reader('是否关闭以上反重力，继续安装？[y/N]：').strip().lower()
            if answer in ('y','yes','是'):break
            if answer in ('','n','no','否'):
                raise ValueError('已取消安装：反重力仍在运行，未关闭程序或修改安装配置。')
            writer('请输入 y（关闭并继续）或 n（取消安装）。')
    return dict(version=1,approved=True,instances=found)


def close(plan):
    if plan.get('version')!=1:raise ValueError('反重力关闭计划无效')
    permitted={scope(p) for p in plan.get('instances',[])} if plan.get('approved') else set()
    found=instances()
    unexpected=[p for p in found if scope(p) not in permitted]
    if unexpected:
        raise ValueError('安装期间发现新的反重力实例（PID %s），未在刚才确认的范围内；请关闭后重新运行安装。'%','.join(str(p['pid']) for p in unexpected))
    for item in found:
        current=process_info(item['pid'])
        if current and current['start_time']==item['start_time']:
            print('正在关闭反重力 PID %s，等待配置锁释放…'%item['pid'],flush=True)
            stop_process(ExistingProcess(current),grace_period=15)
    ensure_stopped()
    print('反重力已停止，配置文件保留；继续安装。',flush=True)


def ensure_stopped():
    found=instances()
    if found:
        raise ValueError('反重力仍在运行或被其他服务重新启动（PID %s），可能仍占用独占锁。请停止对应实例或其自动重启服务，再重新安装。'%','.join(str(p['pid']) for p in found))


def main(argv=None):
    parser=argparse.ArgumentParser(description='安装前反重力运行检查与关闭确认')
    parser.add_argument('action',choices=('prepare','close','check'))
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--non-interactive',action='store_true')
    parser.add_argument('--close-antigravity',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.action=='prepare':
            if not args.non_interactive and not args.close_antigravity and not sys.stdin.isatty():
                if instances():raise ValueError('反重力正在运行，但当前没有交互终端。请关闭后重试，或明确添加 --non-interactive --close-antigravity。')
            plan=prepare(args.non_interactive,args.close_antigravity);atomic_json(args.plan,plan)
        elif args.action=='close':close(json.loads(args.plan.read_text(encoding='utf-8')))
        else:ensure_stopped()
        return 0
    except (EOFError,KeyboardInterrupt):
        print('\n已取消安装，尚未关闭反重力。',file=sys.stderr);return 130
    except (OSError,ValueError,KeyError,TypeError) as error:
        print('安装前检查未通过：'+str(error),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
