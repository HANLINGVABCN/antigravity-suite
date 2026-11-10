"""Offline adapters for CPA and Sub2API Antigravity account exports."""
import copy
import datetime
import json
import math
from pathlib import Path

from config import atomic_json
from profile_manager import remove_path

MAX_JSON_BYTES=4*1024*1024
MAX_ACCOUNTS=100
UTC=datetime.timezone.utc


def text(value,field,required=False,limit=16384):
    if value is None:value=''
    if not isinstance(value,str) or len(value)>limit or '\x00' in value:
        raise ValueError(field+'格式无效')
    value=value.strip()
    if required and not value:raise ValueError('缺少'+field)
    return value


def instant(value):
    if value is None or value=='':return None
    try:
        if isinstance(value,bool):raise ValueError()
        if isinstance(value,(int,float)) or (isinstance(value,str) and value.replace('.','',1).isdigit()):
            number=float(value)
            if not math.isfinite(number):raise ValueError()
            if number>100000000000:number/=1000
            return datetime.datetime.fromtimestamp(number,UTC)
        date=datetime.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if date.tzinfo is None:raise ValueError()
        return date.astimezone(UTC)
    except (ValueError,TypeError,OverflowError,OSError):raise ValueError('凭据过期时间格式无效')


def native_token(credentials,email):
    if not isinstance(credentials,dict):raise ValueError('账号凭据必须是 JSON 对象')
    refresh=text(credentials.get('refresh_token'),'refresh_token',required=True)
    access=text(credentials.get('access_token'),'access_token')
    expiry=None
    for name in ('expired','expires_at','expiry'):
        if credentials.get(name) not in (None,''):
            expiry=instant(credentials[name]);break
    raw_seconds=credentials.get('expires_in',3599)
    try:
        if isinstance(raw_seconds,bool):raise ValueError()
        seconds=int(raw_seconds)
        if not 0<=seconds<=365*86400:raise ValueError()
    except (TypeError,ValueError):raise ValueError('expires_in 格式无效')
    if expiry is None and credentials.get('timestamp') not in (None,''):
        expiry=instant(credentials['timestamp'])+datetime.timedelta(seconds=seconds)
    # Do not pretend a missing/expired access token has just been issued.
    if expiry is None or not access:expiry=datetime.datetime(1970,1,1,tzinfo=UTC)
    warning=None
    if not access or expiry<=datetime.datetime.now(UTC):
        warning='访问凭据可能已过期或缺失，启动后需要尝试续期；是否可用以检测结果为准。'
    value=dict(token=dict(access_token=access,refresh_token=refresh,token_type=text(credentials.get('token_type','Bearer'),'token_type',limit=64),
        expiry=expiry.isoformat().replace('+00:00','Z'),expires_in=seconds),auth_method='consumer',id_token_email=email,writer_id='antigravity')
    return value,warning


def read_external(path,source):
    path=Path(path)
    if source not in ('cpa','sub2api'):raise ValueError('不支持的导入来源')
    if not 0<path.stat().st_size<=MAX_JSON_BYTES:raise ValueError('第三方 JSON 文件不能超过 4MB 或为空')
    try:
        value=json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError,UnicodeError):raise ValueError('JSON 格式无效，请选择该工具导出的账号 JSON 文件')
    warnings=[];skipped=0
    if source=='cpa':
        rows=value if isinstance(value,list) else [value]
    else:
        if not isinstance(value,dict) or not isinstance(value.get('accounts'),list):
            raise ValueError('Sub2API 文件需要包含 accounts 数组，请导出账号数据文件')
        if value.get('type') not in (None,'','sub2api-data','sub2api-bundle'):
            raise ValueError('不支持的 Sub2API 数据类型')
        version=value.get('version',1)
        if isinstance(version,bool) or version not in (0,1):raise ValueError('不支持此 Sub2API 导出版本')
        # The user imports accounts only: never parse, validate or bind proxy data.
        rows=value['accounts']
    if not isinstance(rows,list) or not 1<=len(rows)<=MAX_ACCOUNTS:raise ValueError('每次支持导入 1–100 个账号')
    accounts=[]
    for index,row in enumerate(rows,1):
        if not isinstance(row,dict):raise ValueError('第 %d 个账号格式无效'%index)
        if source=='cpa':
            supported=row.get('type')=='antigravity'
            disabled=row.get('disabled') is True
        else:
            supported=row.get('platform')=='antigravity' and row.get('type')=='oauth'
            disabled=row.get('disabled') is True or row.get('status') in ('disabled','inactive')
        if not supported or disabled:
            skipped+=1;continue
        credentials=row if source=='cpa' else row.get('credentials')
        email_value=row.get('email') or (credentials.get('email') if isinstance(credentials,dict) else '')
        if not email_value and isinstance(row.get('extra'),dict):email_value=row['extra'].get('email')
        email=text(email_value,'邮箱',limit=320)
        name=text(row.get('name') or email or ('CPA' if source=='cpa' else 'Sub2API')+'账号-%d'%index,'账号名称',required=True,limit=256)
        try:token,warning=native_token(credentials,email)
        except ValueError as error:raise ValueError('第 %d 个 Antigravity 账号：%s'%(index,error))
        if warning:warnings.append('第 %d 个账号：%s'%(index,warning))
        project_id=text(credentials.get('project_id'),'project_id',limit=1024)
        if not project_id and source=='cpa' and isinstance(row.get('antigravity_capability_hints'),dict):
            project_id=text(row['antigravity_capability_hints'].get('project_id'),'project_id',limit=1024)
        if project_id:token['project_id']=project_id
        accounts.append(dict(name=name,email=email,token=token))
    if not accounts:raise ValueError('没有可导入的 Antigravity OAuth 账号；其他服务账号和已停用账号不支持导入')
    if skipped:warnings.append('已跳过 %d 个其他服务、非 OAuth 或已停用账号。'%skipped)
    warnings.append('只导入账号凭据，代理由你自己配置；第三方模型列表、上游地址、套餐及调度设置不会覆盖本工具，也不会导入聊天记录。')
    return dict(source=source,accounts=accounts,warnings=warnings,skipped_count=skipped)


