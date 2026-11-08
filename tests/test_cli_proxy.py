import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

if os.name != 'posix':
    pytest.skip('Linux CLI entrypoint', allow_module_level=True)

from cli_proxy import MARKER
from config import atomic_json
from profile_manager import ProfileManager
from server import SuiteState
from proxy_manager import ProxyManager


@pytest.fixture
def runtime(tmp_path):
    manager = ProfileManager(tmp_path / 'data', tmp_path / 'data/profiles')
    home = tmp_path / 'home'
    (home / '.config/Antigravity').mkdir(parents=True)
    entry = home / '.local/bin/agy'
    entry.parent.mkdir(parents=True)
    entry.write_bytes(b'\x7fELF CLITokenFilePath cliFileTokenStorage official-placeholder')
    entry.chmod(0o755)
    spec = manager.native_runtime.register_installation(dict(home=str(home), config_dir=str(home / '.config/Antigravity'),
        uid=os.getuid(), binary='/opt/antigravity/antigravity', display=':1', available=True))
    manager.native_runtime.select()
    return manager, spec, home


def test_cli_entrypoint_is_reversible_and_runtime_contains_no_node_secret(runtime):
    manager, spec, home = runtime
    original = (home / '.local/bin/agy').read_bytes()
    assert manager.cli_proxy.publish(spec, 'http://127.0.0.1:32199', required=True, profile_id='fixed-profile')
    assert MARKER in (home / '.local/bin/agy').read_text()
    assert (home / '.local/bin/agy.suite-real').read_bytes() == original
    path = manager.cli_proxy.runtime_path(spec)
    value = json.loads(path.read_text())
    assert value['proxy_url'] == 'http://127.0.0.1:32199' and value['proxy_required']
    assert not any(key in value for key in ('password', 'username', 'upstream'))
    assert path.stat().st_mode & 0o777 == 0o600
    manager.cli_proxy.suspend()
    assert json.loads(path.read_text())['ready'] is False
    assert manager.cli_proxy.restore() == 1
    assert (home / '.local/bin/agy').read_bytes() == original
    assert not (home / '.local/bin/agy.suite-real').exists()


def test_user_modification_of_cli_entrypoint_is_not_overwritten(runtime):
    manager, spec, home = runtime
    manager.cli_proxy.publish(spec)
    entry = home / '.local/bin/agy'
    entry.write_text(entry.read_text() + '# user customization\n')
    with pytest.raises(ValueError, match='其他操作修改'):
        manager.cli_proxy.publish(spec)
    with pytest.raises(ValueError, match='其他修改'):
        manager.cli_proxy.restore()
    assert '# user customization' in entry.read_text()


def spy_launcher(home, ready=True, required=False, proxy=''):
    binary = home / '.local/bin/agy.suite-real'
    binary.parent.mkdir(parents=True, exist_ok=True)
    python = getattr(sys, '_base_executable', sys.executable)
    binary.write_text('#!' + python + '\nimport os,json\nprint(json.dumps({"proxy":os.environ.get("HTTPS_PROXY"),"http":os.environ.get("HTTP_PROXY"),"no_proxy":os.environ.get("NO_PROXY"),"home":os.environ.get("HOME")}))\n')
    binary.chmod(0o755)
    path = home / 'runtime.json'
    atomic_json(path, dict(uid=os.getuid(), gid=os.getgid(), home=str(home), binary=str(binary), ready=ready,
                           proxy_required=required, proxy_url=proxy))
    return path


def run_launcher(path, *args):
    driver = Path(__file__).parents[1] / 'cli_launcher.py'
    return subprocess.run([sys.executable, '-B', str(driver), '--runtime', str(path), '--', *args],
                          text=True, capture_output=True, timeout=10,
                          env=dict(os.environ, HTTPS_PROXY='http://wrong.example:9', HTTP_PROXY='http://wrong.example:9', NO_PROXY='*'))


def test_direct_selected_configuration_clears_old_ssh_proxy_environment(tmp_path):
    path = spy_launcher(tmp_path)
    result = run_launcher(path, 'models')
    assert result.returncode == 0
    value = json.loads(result.stdout)
    assert value['proxy'] is None and value['http'] is None
    assert value['home'] == str(tmp_path) and value['no_proxy'] == 'localhost,127.0.0.1,::1'


@pytest.mark.parametrize('status', [200, 502])
def test_bound_node_requires_shared_proxy_and_never_falls_back_to_direct(tmp_path, status):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0));listener.listen()
    proxy = 'http://127.0.0.1:' + str(listener.getsockname()[1])
    seen = []
    def accept():
        connection, _ = listener.accept()
        with connection:
            seen.append(connection.recv(4096).split(b'\r\n', 1)[0])
            connection.sendall(('HTTP/1.1 '+str(status)+' Test\r\n\r\n').encode())
    thread = threading.Thread(target=accept, daemon=True);thread.start()
    try:
        path = spy_launcher(tmp_path, required=True, proxy=proxy)
        result = run_launcher(path, 'models')
        if status == 200:
            assert result.returncode == 0
            value = json.loads(result.stdout)
            assert value['proxy'] == value['http'] == proxy
        else:
            assert result.returncode == 1 and result.stdout == '' and '未改走直连' in result.stderr
        assert seen == [b'CONNECT oauth2.googleapis.com:443 HTTP/1.1']
    finally:
        listener.close();thread.join(timeout=2)


def test_unready_runtime_blocks_models_but_static_help_is_available(tmp_path):
    path = spy_launcher(tmp_path, ready=False, required=True)
    result = run_launcher(path, 'models')
    assert result.returncode == 1 and result.stdout == '' and '尚未就绪' in result.stderr
    assert run_launcher(path, '--help').returncode == 0


def test_cli_and_desktop_reuse_one_proxy_bridge_and_remember_its_port(runtime):
    import asyncio
    manager, spec, _ = runtime
    state = SuiteState.__new__(SuiteState)
    state.cfg = {};state.profiles = manager;state.outbound_bridge = None;state.engine_proxy_url = None
    state.proxies = ProxyManager(manager.data_dir)
    state.proxies.proxies = [dict(id='node-id', protocol='http', host='127.0.0.1', port=31399, username='', password='')]
    state.session_ops = SimpleNamespace(runtime=lambda: spec)
    state.persist = lambda: None
    profile = dict(id='fixed-profile', proxy_id='node-id')
    async def check():
        first = await state.prepare_engine_proxy(profile)
        original_bridge = state.outbound_bridge
        try:
            second = await state.prepare_engine_proxy(profile)
            assert first == second and state.outbound_bridge is original_bridge
            metadata = json.loads(manager.cli_proxy.runtime_path(spec).read_text())
            assert metadata['proxy_url'] == first and metadata['profile_id'] == 'fixed-profile'
            assert state.cfg['shared_proxy_port'] == original_bridge.server.sockets[0].getsockname()[1]
        finally:
            await state.outbound_bridge.close()
    asyncio.run(check())
