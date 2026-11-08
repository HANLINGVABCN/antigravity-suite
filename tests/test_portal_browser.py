"""Optional Chromium regressions using a disposable local service and dummy accounts."""
import asyncio
import json
import os
import queue
import secrets
import socket
import threading
from pathlib import Path

import pytest
from aiohttp import web

pw = pytest.importorskip('playwright.sync_api', reason='Install requirements-browser.txt for real browser checks')

from config import atomic_json
from server import create_app, session_token
from test_suite import PASSWORD, FakeSupervisor, config, start


@pytest.fixture(scope='module')
def chromium():
    with pw.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=os.environ.get('ANTIGRAVITY_TEST_BROWSER') or None)
        yield browser
        browser.close()


@pytest.fixture
def portal(tmp_path, chromium):
    cfg = config(tmp_path)
    app = create_app(cfg, supervisor=FakeSupervisor())
    @web.middleware
    async def delayed_node_details(request, handler):
        if request.path=='/__suite/api/proxies/detail':
            await asyncio.sleep(.15)
        if request.path=='/__suite/api/profiles/import/preview':
            gate=getattr(state,'test_import_gate',None)
            while gate is not None and not gate.is_set():await asyncio.sleep(.02)
        return await handler(request)
    app.middlewares.insert(0, delayed_node_details)
    state = app['state']
    state.updater.controller=dict(kind='test')
    async def fake_update_check():
        state.updater.release=dict(tag_name='v99.0.0',html_url='https://github.com/HANLINGVABCN/antigravity-suite/releases/tag/v99.0.0',body='测试更新说明')
        state.updater.checked_at=1
        return state.updater.status()
    state.updater.check=fake_update_check
    node = state.proxies.add_proxy('节点旧名称 #1', 'socks5h', '2001:db8::1', 1080, 'a@b', 'dummy-password:@ /')
    profile = state.profiles.create_profile('测试账号 · ' + '较长名称和备注' * 5, node['id'])
    state.profiles.update_profile(profile['id'], email='dummy-account@example.invalid')
    settings = state.profiles.profile_dir(profile['id'])/'store/Antigravity_config/User/settings.json'
    atomic_json(settings, dict(theme='test'))
    archive = tmp_path/'test-profile.tar.gz'
    state.profiles.export_profile(profile['id'], archive, node)
    ready = queue.Queue()
    stop = threading.Event()

    async def serve():
        # Browser-reserved ports can be returned by a customized ephemeral range.
        sock=None
        for _ in range(100):
            candidate=socket.socket()
            try:
                candidate.bind(('127.0.0.1',20000+secrets.randbelow(30000)))
                sock=candidate;break
            except OSError:candidate.close()
        if sock is None:raise RuntimeError('Could not allocate a browser-safe local test port')
        runner=web.AppRunner(app);await runner.setup()
        site=web.SockSite(runner,sock);await site.start()
        port=sock.getsockname()[1]
        ready.put(port)
        try:
            while not stop.is_set():
                await asyncio.sleep(.02)
        finally:
            await runner.cleanup()

    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()
    url = 'http://127.0.0.1:%d' % ready.get(timeout=10)
    context = chromium.new_context()
    context.add_cookies([dict(name='ag_session', value=session_token(cfg), url=url)])
    page = context.new_page()
    page.set_default_timeout(8000)
    page.goto(url+'/?portal=1')
    pw.expect(page.locator('#profile-container [data-card-id]')).to_have_count(1)
    pw.expect(page.locator('#btn-core-manage')).to_have_text('卸载反重力')
    yield dict(page=page, context=context, url=url, node=node, profile=profile, settings=settings, archive=archive,state=state)
    context.close()
    stop.set()
    thread.join(timeout=10)
    assert not thread.is_alive()


