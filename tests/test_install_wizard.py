import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config import atomic_json, check_password, default_config, hash_password
from configure_install import apply_install, check_listen_port, main, prepare_install, read_existing


def answers(values):
    iterator = iter(values)
    return lambda prompt: next(iterator)


def existing_config(tmp_path):
    cfg = default_config(tmp_path)
    cfg.update(listen_port=19999, auth_user='my-admin', auth_password_hash=hash_password('recently-changed-password'),
               mfa_enabled=True, mfa_secret='JBSWY3DPEHPK3PXP', mfa_recovery_hashes=['private-test-recovery-hash'],
               mfa_last_counter=123, active_profile_id='fixed-profile-id', default_profile_id='fixed-profile-id')
    atomic_json(tmp_path/'config.json',cfg)
    atomic_json(tmp_path/'profiles.json',[dict(id='fixed-profile-id',proxy_id='fixed-node-id')])
    atomic_json(tmp_path/'proxies.json',[dict(id='fixed-node-id',name='node')])
    atomic_json(tmp_path/'initial-credentials.txt',dict(username='old',password='obsolete-initial-password'))
    return cfg


def test_first_install_prompts_retry_bad_inputs_and_accepts_custom_password_without_plaintext(tmp_path,capsys):
    messages=[]
    password='custom-password with $() and spaces '
    plan=prepare_install(tmp_path,reader=answers(['0','65536','20080','bad user','my-admin','2']),
                         password_reader=answers(['short',password,'does-not-match',password,password]),writer=messages.append)
    assert not (tmp_path/'config.json').exists()
    cfg=apply_install(plan,tmp_path)
    assert cfg['listen_port']==20080 and cfg['auth_user']=='my-admin'
    assert check_password(password,cfg['auth_password_hash']) and not check_password(password.strip(),cfg['auth_password_hash'])
    assert any('两次输入' in message for message in messages)
    assert not (tmp_path/'initial-credentials.txt').exists()
    assert password not in json.dumps(plan,ensure_ascii=False) and password not in '\n'.join(messages)
    assert password not in capsys.readouterr().out


@pytest.mark.parametrize('non_interactive',[False,True])
def test_fresh_random_password_is_private_and_matches_login_hash(tmp_path,non_interactive):
    messages=[]
    plan=prepare_install(tmp_path,non_interactive,reader=answers(['','','']),writer=messages.append)
    cfg=apply_install(plan,tmp_path)
    credentials=json.loads((tmp_path/'initial-credentials.txt').read_text(encoding='utf-8'))
    assert cfg['listen_port']==18888 and cfg['auth_user']=='admin'
    assert credentials['username']=='admin' and len(credentials['password'])>=20
    assert check_password(credentials['password'],cfg['auth_password_hash'])
    assert credentials['password'] not in '\n'.join(messages)
    if os.name!='nt':
        assert (tmp_path/'config.json').stat().st_mode & 0o777 == 0o600
        assert (tmp_path/'initial-credentials.txt').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('non_interactive',[False,True])
def test_upgrade_keeps_changed_password_mfa_ids_and_bytes_without_asking_for_credentials(tmp_path,non_interactive):
    cfg=existing_config(tmp_path)
    before={path.name:path.read_bytes() for path in tmp_path.iterdir()}
    plan=prepare_install(tmp_path,non_interactive,reader=answers(['']),password_reader=lambda _:pytest.fail('Must not ask for password'))
    assert 'config' not in plan and 'initial_credentials' not in plan
    assert apply_install(plan,tmp_path)==cfg
    assert before=={path.name:path.read_bytes() for path in tmp_path.iterdir()}


def test_upgrade_port_choice_merges_recent_password_change_without_overwriting_other_settings(tmp_path):
    cfg=existing_config(tmp_path)
    private_before={name:(tmp_path/name).read_bytes() for name in ('profiles.json','proxies.json','initial-credentials.txt')}
    plan=prepare_install(tmp_path,reader=answers(['2','21080']))
    cfg['auth_password_hash']=hash_password('changed-again-during-the-wizard')
    cfg['session_secret']='recent-new-session-secret'
    cfg['mfa_last_counter']=124
    atomic_json(tmp_path/'config.json',cfg)
    after=apply_install(plan,tmp_path)
    assert after==dict(cfg,listen_port=21080)
    assert check_password('changed-again-during-the-wizard',after['auth_password_hash'])
    assert private_before=={name:(tmp_path/name).read_bytes() for name in private_before}


def test_port_changed_concurrently_causes_failure_without_overwrite(tmp_path):
    cfg=existing_config(tmp_path)
    plan=prepare_install(tmp_path,reader=answers(['2','21080']))
    cfg['listen_port']=22080
    atomic_json(tmp_path/'config.json',cfg)
    before=(tmp_path/'config.json').read_bytes()
    with pytest.raises(ValueError,match='重新运行'):
        apply_install(plan,tmp_path)
    assert (tmp_path/'config.json').read_bytes()==before


@pytest.mark.parametrize('bad',[
    '{not valid json',
    json.dumps(dict(auth_user='admin',listen_port=18888,auth_password_hash='')),
    json.dumps(dict(auth_user='admin',listen_port=18888,auth_password_hash='invalid-private-value')),
])
def test_corrupt_existing_config_never_generates_new_password(tmp_path,bad):
    (tmp_path/'config.json').write_text(bad,encoding='utf-8')
    with pytest.raises(ValueError,match='不会重置') as result:
        prepare_install(tmp_path,True)
    assert 'invalid-private-value' not in str(result.value)
    assert (tmp_path/'config.json').read_text(encoding='utf-8')==bad
    assert not (tmp_path/'initial-credentials.txt').exists()


