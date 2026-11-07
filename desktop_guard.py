"""Install a reversible Electron bootstrap hook into a compatible, stopped native client."""
import copy
import hashlib
import json
import os
import shutil
import struct
import tempfile
from pathlib import Path

from config import SUITE_DIR,atomic_json

MARKER="/* antigravity-suite-native-guard-v1 */"
PRELOAD_END="/* antigravity-suite-native-preload-end */"
WINDOW_HOOK="require('./suite-native-main.js').registerWindow(win, url);"


def read_archive(path):
    with Path(path).open('rb') as file:
        first=file.read(16)
        if len(first)!=16 or struct.unpack('<I',first[:4])[0]!=4:raise ValueError('客户端资源格式不兼容')
        header_size=struct.unpack('<I',first[4:8])[0];json_size=struct.unpack('<I',first[12:16])[0]
        if not 0<json_size<16*1024*1024:raise ValueError('客户端资源头无效')
        header=json.loads(file.read(json_size));start=8+header_size
        data={}
        def collect(files,prefix=''):
            for name,item in files.items():
                path=prefix+name
                if 'files' in item:collect(item['files'],path+'/')
                elif not item.get('unpacked') and 'offset' in item:
                    file.seek(start+int(item['offset']));content=file.read(item['size'])
                    if len(content)!=item['size']:raise ValueError('客户端资源不完整')
                    data[path]=content
        collect(header['files'])
    return header,data


def archive_item(header,name):
    item=header
    for part in name.split('/'):item=item['files'][part]
    return item


def encode_archive(header,data):
    header=copy.deepcopy(header);offset=0;body=[]
    for name,content in data.items():
        item=header
        parts=name.split('/')
        for part in parts[:-1]:item=item.setdefault('files',{}).setdefault(part,{'files':{}})
        leaf=item.setdefault('files',{}).setdefault(parts[-1],{})
        leaf.update(size=len(content),offset=str(offset));leaf.pop('unpacked',None)
        block=leaf.get('integrity',{}).get('blockSize',4*1024*1024)
        leaf['integrity']=dict(algorithm='SHA256',hash=hashlib.sha256(content).hexdigest(),blockSize=block,
            blocks=[hashlib.sha256(content[i:i+block]).hexdigest() for i in range(0,len(content),block)])
        offset+=len(content);body.append(content)
    text=json.dumps(header,separators=(',',':'),ensure_ascii=False).encode()
    payload=struct.pack('<I',len(text))+text
    payload+=b'\0'*((-len(payload))%4)
    packed=struct.pack('<I',len(payload))+payload
    return struct.pack('<II',4,len(packed))+packed+b''.join(body)


def payloads():
    folder=SUITE_DIR/'static'
    main=(folder/'native-main.js').read_text(encoding='utf-8')
    main=main.replace('__AGY_BOOTSTRAP__',json.dumps((folder/'logout-guard.js').read_text(encoding='utf-8')))
    main=main.replace('__AGY_DOCK__',json.dumps((folder/'floating.js').read_text(encoding='utf-8')))
    return {'dist/suite-native-main.js':main.encode(),
        'dist/suite-client-transform.js':(folder/'client-transform.js').read_bytes()}


def inline_preload(preload):
    source=preload.decode('utf-8')
    if MARKER in source:
        start=source.index(MARKER)
        if PRELOAD_END in source:
            end=source.index(PRELOAD_END,start)+len(PRELOAD_END)
        else:
            old=MARKER+"\nrequire('./suite-native-preload.js');\n"
            if old not in source:raise ValueError('已有预加载保护格式不兼容')
            end=start+len(old)
        source=(source[:start].rstrip()+source[end:]).lstrip('\r\n')
    addon=(SUITE_DIR/'static/native-preload.js').read_text(encoding='utf-8')
    # Sandboxed Electron preloads can require electron, but cannot require local modules.
    region=MARKER+'\n(()=>{\n'+addon+'\n})();\n'+PRELOAD_END+'\n'
    return (source.rstrip()+'\n'+region).encode()


