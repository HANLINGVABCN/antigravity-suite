import os
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

import installation_service as module


pytestmark=pytest.mark.skipif(os.name=='nt',reason='Linux service configurations use absolute POSIX paths')


def test_existing_supervisor_program_reuses_its_source_and_data_and_preserves_other_sections(tmp_path,monkeypatch):
    root=tmp_path/'project';root.mkdir();data=root/'data';folder=tmp_path/'supervisor';folder.mkdir()
    cfg=folder/'supervisord.conf';program=folder/'program.conf'
    cfg.write_text('[unix_http_server]\nfile='+str(folder/'rpc.sock')+'\n[include]\nfiles='+str(folder/'*.conf')+'\n')
    program.write_text('[program:other]\ncommand=/bin/sleep 300\n[program:antigravity-suite-improved]\ncommand='+str(root/'.venv/bin/python')+' '+str(root/'server.py')+'\nenvironment=ANTIGRAVITY_SUITE_DATA="'+str(data)+'"\n')
    monkeypatch.setattr(module.xmlrpc.client,'ServerProxy',lambda *args,**kwargs:SimpleNamespace(supervisor=SimpleNamespace(getState=lambda:dict(statecode=1))))
    result=module.prepare(tmp_path/'downloaded',supervisor_config=str(cfg))
    assert result['install_dir']==str(root) and result['data_dir']==str(data)
    assert not result['owned_supervisor'] and result['program_file']==str(program)
    module.update_section(program,'program:antigravity-suite-improved',module.program_text(result))
    assert '[program:other]\ncommand=/bin/sleep 300\n' in program.read_text()
    assert program.read_text().count('[program:antigravity-suite-improved]')==1


def test_mismatched_existing_installation_never_creates_duplicate_service(tmp_path,monkeypatch):
    cfg=tmp_path/'supervisord.conf';cfg.write_text('fixture')
    found=dict(controller=dict(kind='supervisor',name=module.NAME,socket='/run/fixture.sock'),supervisor_config=str(cfg),install_dir=str(tmp_path/'existing'),data_dir=str(tmp_path/'existing/data'))
    monkeypatch.setattr(module,'read_supervisor',lambda _:dict(found))
    with pytest.raises(ValueError,match='重复后台'):module.prepare(tmp_path,install_dir=str(tmp_path/'other'),supervisor_config=str(cfg))


def test_supervisor_command_quotes_paths_and_escapes_interpolation(tmp_path):
    root=tmp_path/'project with 100%';data=root/'data with spaces'
    text=module.program_text(dict(install_dir=str(root),data_dir=str(data),controller=dict(name=module.NAME)))
    command=next(line.split('=',1)[1] for line in text.splitlines() if line.startswith('command='))
    assert shlex.split(command.replace('%%','%'))==[str(root/'.venv/bin/python'),str(root/'server.py')]
    assert '100%%' in text and 'stopasgroup=false' in text


def test_no_systemd_and_no_supervisor_selects_owned_daemon(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'read_supervisor',lambda _:None)
    p=module.prepare(tmp_path,install_dir=str(tmp_path/'project'),data_dir=str(tmp_path/'private'),manager='supervisor')
    assert p['owned_supervisor'] and p['controller']['kind']=='supervisor'
    assert p['supervisor_config']==str(tmp_path/'private/supervisor/supervisord.conf')
    assert p['controller']['socket'].startswith('/run/antigravity-suite-')


def test_real_existing_supervisor_registration_and_removal_preserve_other_program(tmp_path):
    import shutil,subprocess,sys,tempfile,time
    if not shutil.which('supervisord'):pytest.skip('Needs local supervisor CLI for integration')
    with tempfile.TemporaryDirectory(prefix='agy-svc-',dir='/tmp') as folder:
        base=Path(folder);root=base/'project';data=root/'data';data.mkdir(parents=True)
        (root/'.venv/bin').mkdir(parents=True);(root/'.venv/bin/python').symlink_to(sys.executable)
        (root/'server.py').write_text('import time\ntime.sleep(300)\n')
        cfg=base/'supervisord.ini';program=base/'program.conf';socket=base/'rpc.sock'
        program.write_text('[program:other]\ncommand=/bin/sleep 300\nautostart=true\nstartsecs=0\n')
        cfg.write_text('[unix_http_server]\nfile='+str(socket)+'\n[supervisord]\nlogfile='+str(base/'daemon.log')+'\npidfile='+str(base/'pid')+'\n[rpcinterface:supervisor]\nsupervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface\n[include]\nfiles='+str(base/'*.conf')+'\n')
        proc=subprocess.Popen(['supervisord','-n','-c',str(cfg)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(60):
                if socket.exists():break
                time.sleep(.1)
            plan=module.prepare(root,install_dir=str(root),data_dir=str(data),supervisor_config=str(cfg))
            assert not plan['owned_supervisor'];rpc=module.server(plan)
            pid=rpc.supervisor.getProcessInfo('other')['pid'];assert pid
            module.register(plan);module.start(plan)
            for _ in range(60):
                if module.running(plan):break
                time.sleep(.1)
            assert module.running(plan)
            module.stop(plan);assert plan['was_active']
            module.start(plan)
            for _ in range(60):
                if module.running(plan):break
                time.sleep(.1)
            module.remove(plan)
            assert rpc.supervisor.getProcessInfo('other')['pid']==pid
            assert 'program:antigravity-suite-improved' not in Path(plan['program_file']).read_text()
        finally:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()


def test_owned_supervisor_starts_reuses_record_and_removes_its_own_service(tmp_path,monkeypatch):
    import shutil,sys,tempfile,time
    if not shutil.which('supervisord'):pytest.skip('Needs local supervisor CLI for integration')
    monkeypatch.setattr(module,'container_init_directory',lambda:None)
    with tempfile.TemporaryDirectory(prefix='agy-own-',dir='/tmp') as folder:
        base=Path(folder);root=base/'project';data=root/'data';data.mkdir(parents=True)
        (root/'.venv/bin').mkdir(parents=True);(root/'.venv/bin/python').symlink_to(sys.executable)
        (root/'server.py').write_text('import time\ntime.sleep(300)\n')
        config=data/'supervisor/supervisord.conf'
        plan=dict(controller=dict(kind='supervisor',socket=str(base/'rpc.sock'),name=module.NAME),install_dir=str(root),data_dir=str(data),supervisor_config=str(config),program_file=str(config.parent/'program.conf'),owned_supervisor=True,was_active=False)
        try:
            module.register(plan);module.start(plan)
            for _ in range(60):
                if module.running(plan):break
                time.sleep(.1)
            assert module.running(plan)
            pid=module.server(plan).supervisor.getPID()
            again=module.prepare(root,install_dir=str(root),data_dir=str(data),manager='supervisor')
            assert again['owned_supervisor'] and again['supervisor_config']==str(config)
            module.start(again);assert module.server(again).supervisor.getPID()==pid
            module.remove(again)
            assert not (data/module.RECORD).exists()
        finally:
            try:module.server(plan).supervisor.shutdown()
            except Exception:pass
