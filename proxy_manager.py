import os
import json
import uuid
import time
import subprocess
import urllib.request
import ipaddress
import urllib.parse
import copy
from config import atomic_json

class ProxyManager:
    def __init__(self, data_dir):
        self.file_path = os.path.join(data_dir, "proxies.json")
        self.proxies = self._load()

    def _load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                raise ValueError('代理配置文件损坏，请从备份恢复') from e
        return []

    def _save(self):
        atomic_json(self.file_path, self.proxies)

    def list_proxies(self):
        return self.proxies

    def get_proxy(self, proxy_id):
        for p in self.proxies:
            if p["id"] == proxy_id:
                return p
        return None

    @staticmethod
    def public_proxy(item, reveal_password=False):
        if not item: raise ValueError('代理节点不存在')
        result={k:v for k,v in item.items() if k!='password'}
        result['has_password']=bool(item.get('password'))
        if reveal_password: result['password']=item.get('password','')
        return result

    @staticmethod
    def validate_fields(name, protocol, host, port, username='', password=''):
        if not all(isinstance(value,str) for value in (name,protocol,host,username,password)):
            raise ValueError('节点文本字段必须是字符串')
        protocol=protocol.lower().strip()
        protocol={'ss5':'socks5','socks':'socks5'}.get(protocol,protocol)
        host=host.strip().strip('[]')
        if protocol not in ('http','https','socks4','socks4a','socks5','socks5h'):
            raise ValueError('支持 HTTP、HTTPS、SOCKS4 和 SOCKS5；其他协议请通过客户端转换为 HTTP/SOCKS 端口')
        try:
            if isinstance(port,bool) or isinstance(port,float):raise ValueError()
            port=int(port)
        except (ValueError,TypeError):raise ValueError('端口必须是 1–65535 的整数')
        if not name.strip() or not host or any(c in host for c in '/@?# \\') or any(ord(c)<32 or ord(c)==127 for c in host) or not 1<=port<=65535:
            raise ValueError('请填写有效的代理名称、主机和端口')
        if ':' in host:
            try:ipaddress.IPv6Address(host)
            except ValueError:raise ValueError('IPv6 地址格式无效')
        return dict(name=name.strip(),protocol=protocol,host=host,port=port,username=username.strip(),password=password)

    def add_proxy(self, name, protocol, host, port, username="", password=""):
        fields=self.validate_fields(name,protocol,host,port,username,password)
        p_id=uuid.uuid4().hex
        while self.get_proxy(p_id):p_id=uuid.uuid4().hex
        stamp=time.strftime('%Y-%m-%d %H:%M:%S')
        item=dict(id=p_id,created_at=stamp,updated_at=stamp,**fields)
        self.proxies.append(item)
        try:self._save()
        except Exception:
            self.proxies.remove(item)
            raise
        return item

    @staticmethod
    def parse_link(row, fallback_name='导入节点'):
        if not isinstance(row,str) or not row.strip() or len(row.splitlines())!=1:
            raise ValueError('编辑节点时只能填写一个链接')
        parsed=urllib.parse.urlsplit(row.strip() if '://' in row else 'http://'+row.strip())
        if parsed.scheme not in ('http','https','socks4','socks4a','socks5','socks5h','ss5'):
            raise ValueError('节点链接协议暂不支持，可用外部客户端转换为 HTTP/SOCKS 端口')
        if parsed.path not in ('','/') or parsed.query or not parsed.hostname:
            raise ValueError('节点链接无效')
        port=parsed.port or (443 if parsed.scheme=='https' else 8080 if parsed.scheme=='http' else 1080)
        return ProxyManager.validate_fields(urllib.parse.unquote(parsed.fragment) or fallback_name,
            parsed.scheme,parsed.hostname,port,urllib.parse.unquote(parsed.username or ''),urllib.parse.unquote(parsed.password or ''))

    def prepare_update(self, proxy_id, fields):
        item=self.get_proxy(proxy_id)
        if not item:raise ValueError('代理节点不存在')
        allowed={'name','protocol','host','port','username','password','clear_password','link'}
        if set(fields)-allowed:raise ValueError('节点 ID 和创建时间不可修改')
        candidate=copy.deepcopy(item)
        link=fields.get('link','')
        if link:
            linked=self.parse_link(link,fields.get('name',item['name']))
            self.check_link_consistency(linked,fields,item)
            candidate.update(linked)
        else:
            for key in ('name','protocol','host','port','username'):
                if key in fields:candidate[key]=fields[key]
            if fields.get('clear_password') is True:candidate['password']=''
            elif fields.get('password') not in (None,''):candidate['password']=fields['password']
        candidate.update(self.validate_fields(**{key:candidate.get(key,'') for key in ('name','protocol','host','port','username','password')}))
        return candidate

    @staticmethod
    def check_link_consistency(linked, fields, existing=None):
        manual=copy.deepcopy(linked)
        checked=[]
        for key in ('name','protocol','host','port','username'):
            if key in fields:manual[key]=fields[key];checked.append(key)
        if 'password' in fields or fields.get('clear_password') is True:
            if fields.get('clear_password') is True:manual['password']=''
            elif existing is not None and fields.get('password') in (None,''):
                manual['password']=existing.get('password','')
            else:manual['password']=fields.get('password','')
            checked.append('password')
        normalized=ProxyManager.validate_fields(**manual)
        def comparable(key,value):
            if key=='host':
                try:return ipaddress.ip_address(value).compressed
                except ValueError:return value.lower()
            return value
        labels=dict(name='节点名称',protocol='协议',host='地址',port='端口',username='用户名',password='密码')
        conflicts=[labels[key] for key in checked if comparable(key,normalized[key])!=comparable(key,linked[key])]
        if conflicts:raise ValueError('保存失败：节点链接与下方字段不一致（'+ '、'.join(conflicts)+'）。请改成一致，或清空链接后只用下方字段。')

    def add_from_input(self, fields):
        allowed={'name','protocol','host','port','username','password','link'}
        if set(fields)-allowed:raise ValueError('创建节点不能指定 ID 或创建时间')
        if fields.get('link'):
            linked=self.parse_link(fields['link'],fields.get('name') or '导入节点')
            self.check_link_consistency(linked,fields)
            return self.add_proxy(**linked)
        return self.add_proxy(fields.get('name',''),fields.get('protocol','http'),fields.get('host',''),fields.get('port',0),fields.get('username',''),fields.get('password',''))

    def update_proxy(self, proxy_id, fields):
        candidate=self.prepare_update(proxy_id,fields)
        item=self.get_proxy(proxy_id)
        original=copy.deepcopy(item)
        candidate['updated_at']=time.strftime('%Y-%m-%d %H:%M:%S')
        item.clear();item.update(candidate)
        try:self._save()
        except Exception:
            item.clear();item.update(original)
            raise
        return item

    def delete_proxy(self, proxy_id):
        self.proxies = [p for p in self.proxies if p["id"] != proxy_id]
        self._save()
        return True
    def import_links(self, text):
        rows=[line.strip() for line in text.splitlines() if line.strip()]
        if not rows or len(rows)>100: raise ValueError('请填写 1–100 个节点链接，每行一个')
        parsed_nodes=[]
        for index,row in enumerate(rows,1):
            try:parsed_nodes.append(self.parse_link(row,'导入节点-%d'%index))
            except ValueError as e:raise ValueError('第 %d 行：%s'%(index,e))
        original=list(self.proxies)
        try:return [self.add_proxy(**node) for node in parsed_nodes]
        except Exception:
            self.proxies=original;self._save();raise

    def get_proxy_url(self, p):
        if not p:
            return None
        proto = p.get("protocol", "http")
        auth = ""
        if p.get("username"):
            pwd = p.get("password", "")
            auth = urllib.parse.quote(p['username'], safe='') + ':' + urllib.parse.quote(pwd, safe='') + '@'
        host = p['host']
        if ':' in host: host = '[' + host + ']'
        return f"{proto}://{auth}{host}:{p['port']}"

    def get_proxy_link(self, p):
        """Reconstruct an importable link from the node's current saved fields."""
        if not p:raise ValueError('代理节点不存在')
        return self.get_proxy_url(p)+'#'+urllib.parse.quote(p.get('name',''),safe='')

    def test_proxy(self, p_or_id):
        if isinstance(p_or_id, str):
            p = self.get_proxy(p_or_id)
        else:
            p = p_or_id
        if not p:
            return {"ok": False, "error": "代理未找到"}

        proxy_url = self.get_proxy_url(p)
        start_t = time.time()
        try:
            # Use curl for robust test supporting socks5/http
            cmd = [
                "curl", "-fsS", "-m", "7", "--noproxy", "",
                "--proxy", proxy_url,
                "https://api.ipify.org?format=json"
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8)
            latency = int((time.time() - start_t) * 1000)
            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                return {
                    "ok": True,
                    "ip": data.get("ip", "未知IP"),
                    "latency_ms": latency
                }
            else:
                err = res.stderr.strip() or "连接超时或无响应"
                return {"ok": False, "error": err, "latency_ms": latency}
        except Exception as e:
            return {"ok": False, "error": str(e), "latency_ms": 0}