def install(binary,data_dir,engine_running=False):
    archive=Path(binary).resolve().parent/'resources/app.asar'
    if not archive.is_file():return dict(installed=False,reason='未发现可兼容的原生客户端资源')
    header,files=read_archive(archive);required=payloads()
    main=files.get('dist/main.js',b'');preload=files.get('dist/preload.js',b'');utils=files.get('dist/utils.js',b'')
    if not main or not preload or b'require("electron")' not in main or b'contextBridge.exposeInMainWorld' not in preload:
        return dict(installed=False,reason='原生客户端入口不兼容，暂不能覆盖桌面退出')
    if utils.count(b'void win.loadURL(url);')!=1:return dict(installed=False,reason='原生窗口入口不兼容，暂不能覆盖桌面退出')
    new_preload=inline_preload(preload)
    if MARKER.encode() in main and preload==new_preload and WINDOW_HOOK.encode() in utils and all(files.get(name)==content for name,content in required.items()):
        return dict(installed=True,archive=str(archive))
    from discovery import engine_processes
    if engine_running or any(Path(p['executable']).resolve()==Path(binary).resolve() for p in engine_processes()):
        raise ValueError('需要停止使用此本体的所有反重力实例后更新原生退出保护')
    backup=Path(data_dir)/'desktop-guard-backups';backup.mkdir(parents=True,exist_ok=True,mode=0o700)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest();saved=backup/(digest+'.asar')
    previous=backup/'manifest.json'
    original_digest=digest
    if MARKER.encode() in main:
        if not previous.is_file():raise ValueError('原生退出保护缺少原始备份记录，请先恢复客户端')
        value=json.loads(previous.read_text())
        if value['archive']!=str(archive) or value['patched_sha256']!=digest:raise ValueError('客户端有其他修改，拒绝覆盖')
        saved=Path(value['backup']);original_digest=value['original_sha256']
    if not saved.exists():shutil.copy2(archive,saved);os.chmod(saved,0o600)
    prefix=(MARKER+"\nrequire('./suite-native-main.js').install();\n").encode()
    if MARKER.encode() not in main:
        offset=len(b'"use strict";') if main.startswith(b'"use strict";') else 0
        files['dist/main.js']=main[:offset]+b'\n'+prefix+main[offset:]
    files['dist/preload.js']=new_preload
    if WINDOW_HOOK.encode() not in utils:files['dist/utils.js']=utils.replace(b'void win.loadURL(url);',WINDOW_HOOK.encode()+b'\n    void win.loadURL(url);',1)
    files.update(required)
    blob=encode_archive(header,files)
    fd,tmp=tempfile.mkstemp(dir=archive.parent,prefix='.suite-native-')
    try:
        with os.fdopen(fd,'wb') as output:output.write(blob);output.flush();os.fsync(output.fileno())
        os.chmod(tmp,archive.stat().st_mode & 0o777);os.replace(tmp,archive)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    atomic_json(backup/'manifest.json',dict(archive=str(archive),backup=str(saved),original_sha256=original_digest,patched_sha256=hashlib.sha256(blob).hexdigest()))
    return dict(installed=True,archive=str(archive),backup=str(saved))


def restore(data_dir):
    manifest=Path(data_dir)/'desktop-guard-backups/manifest.json'
    if not manifest.is_file():return False
    value=json.loads(manifest.read_text());archive=Path(value['archive'])
    from discovery import engine_processes
    if any(Path(p['executable']).resolve().parent/'resources/app.asar'==archive for p in engine_processes()):
        raise ValueError('请先停止使用此本体的反重力实例，再恢复原生退出功能')
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=value['patched_sha256']:raise ValueError('客户端已发生其他修改，拒绝覆盖，请保留原生保护备份')
    saved=Path(value['backup'])
    if hashlib.sha256(saved.read_bytes()).hexdigest()!=value['original_sha256']:raise ValueError('原生保护备份校验失败')
    fd,tmp=tempfile.mkstemp(dir=archive.parent,prefix='.suite-restore-')
    try:
        with os.fdopen(fd,'wb') as output:output.write(saved.read_bytes());output.flush();os.fsync(output.fileno())
        os.chmod(tmp,archive.stat().st_mode & 0o777);os.replace(tmp,archive)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    manifest.unlink()
    return True


if __name__=='__main__':
    import argparse
    from config import DATA_DIR
    parser=argparse.ArgumentParser(description='恢复本体的原生退出功能，保留账号和聊天记录')
    parser.add_argument('--restore',action='store_true',required=True)
    parser.add_argument('--data-dir',type=Path,default=DATA_DIR)
    args=parser.parse_args()
    print('原生退出保护已恢复' if restore(args.data_dir) else '没有需要恢复的原生退出保护')
