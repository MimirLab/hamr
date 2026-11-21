import json
from pathlib import Path

import pytest

from audiobook import hardware, cli
from audiobook.core import Store


def catalog():
    return json.loads((Path(__file__).parents[1] / 'src/audiobook/catalog.json').read_text())


def host(free=15, ram=24, vulkan='available', system='Linux', arch='x86_64', avx=True):
    return {'system': system, 'arch': arch, 'cpu_avx2': avx,
            'gpus': [{'name': 'A test GPU', 'available_vram_bytes': None if free is None else free*hardware.GIB, 'memory_kind': 'dedicated'}],
            'ram_available_bytes': ram*hardware.GIB, 'vulkan': {'status': vulkan, 'reason': 'fixture'}}


@pytest.mark.parametrize('free,expected', [(15,'vulkan-f16'), (7,'vulkan-q8'), (5,'vulkan-q6'), (3.5,'vulkan-q4'), (1,'cpu-q8')])
def test_starting_choice_uses_free_memory_and_headroom(free, expected):
    result = hardware.advise(host(free=free), catalog())
    recommendation = result['recommended_profile']
    assert recommendation['profile'] == expected
    assert recommendation['reasons'] and recommendation['uncertainty']
    assert recommendation['command'] == 'hamr setup --profile ' + expected


def test_unknown_memory_starts_small_and_does_not_claim_compatibility():
    result = hardware.advise(host(free=None, vulkan='unknown'), catalog())
    assert result['recommended_profile']['profile'] == 'vulkan-q4'
    assert result['recommended_profile']['confidence'] == 'tentative'
    assert result['uncertainties']


@pytest.mark.parametrize('ram,expected', [(24,'cpu-q8'), (10,'cpu-q4')])
def test_software_vulkan_or_missing_driver_offers_cpu(ram, expected):
    data = host(ram=ram, vulkan='unavailable');data['gpus'] = []
    result = hardware.advise(data, catalog())
    assert result['recommended_profile']['profile'] == expected
    assert not result['recommended_profile']['automatic_eligible']
    assert all(p['compatibility']=='unavailable' for p in result['profiles'] if p['implemented'] and p['id'].startswith('vulkan-'))


def test_missing_linked_loader_does_not_suggest_cpu_will_fix_it():
    data=host(vulkan='unavailable'); data['vulkan']['loader_found']=False
    result=hardware.advise(data,catalog())
    assert result['recommended_profile'] is None
    assert all(p['compatibility']=='unavailable' and 'loader library' in p['reasons'][0] for p in result['profiles'] if p['implemented'])


def test_no_identified_gpu_with_unknown_probe_prefers_cpu():
    data = host(vulkan='unknown'); data['gpus'] = []
    assert hardware.advise(data, catalog())['recommended_profile']['profile'] == 'cpu-q8'


def test_shared_memory_starts_small_and_explains_uncertainty():
    data = host(); data['gpus'][0]['memory_kind'] = 'shared'
    result = hardware.advise(data, catalog())
    assert result['recommended_profile']['profile'] == 'vulkan-q4'
    assert any('shared' in message for message in result['uncertainties'])


def test_only_vulkan_capable_gpu_memory_counts_toward_recommendation():
    data = host(free=7)
    data['gpus'][0]['vulkan_device'] = True
    data['gpus'].append({'name': 'Large unsupported GPU', 'available_vram_bytes': 20*hardware.GIB})
    result = hardware.advise(data, catalog())
    assert result['recommended_profile']['profile'] == 'vulkan-q8'
    assert result['recommended_profile']['confidence'] == 'tentative'


@pytest.mark.parametrize('change', [{'system':'Darwin','arch':'arm64'}, {'avx':False}, {'ram':1,'free':0}])
def test_unsupported_host_or_insufficient_memory_has_no_false_suggestion(change):
    result = hardware.advise(host(**change), catalog())
    assert result['recommended_profile'] is None