def assert_dialog_reachable(page, mask_id):
    dialog = page.locator('#'+mask_id)
    pw.expect(dialog).to_be_visible()
    assert page.locator('.modal-mask:visible').count()==1
    assert page.locator('#app-page').evaluate('element => element.inert')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    for button in dialog.locator('.dialog-heading button,.dialog-actions button').all():
        if not button.is_visible():
            continue
        box=button.bounding_box()
        viewport=page.viewport_size
        assert box['x']>=0 and box['y']>=0
        assert box['x']+box['width']<=viewport['width']+1
        assert box['y']+box['height']<=viewport['height']+1
        button.click(trial=True)
    body=dialog.locator('.dialog-scroll')
    body.evaluate('element => { element.scrollTop = element.scrollHeight; }')
    for button in dialog.locator('.dialog-actions button').all():
        if button.is_visible():
            button.click(trial=True)
    return dialog


@pytest.mark.parametrize('width,height', [(1280,720),(1024,600),(800,450),(390,844),(390,600),(390,400)])
def test_responsive_layout_and_each_dialog_keep_actions_clickable(portal,width,height):
    page=portal['page']
    page.set_viewport_size(dict(width=width,height=height))
    assert page.locator('.modal-mask:visible').count()==0
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    steps=[
        ('#btn-core-manage','modal-core-install'),
        ('button:has-text("服务设置")','modal-service-settings'),
        ('#proxy-container button:has-text("详情与编辑")','modal-proxy'),
        ('button:has-text("+ 空配置")','modal-create-profile'),
        ('button:has-text("+ 登录 Google 账号")','modal-oauth'),
        ('#profile-container button:has-text("详情与编辑")','modal-edit-profile'),
        ('#profile-container button:has-text("导出")','modal-export-profile'),
    ]
    for selector, mask_id in steps:
        page.locator(selector).click()
        dialog=assert_dialog_reachable(page,mask_id)
        if mask_id=='modal-proxy' and os.environ.get('ANTIGRAVITY_TEST_SCREENSHOTS'):
            output=Path(os.environ['ANTIGRAVITY_TEST_SCREENSHOTS'])
            output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/('proxy-dialog-%dx%d.png'%(width,height))))
        dialog.locator('.dialog-close').click()
        pw.expect(dialog).to_be_hidden()
        assert not page.locator('#app-page').evaluate('element => element.inert')
    page.locator('button:has-text("📥 导入")').click()
    sources=assert_dialog_reachable(page,'modal-import-sources')
    sources.get_by_role('button',name='从 CPA 导入',exact=True).click()
    external=assert_dialog_reachable(page,'modal-external-import')
    external.locator('.dialog-close').click()
    page.locator('button:has-text("📥 导入")').click()
    with page.expect_file_chooser() as chooser:
        page.locator('#modal-import-sources').get_by_role('button',name='从本工具导入',exact=True).click()
    chooser.value.set_files(portal['archive'])
    imported=assert_dialog_reachable(page,'modal-import-profile')
    imported.locator('.dialog-close').click()
    pw.expect(imported).to_be_hidden()


def test_prefilled_node_rename_survives_polling_and_real_conflicts_stay_unsaved(portal):
    page=portal['page']
    node=portal['node'];profile=portal['profile']
    page.locator('#proxy-container button:has-text("详情与编辑")').click()
    pw.expect(page.locator('#modal-proxy')).to_be_visible()
    pw.expect(page.locator('#proxy-id')).to_have_value(node['id'])
    pw.expect(page.locator('#proxy-links')).not_to_have_value('')
    link=page.locator('#proxy-links').input_value()
    assert 'dummy-password' in link and '#%E8%8A%82' in link
    with page.expect_response(lambda response:response.url.endswith('/__suite/api/status'),timeout=6500):
        page.locator('#proxy-in-name').fill('新名称 #2')
    assert page.locator('#proxy-in-name').input_value()=='新名称 #2'
    assert page.locator('#proxy-links').input_value().split('#',1)[0]==link.split('#',1)[0]
    page.locator('#proxy-save-button').click()
    pw.expect(page.locator('#modal-proxy')).to_be_hidden()
    saved=page.request.get(portal['url']+'/__suite/api/proxies/detail?id='+node['id']).json()
    assert saved['proxy']['id']==node['id'] and saved['proxy']['name']=='新名称 #2'
    assert saved['bound_profiles'][0]['id']==profile['id'] and node['password']=='dummy-password:@ /'
    page.locator('#proxy-container button:has-text("详情与编辑")').click()
    page.locator('#proxy-in-host').fill('different.example.invalid')
    page.locator('#proxy-save-button').click()
    error=page.locator('#modal-proxy .dialog-error')
    pw.expect(error).to_contain_text('不一致')
    pw.expect(error).to_contain_text('地址')
    pw.expect(page.locator('#modal-proxy')).to_be_visible()
    assert node['host']=='2001:db8::1' and profile['proxy_id']==node['id']
    page.locator('#modal-proxy .dialog-close').click()
    page.locator('#proxy-container button:has-text("详情与编辑")').click()
    page.locator('#proxy-links').fill(link.split('#',1)[0]+'#ManualRemark')
    page.locator('#proxy-in-name').fill('AnotherName')
    assert page.locator('#proxy-links').input_value().endswith('#ManualRemark')
    page.locator('#proxy-save-button').click()
    pw.expect(page.locator('#modal-proxy .dialog-error')).to_contain_text('节点名称')
    assert node['name']=='新名称 #2'