def test_missing_config_with_existing_account_data_refuses_fresh_initialization(tmp_path):
    atomic_json(tmp_path/'profiles.json',[dict(id='existing-id')])
    with pytest.raises(ValueError,match='缺少 config.json'):
        prepare_install(tmp_path,True)
    assert not (tmp_path/'config.json').exists()


def test_occupied_port_is_rejected_before_installation_writes_any_config(tmp_path):
    import socket
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        plan=prepare_install(tmp_path,True)
        plan['config']['listen_port']=listener.getsockname()[1]
        with pytest.raises(ValueError,match='地址或端口无法使用'):
            check_listen_port(plan,tmp_path)
    assert not (tmp_path/'config.json').exists()


def test_available_port_check_keeps_existing_auth_and_data_unchanged(tmp_path):
    import socket
    cfg=existing_config(tmp_path)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        cfg['listen_port']=listener.getsockname()[1]
    atomic_json(tmp_path/'config.json',cfg)
    before={path.name:path.read_bytes() for path in tmp_path.iterdir()}
    check_listen_port(prepare_install(tmp_path,True),tmp_path)
    assert before=={path.name:path.read_bytes() for path in tmp_path.iterdir()}


def test_legacy_password_upgrade_is_preserved_for_normal_runtime_migration(tmp_path):
    cfg=dict(auth_user='legacy',listen_port=18888,auth_pass='old-legacy-password',mfa_enabled=False)
    atomic_json(tmp_path/'config.json',cfg)
    assert apply_install(prepare_install(tmp_path,True),tmp_path)==cfg
    assert read_existing(tmp_path)==cfg


def test_new_install_plan_cannot_overwrite_an_account_created_since_prepare(tmp_path):
    plan=prepare_install(tmp_path,True)
    original=existing_config(tmp_path)
    with pytest.raises(ValueError,match='拒绝'):
        apply_install(plan,tmp_path)
    assert read_existing(tmp_path)==original


def test_cancelling_during_prompt_does_not_apply_partial_values(tmp_path,monkeypatch):
    import configure_install
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    def cancel(*args,**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(configure_install,'prepare_install',cancel)
    assert main(['--data-dir',str(tmp_path)])==130
    assert not (tmp_path/'config.json').exists()


def test_noninteractive_cli_prepare_apply_has_no_password_in_stdout_and_uses_selected_directory(tmp_path):
    root=Path(__file__).parents[1]
    data=tmp_path/'private data';plan=tmp_path/'options.json'
    command=[sys.executable,str(root/'configure_install.py'),'--data-dir',str(data)]
    first=subprocess.run(command+['--prepare',str(plan),'--non-interactive'],capture_output=True,encoding='utf-8',check=True)
    assert not data.exists()
    second=subprocess.run(command+['--apply',str(plan)],capture_output=True,encoding='utf-8',check=True)
    credentials=json.loads((data/'initial-credentials.txt').read_text(encoding='utf-8'))
    assert credentials['password'] not in first.stdout+second.stdout+first.stderr+second.stderr
    cfg=read_existing(data)
    assert check_password(credentials['password'],cfg['auth_password_hash'])
    assert Path(cfg['profiles_dir'])==data/'profiles'


@pytest.mark.skipif(os.name=='nt',reason='Interactive terminal input is verified on Linux with a PTY')
def test_real_terminal_wizard_selects_port_user_and_hides_confirmed_password(tmp_path):
    import pty,select,time
    master,slave=pty.openpty()
    command=[sys.executable,str(Path(__file__).parents[1]/'configure_install.py'),'--data-dir',str(tmp_path/'private')]
    process=subprocess.Popen(command,stdin=slave,stdout=slave,stderr=slave,start_new_session=True)
    os.close(slave)
    transcript=bytearray()
    password='test-terminal-hidden-password'

    def answer_after(prompt,value):
        expected=prompt.encode('utf-8')
        deadline=time.monotonic()+10
        while expected not in transcript:
            if time.monotonic()>deadline:
                pytest.fail('Terminal prompt did not appear (private input omitted)')
            readable,_,_=select.select([master],[],[],.2)
            if readable:
                transcript.extend(os.read(master,8192))
        os.write(master,(value+'\n').encode('utf-8'))

    try:
        answer_after('后台监听端口 [18888]：','20580')
        answer_after('管理员用户名（','terminal-admin')
        answer_after('密码：1. 随机生成','2')
        answer_after('管理员密码（12–1024',password)
        answer_after('再次输入管理员密码：',password)
        assert process.wait(timeout=10)==0
        while select.select([master],[],[],.1)[0]:
            try:
                chunk=os.read(master,8192)
            except OSError:
                break
            if not chunk:
                break
            transcript.extend(chunk)
        cfg=read_existing(tmp_path/'private')
        assert cfg['listen_port']==20580 and cfg['auth_user']=='terminal-admin'
        assert check_password(password,cfg['auth_password_hash'])
        assert password.encode() not in transcript
        assert not (tmp_path/'private/initial-credentials.txt').exists()
    finally:
        if process.poll() is None:
            process.kill();process.wait(timeout=5)
        os.close(master)