def test_every_model_has_cpu_and_vulkan_choices_with_bound_download_sizes():
    rows = hardware.profile_catalog(catalog());mapping = {r['id']: r for r in rows}
    assert sum(r['implemented'] for r in rows) == 9
    for variant in ('f16','q8','q6','q4'):
        gpu, cpu = mapping['vulkan-'+variant],mapping['cpu-'+variant]
        assert gpu['implemented'] and cpu['implemented']
        assert gpu['download_bytes'] == cpu['download_bytes'] > 2*hardware.GIB
    assert not mapping['cuda-bf16-eager']['implemented']
    assert mapping['vulkan-f16']['testing_status'] == 'locally_tested'
    assert mapping['vulkan-q6']['testing_status'] == 'implemented_experimental'
    assert mapping['vulkan-q8-cpu-text-encoder']['settings']['parallel'] == 1


def test_builtin_probe_is_bounded_and_does_not_need_vulkaninfo(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware.shutil, 'which', lambda _: None)
    (tmp_path/'meminfo').write_text('MemTotal: 33554432 kB\nMemAvailable: 16777216 kB\n')
    (tmp_path/'cpuinfo').write_text('flags : avx avx2 sse\n')
    calls = []
    def command(args):
        calls.append(args)
        return 0,json.dumps({'status':'available','devices':[{'name':'Test discrete GPU','type':'DISCRETE_GPU','vendor_id':0x10de,'device_id':1,'vram_bytes':8*hardware.GIB,'available_budget_bytes':7*hardware.GIB}], 'reason':'inventory','gpu_work_submitted':False}),''
    data = hardware.inspect_host(sysroot=tmp_path/'sys', procroot=tmp_path, command=command)
    assert calls[0][-1] == 'audiobook.vulkan_probe'
    assert data['vulkan']['status'] == 'available'
    assert data['gpus'][0]['available_vram_bytes'] == 7*hardware.GIB
    assert data['gpus'][0]['memory_measurement'] == 'driver_budget'
    assert data['cpu_avx2'] is True


def test_failed_inventory_stays_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware.shutil,'which',lambda _:None)
    data = hardware.inspect_host(sysroot=tmp_path,procroot=tmp_path,command=lambda _: (None,'','TimeoutExpired'))
    assert data['vulkan']['status'] == 'unknown'


def test_nvidia_memory_is_available_without_amd_specific_sysfs(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware.shutil,'which',lambda name:'/bin/tool' if name=='nvidia-smi' else None)
    def command(args):
        if args[0]=='nvidia-smi':return 0,'00000000:01:00.0, A test NVIDIA GPU, 8192, 7168\n',''
        return 0,json.dumps({'status':'unknown'}),''
    data=hardware.inspect_host(sysroot=tmp_path,procroot=tmp_path,command=command)
    assert data['gpus'][0]['vendor']=='NVIDIA'
    assert data['gpus'][0]['available_vram_bytes']==7*hardware.GIB


def test_guided_selection_uses_suggestion_only_after_user_choice(tmp_path, monkeypatch, capsys):
    data=host();advice=hardware.advise(data,catalog())
    result={'hardware':data,**advice,'issues':[]}
    monkeypatch.setattr(cli,'doctor',lambda _:result)
    monkeypatch.setattr(cli.sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr('builtins.input',lambda _: '')
    assert cli.choose_backend(Store(tmp_path/'state'),False)=='vulkan-f16'
    assert 'Suggested starting choice' in capsys.readouterr().out


def test_headless_setup_requires_explicit_selection(tmp_path, capsys):
    assert cli.main(['--home',str(tmp_path/'state'),'setup','--json'])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='profile_required'


def test_guided_choice_retries_typo_then_cancels_without_assets(tmp_path, monkeypatch, capsys):
    from audiobook.core import Error
    data=host();advice=hardware.advise(data,catalog())
    monkeypatch.setattr(cli,'doctor',lambda _:{'hardware':data,**advice,'issues':[]})
    monkeypatch.setattr(cli.sys.stdin,'isatty',lambda:True)
    answers=iter(['a typo','q'])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    store=Store(tmp_path/'state')
    with pytest.raises(Error) as failure:cli.choose_backend(store,False)
    assert failure.value.code=='setup_cancelled'
    assert list((store.root/'assets').iterdir())==[]
    assert 'Press Enter to install' in capsys.readouterr().out