def test_nested_file_editor_returns_to_unsaved_profile_and_traps_focus(portal):
    page=portal['page']
    page.set_viewport_size(dict(width=390,height=600))
    page.locator('#profile-container button:has-text("详情与编辑")').click()
    pw.expect(page.locator('#edit-prof-name')).to_have_value(portal['profile']['name'])
    page.locator('#edit-prof-name').fill('尚未保存的账号名称')
    edit_button=page.locator('#edit-prof-files button').first
    edit_button.click()
    dialog=assert_dialog_reachable(page,'modal-file-editor')
    pw.expect(page.locator('#modal-edit-profile')).to_be_hidden()
    page.locator('#file-editor-content').fill('{"theme":"changed"}')
    page.locator('#file-editor-save').click()
    pw.expect(dialog).to_be_hidden()
    pw.expect(page.locator('#modal-edit-profile')).to_be_visible()
    pw.expect(page.locator('#edit-prof-name')).to_have_value('尚未保存的账号名称')
    pw.expect(edit_button).to_be_focused()
    assert json.loads(portal['settings'].read_text(encoding='utf-8'))==dict(theme='changed')
    edit_button.click()
    pw.expect(dialog).to_be_visible()
    page.locator('#modal-file-editor .dialog-close').focus()
    pw.expect(page.locator('#modal-file-editor .dialog-close')).to_be_focused()
    page.keyboard.press('Shift+Tab')
    pw.expect(page.locator('#file-editor-save')).to_be_focused()
    page.keyboard.press('Tab')
    pw.expect(page.locator('#modal-file-editor .dialog-close')).to_be_focused()
    page.keyboard.press('Escape')
    pw.expect(page.locator('#modal-edit-profile')).to_be_visible()
    pw.expect(page.locator('#edit-prof-name')).to_have_value('尚未保存的账号名称')
    page.keyboard.press('Escape')
    assert page.locator('.modal-mask:visible').count()==0
    pw.expect(page.locator('#profile-container button:has-text("详情与编辑")')).to_be_focused()


def test_status_updates_keep_keyboard_focus_and_do_not_interrupt_pointer_clicks(portal):
    page=portal['page']
    launch=page.locator('#profile-container [data-launch-id]')
    with page.expect_response(lambda response:response.url.endswith('/__suite/api/status'),timeout=6500):
        launch.focus()
        portal['profile']['email']='first-update@example.invalid'
    pw.expect(page.locator('#profile-container')).to_contain_text('first-update@example.invalid')
    pw.expect(launch).to_be_focused()
    detail=page.locator('#profile-container button:has-text("详情与编辑")')
    detail.hover()
    page.mouse.down()
    with page.expect_response(lambda response:response.url.endswith('/__suite/api/status'),timeout=6500):
        portal['profile']['email']='second-update@example.invalid'
    pw.expect(page.locator('#profile-container')).not_to_contain_text('second-update@example.invalid')
    page.mouse.up()
    pw.expect(page.locator('#modal-edit-profile')).to_be_visible()
    pw.expect(page.locator('#edit-prof-email')).to_have_value('second-update@example.invalid')


