import os
import json
import uuid
import time
import subprocess
import urllib.request
import ipaddress
import urllib.parse
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

    def add_proxy(self, name, protocol, host, port, username="", password=""):
        protocol = protocol.lower().strip()
        host = host.strip().strip('[]')
        protocol={'ss5':'socks5','socks':'socks5'}.get(protocol,protocol)
        if protocol not in ('http', 'https', 'socks4', 'socks4a', 'socks5', 'socks5h'):
            raise ValueError('支持 HTTP、HTTPS、SOCKS4 和 SOCKS5；其他协议请通过客户端转换为 HTTP/SOCKS 端口')
        if not name.strip() or not host or any(c in host for c in '/@?# \\') or not 1 <= int(port) <= 65535:
            raise ValueError('请填写有效的代理名称、主机和端口')
        p_id = str(uuid.uuid4())[:8]
        item = {
            "id": p_id,
            "name": name.strip(),
            "protocol": protocol.lower().strip(), # http or socks5
            "host": host.strip(),
            "port": int(port),
            "username": username.strip(),
            "password": password,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.proxies.append(item)
        self._save()
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
            parsed=urllib.parse.urlsplit(row if '://' in row else 'http://'+row)
            if parsed.scheme not in ('http','https','socks4','socks4a','socks5','socks5h','ss5'):
                raise ValueError('第 %d 行协议暂不支持，可用外部客户端转换为 HTTP/SOCKS 端口'%index)
            if parsed.path not in ('','/') or parsed.query or not parsed.hostname: raise ValueError('第 %d 行节点链接无效'%index)
            port=parsed.port or (443 if parsed.scheme=='https' else 8080 if parsed.scheme=='http' else 1080)
            if not 1<=port<=65535: raise ValueError('第 %d 行端口无效'%index)
            parsed_nodes.append((urllib.parse.unquote(parsed.fragment) or '导入节点-%d'%index,
                parsed.scheme,parsed.hostname,port,urllib.parse.unquote(parsed.username or ''),urllib.parse.unquote(parsed.password or '')))
        original=list(self.proxies)
        try:return [self.add_proxy(*node) for node in parsed_nodes]
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
