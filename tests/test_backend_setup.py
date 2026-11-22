"""Exercise real setup/verification with tiny assets; never start a speech model."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from audiobook import backend, cli, hardware
from audiobook.core import Error, Store, loadjson, writejson


@pytest.fixture
def assets(tmp_path, monkeypatch):
    catalog = loadjson(backend.CATALOG)
    payloads = {}
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        binary = b'fixture executable; no model is run\n'
        entry = tarfile.TarInfo('engine/breeze-server')
        entry.size = len(binary)
        tar.addfile(entry, io.BytesIO(binary))

    def asset(name, content):
        url = 'https://setup-fixture.invalid/' + name
        payloads[url] = content
        return {'url': url, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}

    runtime = catalog['runtimes']['hoppou']['linux_x86_64']
    runtime.update(asset(runtime['filename'], archive.getvalue()))
    for precision in ('f16', 'q8', 'q6', 'q4'):
        package = catalog['packages']['hoppou-' + precision]
        for file in package['files']:
            file.update(asset(file['path'], ('fixture weights ' + precision).encode()))
        package['payload_bytes'] = sum(f['bytes'] for f in package['files'])
    license_file = next(f for f in catalog['packages']['official']['files'] if f['path'] == 'LICENSE')
    license_file.update(asset('LICENSE', b'fixture model terms'))
    path = tmp_path / 'fixture-catalog.json'
    writejson(path, catalog)
    monkeypatch.setattr(backend, 'CATALOG', path)
    monkeypatch.setattr(backend.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(backend.platform, 'machine', lambda: 'x86_64')
    transfers = []

    def transfer(url, part, offset, size, **kwargs):
        transfers.append(url)
        content = payloads[url]
        assert size == len(content)
        with part.open('ab' if offset else 'wb') as out:
            out.write(content[offset:])

    monkeypatch.setattr(backend, 'transfer_asset', transfer)
    return catalog, payloads, transfers


@pytest.mark.parametrize('profile', list(hardware.BUDGETS))
def test_selected_variant_acquires_only_its_assets_and_activates_after_health(tmp_path, monkeypatch, capsys, assets, profile):
    catalog, payloads, transfers = assets
    entry = next(p for p in catalog['profiles'] if p['id'] == profile)
    package = catalog['packages'][entry['packages'][0]]
    home = tmp_path / 'fresh state with spaces'
    seen = []

    class Healthy:
        def __init__(self, store, identity):
            seen.append(identity)
            assert not (store.root / 'settings.json').exists()
            assert identity['settings'] == entry['settings']
            assert Path(identity['model']).read_bytes() == payloads[package['files'][0]['url']]
            assert Path(identity['binary']).is_file()
            self.health = {'sample_rate': 24000, 'actual_device': 'fixture', 'fixture': True}
        def __enter__(self): return self
        def __exit__(self, *_): pass

    monkeypatch.setattr(backend, 'Engine', Healthy)
    assert cli.main(['--home', str(home), 'setup', '--profile', profile, '--engine-only', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    expected = {catalog['runtimes']['hoppou']['linux_x86_64']['url'],
                *(f['url'] for f in package['files']),
                next(f['url'] for f in catalog['packages']['official']['files'] if f['path'] == 'LICENSE')}
    assert set(transfers) == expected
    assert result['profile'] == profile
    assert loadjson(home / 'settings.json') == seen[0]
    assert not (home / 'profiles' / (profile + '.json')).exists()
    assert list((home / 'assets').iterdir()) == [home / 'assets' / profile]


@pytest.mark.parametrize('log,code', [
    ('ggml: failed to allocate device buffer: VK_ERROR_OUT_OF_DEVICE_MEMORY', 'insufficient_memory'),
    ('Vulkan: VK_ERROR_INCOMPATIBLE_DRIVER', 'vulkan_unavailable'),
    ('worker exited for an unrelated reason', 'backend_start_failed'),
])
def test_failed_startup_preserves_active_settings_with_actionable_error(tmp_path, monkeypatch, assets, log, code):
    store = Store(tmp_path / 'state')
    writejson(store.root / 'settings.json', {'profile': 'previously-working', 'job_pins': 'untouched'})
    before = (store.root / 'settings.json').read_bytes()
    class Failed:
        def __init__(self, store, identity): self.store = store
        def __enter__(self):
            (self.store.root / 'runtime/backend.log').write_text(log)
            raise Error('backend_start_failed', 'Fixture startup failed.', 'Inspect startup.')
        def __exit__(self, *_): pass
    monkeypatch.setattr(backend, 'Engine', Failed)
    with pytest.raises(Error) as failure:
        backend.setup(store, 'vulkan-q8')
    assert failure.value.code == code
    assert 'hamr doctor' in failure.value.action
    assert (store.root / 'settings.json').read_bytes() == before


def test_unsupported_platform_rejected_before_acquisition(tmp_path, monkeypatch, assets):
    _, _, transfers = assets
    monkeypatch.setattr(backend.platform, 'machine', lambda: 'arm64')
    with pytest.raises(Error) as failure:
        backend.setup(Store(tmp_path / 'state'), 'vulkan-f16')
    assert failure.value.code == 'unsupported_platform'
    assert transfers == []


def test_setup_replaces_mismatched_runtime_without_overwriting_prior_job_pins(tmp_path,monkeypatch,assets):
    catalog,payloads,_=assets
    store=Store(tmp_path/'state')
    monkeypatch.setattr(backend,'Engine',lambda *_: Healthy())
    first=backend.setup(store,'vulkan-q8')['identity']
    previous_binary=Path(first['binary'])
    previous_model=Path(first['model'])
    previous_weights=previous_model.read_bytes()
    previous_binary.write_bytes(b'damaged previous extraction')

    runtime=catalog['runtimes']['hoppou']['linux_x86_64']
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w:gz') as tar:
        binary=b'a different verified runtime'
        entry=tarfile.TarInfo('engine/breeze-server')
        entry.size=len(binary)
        tar.addfile(entry,io.BytesIO(binary))
        link=tarfile.TarInfo('engine/entry-link')
        link.type,link.linkname=tarfile.SYMTYPE,'breeze-server'
        tar.addfile(link)
    model=catalog['packages']['hoppou-q8']['files'][0]
    for record,data in ((runtime,archive.getvalue()),(model,b'different verified weights')):
        payloads[record['url']]=data
        record.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    writejson(backend.CATALOG,catalog)
    current=backend.setup(store,'vulkan-q8')['identity']
    assert Path(current['binary']).read_bytes()==binary
    assert Path(current['model']).read_bytes()==b'different verified weights'
    assert previous_binary.read_bytes()==b'damaged previous extraction' and previous_model.read_bytes()==previous_weights
    assert current['runtime_archive_sha256']==runtime['sha256']
    assert backend.setup(store,'vulkan-q8')['identity']==current

    Path(current['binary']).write_bytes(b'damaged current extraction')
    repaired=backend.setup(store,'vulkan-q8')['identity']
    assert repaired['binary']!=current['binary'] and Path(repaired['binary']).read_bytes()==binary
    assert Path(current['binary']).read_bytes()==b'damaged current extraction'
    assert backend.setup(store,'vulkan-q8')['identity']==repaired


class Healthy:
    health={'actual_device':'cpu'}

    def __enter__(self):return self
    def __exit__(self,*_):pass


def test_setup_counts_only_missing_bytes_for_a_bound_partial(tmp_path,monkeypatch,assets):
    catalog,payloads,_=assets
    store=Store(tmp_path/'state')
    monkeypatch.setattr(backend,'Engine',lambda *_: Healthy())
    runtime=catalog['runtimes']['hoppou']['linux_x86_64']
    model=catalog['packages']['hoppou-q8']['files'][0]
    license_file=next(f for f in catalog['packages']['official']['files'] if f['path']=='LICENSE')
    directory=store.root/'assets/vulkan-q8'
    directory.mkdir()
    part=directory/(model['path']+'.part')
    offset=model['bytes']-3
    part.write_bytes(payloads[model['url']][:offset])
    receipt=part.with_suffix('.part.json')
    receipt.write_text('invalid partial receipt')
    free=1024**3+runtime['bytes']+license_file['bytes']+3
    monkeypatch.setattr(backend.shutil,'disk_usage',lambda _:SimpleNamespace(free=free))
    with pytest.raises(Error) as rejected:backend.setup(store,'vulkan-q8')
    assert rejected.value.code=='disk_space' and part.stat().st_size==offset
    writejson(receipt,{k:model[k] for k in ('url','sha256','bytes')})
    result=backend.setup(store,'vulkan-q8')
    assert Path(result['identity']['model']).read_bytes()==payloads[model['url']]
    assert not part.exists() and not receipt.exists()


def test_combined_setup_activates_only_after_all_requested_stages(tmp_path,monkeypatch,capsys,assets):
    from audiobook import checks_setup,ocr_setup
    _,_,transfers=assets
    store=Store(tmp_path/'state')
    previous={'settings.json':{'profile':'old-speech'},'speech-checks.json':{'profile':'old-checks'},
              'ocr-settings.json':{'profile':'old-ocr'}}
    for name,value in previous.items():writejson(store.root/name,value)
    monkeypatch.setattr(backend,'Engine',lambda *_:Healthy())
    monkeypatch.setattr(checks_setup,'preflight',lambda:None)
    check_result={'profile':'new-checks'}
    monkeypatch.setattr(checks_setup,'provision',lambda *a,activate:check_result if not activate else pytest.fail('early ASR activation'))
    args=['--home',str(store.root),'setup','--profile','vulkan-q8','--ocr-profile','auto','--json']
    monkeypatch.setattr(ocr_setup,'tesseract_binary',lambda:(tmp_path/'missing',None))
    assert cli.main(args)==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='missing_ocr' and not transfers
    monkeypatch.setattr(ocr_setup,'preflight',lambda _:None)

    def check_ocr(*args,activate):
        assert not activate
        assert all(loadjson(store.root/name)==value for name,value in previous.items())
        raise Error('ocr_setup_failed','OCR startup control failed.')

    monkeypatch.setattr(ocr_setup,'provision',check_ocr)
    assert cli.main(args)==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='ocr_setup_failed'
    assert all(loadjson(store.root/name)==value for name,value in previous.items())
    monkeypatch.setattr(ocr_setup,'provision',lambda *a,activate:{'profile':'new-ocr'} if not activate else pytest.fail('early OCR activation'))
    assert cli.main(args)==0
    result=json.loads(capsys.readouterr().out)
    assert loadjson(store.root/'settings.json')==result['identity']
    assert loadjson(store.root/'speech-checks.json')==check_result
    assert loadjson(store.root/'ocr-settings.json')==result['ocr']