def test_long_authenticator_setup_keeps_footer_reachable_without_changing_credentials(portal):
    page=portal['page']
    page.set_viewport_size(dict(width=390,height=400))
    page.get_by_role('button',name='服务设置',exact=True).click()
    pw.expect(page.locator('#modal-service-settings')).to_be_visible()
    page.locator('#settings-current').fill(PASSWORD)
    page.get_by_role('button',name='绑定并开启验证器',exact=True).click()
    pw.expect(page.locator('#mfa-setup')).to_be_visible()
    assert_dialog_reachable(page,'modal-service-settings')
    page.locator('#modal-service-settings .dialog-close').click()
    status=page.request.get(portal['url']+'/__suite/api/system/security/status').json()
    assert status['enabled'] is False


def test_update_dialog_on_short_mobile_window_checks_version_and_shows_failure(portal):
    page=portal['page'];state=portal['state']
    page.set_viewport_size(dict(width=390,height=400))
    page.get_by_role('button',name='更新项目',exact=True).click()
    pw.expect(page.locator('#update-latest-version')).to_have_text('v99.0.0')
    dialog=assert_dialog_reachable(page,'modal-suite-update')
    pw.expect(page.locator('#suite-start-update')).to_be_enabled()
    async def fake_start():
        atomic_json(state.updater.status_file,dict(job_id='browser-test-job',phase='error',message='可能是下载校验失败；原服务未被修改'))
        return state.updater.status()
    state.updater.start=fake_start
    page.locator('#suite-start-update').click()
    pw.expect(page.locator('#suite-update-status')).to_contain_text('可能是下载校验失败')
    pw.expect(page.locator('#suite-update-status')).to_have_class('diag abnormal')
    assert page.locator('#modal-suite-update:visible').count()==1
    dialog.locator('.dialog-close').click()
    pw.expect(page.locator('#btn-suite-update')).to_be_visible()


def test_legacy_runtime_alias_does_not_create_special_or_duplicate_account_card(portal,tmp_path):
    state=portal['state'];manager=state.profiles;page=portal['page']
    home=tmp_path.parent/(tmp_path.name+'-installed-native');config=home/'.config/Antigravity';config.mkdir(parents=True)
    atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='dummy-existing-login'),id_token_email='dummy-account@example.invalid'))
    spec=dict(home=str(home),config_dir=str(config),uid=0,binary='/opt/antigravity/antigravity',display=':1',session_env={},available=True)
    legacy=manager.register_existing(spec)
    manager.native_runtime.refresh_installations([spec])
    manager.native_runtime.binding=dict(profile_id=portal['profile']['id'],native=manager.native_runtime.installations[0])
    manager.import_installed_accounts_once()
    page.reload()
    pw.expect(page.locator('#profile-container [data-card-id]')).to_have_count(1)
    pw.expect(page.locator('#profile-container')).not_to_contain_text('已有共享配置')
    pw.expect(page.locator('#profile-container')).not_to_contain_text('已有共用运行配置')
    pw.expect(page.locator('#profile-container')).not_to_contain_text('配置位置')
    assert page.locator('#profile-container .existing-item').count()==0
    card=page.locator('#profile-container [data-card-id]')
    assert card.get_attribute('data-card-id')==portal['profile']['id']
    pw.expect(card.get_by_role('button',name='🗑️ 删除',exact=True)).to_be_visible()
    assert manager.get_profile(legacy['id'])['internal_runtime']


