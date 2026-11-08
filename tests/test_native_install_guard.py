import copy
import json
import os
from pathlib import Path

import pytest

import native_install_guard as guard


def instance(tmp_path,pid=123):
    return dict(pid=pid,start_time='test-start',uid=0,binary=str(tmp_path/'app/antigravity'),config_dir=str(tmp_path/'home/.config/Antigravity'))


def test_declining_close_cancels_before_any_process_is_stopped(tmp_path,monkeypatch):
    process=instance(tmp_path);messages=[]
    monkeypatch.setattr(guard,'instances',lambda:[process])
    monkeypatch.setattr(guard,'stop_process',lambda *a,**kw:pytest.fail('Cannot stop without approval'))
    with pytest.raises(ValueError,match='已取消安装'):
        guard.prepare(reader=lambda prompt:'',writer=messages.append)
    assert '独占锁' in '\n'.join(messages) and '保留登录文件和聊天记录' in '\n'.join(messages)


def test_noninteractive_requires_explicit_native_close_option(tmp_path,monkeypatch):
    process=instance(tmp_path);monkeypatch.setattr(guard,'instances',lambda:[process])
    with pytest.raises(ValueError,match='--close-antigravity'):guard.prepare(non_interactive=True,writer=lambda _:None)
    plan=guard.prepare(non_interactive=True,close_requested=True,writer=lambda _:None)
    assert plan['approved'] and plan['instances']==[process]


def test_close_only_approved_runtime_and_rechecks_that_it_stopped(tmp_path,monkeypatch):
    process=instance(tmp_path);current=[process];calls=[]
    monkeypatch.setattr(guard,'instances',lambda:copy.deepcopy(current))
    monkeypatch.setattr(guard,'process_info',lambda pid:dict(pid=pid,start_time=process['start_time']))
    plan=guard.prepare(reader=lambda prompt:'y',writer=lambda _:None)
    def stopped(p,**kwargs):
        calls.append((p.pid,kwargs['grace_period']));current.clear()
    monkeypatch.setattr(guard,'stop_process',stopped)
    guard.close(plan)
    assert calls==[(123,15)]


def test_new_unapproved_environment_is_not_closed_even_using_same_binary(tmp_path,monkeypatch):
    approved=instance(tmp_path);other=dict(approved,pid=456,config_dir=str(tmp_path/'unrelated-home'))
    monkeypatch.setattr(guard,'instances',lambda:[other])
    monkeypatch.setattr(guard,'stop_process',lambda *a,**kw:pytest.fail('Must not close a newly discovered environment'))
    with pytest.raises(ValueError,match='确认的范围'):
        guard.close(dict(version=1,approved=True,instances=[approved]))


def test_no_running_instance_needs_no_confirmation(tmp_path,monkeypatch):
    monkeypatch.setattr(guard,'instances',lambda:[])
    plan=guard.prepare(reader=lambda _:pytest.fail('No reason to ask'),writer=lambda _:None)
    assert not plan['approved'] and plan['instances']==[]


def test_process_reappearing_after_close_reports_lock_risk(tmp_path,monkeypatch):
    process=instance(tmp_path);monkeypatch.setattr(guard,'instances',lambda:[process])
    monkeypatch.setattr(guard,'process_info',lambda pid:dict(pid=pid,start_time=process['start_time']))
    monkeypatch.setattr(guard,'stop_process',lambda *a,**kw:None)
    with pytest.raises(ValueError,match='可能仍占用独占锁'):
        guard.close(dict(version=1,approved=True,instances=[process]))


@pytest.mark.skipif(os.name=='nt',reason='Real Linux processes, flock and terminal')
def test_terminal_confirmation_releases_fixture_lock_only_after_approval(tmp_path):
    import fcntl
    import pty
    import select
    import shutil
    import subprocess
    import sys
    import time
    from discovery import process_info
    from process_tracking import stop_process

    # Disposable ELF named like the client; never touches a real native HOME or account.
    binary=tmp_path/'fixture/antigravity';binary.parent.mkdir()
    base=getattr(sys,'_base_executable',sys.executable);shutil.copy2(Path(base).resolve(),binary)
    (binary.parent/'resources').mkdir();(binary.parent/'resources/app.asar').write_text('fixture')
    home=tmp_path/'fixture-home';home.mkdir();lock=home/'exclusive.lock'
    source="import fcntl,time;f=open("+repr(str(lock))+",'w');fcntl.flock(f,fcntl.LOCK_EX);print('locked',flush=True);time.sleep(60)"
    process=subprocess.Popen([str(binary),'-u','-c',source],env=dict(os.environ,HOME=str(home)),stdout=subprocess.PIPE,text=True,start_new_session=True)
    unrelated=subprocess.Popen(['/bin/sleep','60'],start_new_session=True)
    root=Path(__file__).resolve().parents[1];wrapper=tmp_path/'run_fixture_guard.py'
    wrapper.write_text("import sys;sys.path.insert(0,"+repr(str(root))+");import native_install_guard as g;f=g.instances;g.instances=lambda:[p for p in f() if p['binary']=="+repr(str(binary))+'];raise SystemExit(g.main())\n')
    plan=tmp_path/'native-plan.json'
    def terminal(answer):
        master,slave=pty.openpty()
        cli=subprocess.Popen([sys.executable,str(wrapper),'prepare','--plan',str(plan)],stdin=slave,stdout=slave,stderr=slave,start_new_session=True)
        os.close(slave);output=bytearray()
        try:
            deadline=time.monotonic()+10
            while '是否关闭以上反重力'.encode() not in output:
                assert time.monotonic()<deadline,'Expected the actual confirmation prompt'
                if select.select([master],[],[],.1)[0]:output.extend(os.read(master,8192))
            os.write(master,(answer+'\n').encode());return cli.wait(timeout=10),output.decode(errors='replace')
        finally:
            if cli.poll() is None:cli.kill();cli.wait()
            os.close(master)
    try:
        assert process.stdout.readline().strip()=='locked'
        code,transcript=terminal('n');assert code==1 and process.poll() is None and not plan.exists()
        assert '独占锁' in transcript
        with lock.open() as held:
            with pytest.raises(BlockingIOError):fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        code,_=terminal('y');assert code==0 and json.loads(plan.read_text())['approved']
        subprocess.run([sys.executable,str(wrapper),'close','--plan',str(plan)],check=True,capture_output=True,text=True,timeout=25)
        process.wait(timeout=5);assert unrelated.poll() is None
        with lock.open() as held:fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:
        stop_process(process);stop_process(unrelated);process.stdout.close()
