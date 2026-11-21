from pathlib import Path
import io
import json
import os
import subprocess
import sys
import zipfile

import pytest

from audiobook import backend, core, hardware
from audiobook.jobs import rename_new
from audiobook.runtime_catalog import extract_hoppou


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'engine/C:redirect', 'engine/NUL.txt',
                                 'engine/link\\evil', 'engine/filename.'])
def test_windows_archive_rejects_unsafe_paths_before_extracting(tmp_path, name):
    archive = tmp_path / 'runtime.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('engine/breeze-server.exe', b'test')
        output.writestr(name, b'unsafe')
    target = tmp_path / 'runtime'
    target.mkdir()
    with pytest.raises(core.Error, match='Unsafe path'):
        extract_hoppou(archive, target, 'windows_x86_64')
    assert not list(target.iterdir())


def test_windows_setup_binds_the_windows_zip_and_dll(tmp_path, monkeypatch):
    catalog = core.loadjson(backend.CATALOG)
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('engine/breeze-server.exe', b'executable fixture')
        archive.writestr('engine/libbreeze.dll', b'Windows library fixture')
    runtime = catalog['runtimes']['hoppou']['windows_x86_64']
    model = catalog['packages']['hoppou-q8']['files'][0]
    terms = next(f for f in catalog['packages']['official']['files'] if f['path']=='LICENSE')
    payloads = {
        runtime['url']: bundle.getvalue(), model['url']: b'weights fixture', terms['url']: b'terms fixture',
    }
    for asset in (runtime,model,terms):
        payload = payloads[asset['url']]
        asset.update(bytes=len(payload),sha256=core.digest(payload))
    path = tmp_path / 'catalog.json'
    core.writejson(path,catalog)
    monkeypatch.setattr(backend,'CATALOG',path)
    monkeypatch.setattr(backend.platform,'system',lambda:'Windows')
    monkeypatch.setattr(backend.platform,'machine',lambda:'AMD64')
    received = []
    def transfer(url, part, offset, size, **kwargs):
        received.append(url)
        part.write_bytes(payloads[url])
    monkeypatch.setattr(backend,'transfer_asset',transfer)
    class Probe:
        def __init__(self,store,identity):
            assert 'libbreeze.dll' in next(iter(identity['library_sha256']))
        def __enter__(self):
            self.health={'actual_device':'Vulkan0'}
            return self
        def __exit__(self,*args):
            pass
        def speech(self,text):
            return b'fixture audio',{'actual_device':'Vulkan0'}
    monkeypatch.setattr(backend,'Engine',Probe)
    result=backend.setup(core.Store(tmp_path/'isolated state'),'vulkan-q8')
    assert set(received)==set(payloads)
    assert result['runtime_platform']=='windows_x86_64'
    assert Path(result['identity']['binary']).name=='breeze-server.exe'


def test_setup_recommendation_separates_driver_fit_from_quality_and_platform_evidence():
    catalog=core.loadjson(backend.CATALOG)
    host={'system':'Linux','arch':'x86_64','cpu_avx2':True,'ram_available_bytes':32*hardware.GIB,
          'gpus':[{'name':'Fixture GPU','available_vram_bytes':12*hardware.GIB}],
          'vulkan':{'status':'available','loader_found':True,'reason':'fixture'}}
    candidate=hardware.advise(host,catalog)['recommended_profile']
    assert candidate['profile']=='vulkan-f16' and candidate['automatic_eligible']
    windows=hardware.advise({**host,'system':'Windows'},catalog)['recommended_profile']
    assert windows['profile']=='vulkan-f16'
    assert not windows['automatic_eligible']
    assert windows['testing_status']=='implemented_experimental'


def test_breeze_requires_proof_of_requested_device():
    engine=backend.Engine.__new__(backend.Engine)
    engine.identity={'settings':{}}
    assert engine.verify_device('graphics driver inventory',allow_missing=True) is None
    with pytest.raises(core.Error,match='requested execution backend'):
        engine.verify_device('backend: CPU\n')
    assert engine.verify_device('backend: Vulkan1, text encoder: CPU\n')=='Vulkan1'


@pytest.mark.skipif(os.name != 'nt', reason='native Windows filesystem and process ownership')
def test_windows_writer_lock_and_no_replace_promotion(tmp_path):
    store=core.Store(tmp_path/'isolated state')
    code='from audiobook.core import Store; from pathlib import Path; import sys; s=Store(Path(sys.argv[1]));\nwith s.lock(): pass'
    with store.lock():
        child=subprocess.run([sys.executable,'-c',code,str(store.root)],capture_output=True)
        assert child.returncode and b'Another managed writer' in child.stderr
    subprocess.run([sys.executable,'-c',code,str(store.root)],check=True)
    source=store.root/'runtime'/'staging'
    dest=store.root/'runtime'/'publication'
    source.mkdir()
    dest.mkdir()
    passage = source/'committed audio.pcm'
    passage.write_bytes(b'checked content')
    core.syncfile(passage)
    with pytest.raises(core.Error,match='already exists'):
        rename_new(source,dest)
    assert source.is_dir() and dest.is_dir()
    store.safe_remove(dest)
    rename_new(source,dest)
    assert dest.is_dir() and not source.exists()
    assert (dest/passage.name).read_bytes()==b'checked content'


def test_protected_mix_keeps_original_tensor_bytes(tmp_path, monkeypatch):
    import struct
    from tools import mix_hoppou
    from tools.inspect_gguf import inspect

    def make(path, entries):
        header = bytearray(struct.pack('<IIQQ',0x46554747,3,len(entries),0))
        storage = bytearray()
        for name, shape, kind, payload in entries:
            encoded = name.encode()
            header.extend(struct.pack('<Q',len(encoded)) + encoded)
            header.extend(struct.pack('<I'+'Q'*len(shape)+'IQ',len(shape),*shape,kind,len(storage)))
            storage.extend(payload)
            storage.extend(bytes(-len(storage)%32))
        header.extend(bytes(-len(header)%32))
        path.write_bytes(header+storage)
    f16 = tmp_path/'f16.gguf'
    q8 = tmp_path/'q8.gguf'
    out = tmp_path/'combined.gguf'
    make(f16,[('bb.layer',(32,1),1,b'A'*64),('dd.layer',(32,1),1,b'D'*64),('codec.layer',(32,1,1),0,b'C'*128)])
    make(q8,[('bb.layer',(32,1),8,b'B'*34),('dd.layer',(32,1),8,b'E'*34),('codec.layer',(32,1),0,b'C'*128)])
    catalog = tmp_path/'catalog.json'
    core.writejson(catalog,{'packages':{name:{'files':[{'bytes':path.stat().st_size,'sha256':core.filehash(path)}]}
                                     for name,path in [('hoppou-f16',f16),('hoppou-q8',q8)]}})
    monkeypatch.setattr(mix_hoppou,'CATALOG',catalog)
    report=mix_hoppou.mix(f16,q8,out)
    assert report['types']=={'Q8_0':1,'F16':1,'F32':1}
    data=out.read_bytes()
    layout=inspect(data,layout=True)
    for row,expected in zip(layout['tensors'],[b'B'*34,b'D'*64,b'C'*128]):
        start=layout['data_offset']+row[-1]
        assert start%32==0 and data[start:start+len(expected)]==expected
    with pytest.raises(FileExistsError):
        mix_hoppou.mix(f16,q8,out)