def test_logout_notice_keeps_a_card_and_new_b_has_a_different_card(portal):
    page=portal['page'];state=portal['state'];a=portal['profile'];original_name=a['name']
    state.active=None;state.loaded=None;state.cfg['native_logged_out']=True
    page.reload()
    pw.expect(page.locator('#google-auth-notice')).to_contain_text('反重力当前未登录')
    pw.expect(page.locator('#profile-container [data-card-id]')).to_have_count(1)
    pw.expect(page.locator('#profile-container')).to_contain_text(original_name)
    b=state.profiles.create_profile('b@example.invalid',email='b@example.invalid')
    state.active=b['id'];state.cfg['native_logged_out']=False
    page.reload()
    pw.expect(page.locator('#profile-container [data-card-id]')).to_have_count(2)
    ids=page.locator('#profile-container [data-card-id]').evaluate_all('elements => elements.map(element => element.dataset.cardId)')
    assert a['id'] in ids and b['id'] in ids and a['id']!=b['id']
    assert state.profiles.get_profile(a['id'])['name']==original_name and state.profiles.get_profile(a['id'])['email']=='dummy-account@example.invalid'


def test_exit_progress_and_login_entry_are_clear_and_reachable(portal):
    page=portal['page'];state=portal['state'];state.active=portal['profile']['id']
    state.exit_state=dict(phase='saving',message='正在保存登录存档')
    page.reload()
    pw.expect(page.locator('#btn-exit-session')).to_be_disabled()
    pw.expect(page.locator('#google-auth-notice')).to_contain_text('正在保存登录存档')
    state.active=None;state.cfg['native_logged_out']=True;state.login_environment_open=True;state.supervisor.active_port=45000
    state.exit_state=dict(phase='ready',message='已退出配置')
    state.session_ops.desktop_status=dict(installed=True,renderer_ready=True)
    page.reload()
    pw.expect(page.locator('#google-auth-notice').get_by_role('button',name='进入登录界面')).to_be_visible()
    page.locator('#google-auth-notice').get_by_role('button',name='进入登录界面').click(trial=True)
    pw.expect(page.locator('#logout-guard-status')).to_contain_text('桌面退出保护已验证')
    pw.expect(page.locator('#profile-container [data-card-id]')).to_have_count(1)


def test_slow_import_keeps_feedback_visible_and_failure_remains_visible(portal):
    page=portal['page'];state=portal['state'];gate=threading.Event();state.test_import_gate=gate
    page.clock.install()
    try:
        page.get_by_role('button',name='📥 导入',exact=True).click()
        with page.expect_file_chooser() as chooser:
            page.get_by_role('button',name='从本工具导入',exact=True).click()
        chooser.value.set_files(portal['archive'])
        notice=page.locator('#transfer-status')
        pw.expect(notice).to_have_attribute('data-phase','working')
        page.clock.fast_forward(9000)
        pw.expect(notice).to_be_visible();pw.expect(notice).to_contain_text('秒')
        gate.set()
        pw.expect(page.locator('#modal-import-profile')).to_be_visible()
        pw.expect(notice).to_have_attribute('data-phase','waiting')
        page.route('**/__suite/api/profiles/import/confirm',lambda route:route.fulfill(status=500,content_type='application/json',body='{"ok":false,"error":"可能是磁盘写入失败"}'))
        page.locator('#import-profile-button').click()
        pw.expect(notice).to_have_attribute('data-phase','error')
        page.clock.fast_forward(30000)
        pw.expect(notice).to_be_visible();pw.expect(notice).to_contain_text('可能是磁盘写入失败')
        pw.expect(page.locator('#profile-container [data-card-id]')).to_have_count(1)
    finally:gate.set()


def test_core_button_stops_and_starts_the_same_profile_with_three_states(portal):
    page=portal['page'];state=portal['state'];identifier=portal['profile']['id']
    state.active=identifier;state.loaded=identifier;state.supervisor.active_port=45000
    from test_suite import FakeProcess
    state.supervisor.process=FakeProcess()
    page.reload();button=page.locator('#btn-engine-toggle')
    pw.expect(button).to_have_text('停止反重力')
    def stop_route(route):
        state.supervisor.process=None;state.supervisor.active_port=None
        route.fulfill(status=200,content_type='application/json',body='{"ok":true}')
    page.route('**/__suite/api/system/stop',stop_route)
    page.once('dialog',lambda dialog:dialog.accept())
    button.click();pw.expect(button).to_have_text('启动反重力')
    def start_route(route):
        assert route.request.post_data_json['profile_id']==identifier
        state.starting_profile_id=identifier
        route.fulfill(status=202,content_type='application/json',body='{"ok":true}')
    page.route('**/__suite/api/profiles/switch',start_route)
    button.click();pw.expect(button).to_have_text('启动中…');pw.expect(button).to_be_disabled()
    state.starting_profile_id=None;state.supervisor.process=FakeProcess();state.supervisor.active_port=45000
    page.evaluate('fetchStatus()');pw.expect(button).to_have_text('停止反重力')