def preview_external(path,source):
    plan=read_external(path,source)
    return dict(source=source,name=plan['accounts'][0]['name'] if len(plan['accounts'])==1 else '共 %d 个 Antigravity 账号'%len(plan['accounts']),
        account_count=len(plan['accounts']),accounts=[dict(name=row['name'],email=row['email']) for row in plan['accounts']],
        has_proxy=False,proxy=None,proxy_count=0,proxy_message='代理由你自己配置',warnings=plan['warnings'],skipped_count=plan['skipped_count'])


def import_external(path,source,profiles):
    plan=read_external(path,source)
    old_profiles=copy.deepcopy(profiles.profiles)
    created=[]
    try:
        for row in plan['accounts']:
            profile=profiles.create_profile(row['name'],'',row['email']);created.append(profile)
            atomic_json(profiles.profile_dir(profile['id'])/'store/jetski-standalone-oauth-token',row['token'])
        return dict(profiles=created,profile=created[0],imported_count=len(created),proxy_imported=False,warnings=plan['warnings'])
    except Exception:
        for profile in created:remove_path(profiles.profiles_dir/profile['id'])
        profiles.profiles=old_profiles;profiles._save()
        raise


def export_external(profiles,p_id,format):
    if format not in ('cpa','sub2api'):raise ValueError('不支持的导出格式')
    profile=profiles.get_profile(p_id)
    if not profile:raise ValueError('档案不存在')
    folder=profiles.profile_dir(p_id)
    home=profiles.home_for(p_id)
    file=folder/'store/jetski-standalone-oauth-token'
    working=home/'.gemini/jetski-standalone-oauth-token'
    if profiles.native_runtime.spec_for(p_id) or profiles.bound_profile==p_id:
        token=profiles.installed_token(working)
        if token and profiles.account_matches(p_id,token):file=working
    if not file.is_file() or file.stat().st_size>1024*1024:raise ValueError('这个配置没有可导出的 Google 登录凭据，请先完成授权')
    try:value=json.loads(file.read_text(encoding='utf-8'))
    except (ValueError,UnicodeError):raise ValueError('登录凭据文件格式无效')
    if not isinstance(value,dict) or not isinstance(value.get('token'),dict):raise ValueError('登录凭据文件格式无效')
    if value.get('auth_method','consumer')!='consumer':raise ValueError('此授权类型暂不支持 CPA / Sub2API 导出')
    token=value['token']
    email=text(value.get('id_token_email') or profile.get('email'),'邮箱',limit=320)
    if email=='未登录':email=''
    normalized,_=native_token(token,email)
    expiry=instant(normalized['token']['expiry'])
    now=datetime.datetime.now(UTC)
    seconds=normalized['token']['expires_in']
    credentials=dict(access_token=normalized['token']['access_token'],refresh_token=normalized['token']['refresh_token'],
        token_type=normalized['token']['token_type'],expires_in=seconds,email=email)
    project_id=text(value.get('project_id') or token.get('project_id') or profile.get('project_id'),'project_id',limit=1024)
    if project_id:credentials['project_id']=project_id
    if format=='cpa':
        return dict(credentials,type='antigravity',name=profile['name'],disabled=False,
            expired=expiry.isoformat().replace('+00:00','Z'),timestamp=int((expiry.timestamp()-seconds)*1000))
    credentials['expires_at']=str(int(expiry.timestamp()))
    return dict(type='sub2api-data',version=1,exported_at=now.isoformat().replace('+00:00','Z'),proxies=[],
        accounts=[dict(name=profile['name'],platform='antigravity',type='oauth',credentials=credentials,extra={'email':email},
            concurrency=1,priority=1,rate_multiplier=1,auto_pause_on_expired=False)])