def test_export_two_independent_choices_are_visible_and_explain_contents(portal):
    page=portal['page'];page.locator('#profile-container button:has-text("导出")').click()
    history=page.locator('#export-include-history');proxy=page.locator('#export-include-proxy')
    pw.expect(history).to_be_visible();pw.expect(proxy).to_be_visible()
    pw.expect(history).not_to_be_checked();pw.expect(proxy).not_to_be_checked()
    pw.expect(page.locator('#export-format-description')).to_contain_text('不含聊天记录')
    history.check();pw.expect(page.locator('#export-format-description')).to_contain_text('本地全部聊天记录')
    pw.expect(proxy).not_to_be_checked();proxy.check()
    pw.expect(page.locator('#export-format-description')).to_contain_text('对应代理')
    history.uncheck();pw.expect(proxy).to_be_checked()
    page.locator('#export-format-cpa').click();pw.expect(history).to_be_hidden()
    pw.expect(page.locator('#export-format-description')).to_contain_text('不含代理和聊天记录')


def test_management_restart_is_explicit_and_keeps_progress_until_response(portal):
    page=portal['page']
    page.get_by_role('button',name='服务设置',exact=True).click()
    pw.expect(page.locator('label[for="settings-port"]')).to_contain_text('重启后台服务生效')
    button=page.locator('#btn-service-restart');pw.expect(button).to_be_enabled()
    page.route('**/__suite/api/system/service-restart',lambda route:route.fulfill(status=202,content_type='application/json',body='{"ok":true,"supported":true,"running_port":18888,"saved_port":18888,"port_pending":false,"job":{"job_id":"browser-restart","phase":"queued"}}'))
    page.route('**/__suite/api/system/service-restart/status',lambda route:route.fulfill(status=200,content_type='application/json',body='{"ok":true,"supported":true,"running_port":18888,"saved_port":18888,"port_pending":false,"job":{"job_id":"browser-restart","phase":"starting","message":"正在启动后台服务"}}'))
    button.click();pw.expect(button).to_have_text('后台重启中…');pw.expect(button).to_be_disabled()
    pw.expect(page.locator('#service-restart-status')).to_contain_text('正在启动后台服务')


def test_logged_out_login_entry_survives_refresh_and_starts_before_entering(portal):
    page=portal['page'];state=portal['state'];state.active=None;state.cfg['native_logged_out']=True
    state.sessions.exited();page.reload()
    button=page.locator('#btn-open-login');pw.expect(button).to_be_visible();pw.expect(button).to_have_text('打开登录界面')
    identity=button.evaluate('element=>{element.dataset.identity="same-button";return element.dataset.identity}')
    page.evaluate('fetchStatus()');assert button.get_attribute('data-identity')==identity
    def start_route(route):
        state.opening_login=True
        route.fulfill(status=202,content_type='application/json',body='{"ok":true}')
    page.route('**/__suite/api/system/start',start_route)
    button.click();pw.expect(button).to_have_text('正在打开登录界面…');pw.expect(button).to_be_disabled()
    page.reload();pw.expect(button).to_be_visible();pw.expect(button).to_have_text('正在打开登录界面…')
    state.opening_login=False;state.login_environment_open=True;state.supervisor.active_port=45000
    page.evaluate('fetchStatus()');pw.expect(button).to_have_text('进入登录界面')
    pw.expect(button).to_be_enabled();assert page.url.endswith('/?portal=1')
