from pathlib import Path
import wave

import numpy as np
import pytest

from audiobook import hardware, reference_preparation as preparation, wash_setup
from audiobook.backend import save_voice
from audiobook.core import Error, RATE, Store, filehash, loadjson
from audiobook.jobs import cleanup


def write_wav(path, signal, channels=1):
    with wave.open(str(path), 'wb') as output:
        output.setparams((channels, 2, RATE, 0, 'NONE', 'not compressed'))
        output.writeframes((signal * 32767).astype('<i2').tobytes())


def samples():
    seconds = np.arange(8 * RATE) / RATE
    hum = .018 * np.sin(2 * np.pi * 60 * seconds) + .009 * np.sin(2 * np.pi * 120 * seconds)
    speech = .14 * np.sin(2 * np.pi * 207 * seconds) * ((seconds % 2) < .9)
    return hum, speech


def test_quiet_frames_detect_stationary_hum_and_protect_edges():
    hum, speech = samples()
    assert preparation.electrical_hum(speech) is None
    evidence = preparation.electrical_hum(speech + hum)
    assert evidence[0] == 60
    assert preparation.hum_reduced(speech, evidence)
    assert not preparation.hum_reduced(speech + hum, evidence)
    cut = speech.copy()
    cut[:RATE // 10] = 0
    assert preparation.preserves(speech + hum, speech)
    assert not preparation.preserves(speech + hum, cut)
    cut = speech.copy()
    cut[6*RATE:] = 0
    assert not preparation.preserves(speech + hum, cut)


def test_source_snapshot_and_retry_keep_exact_bytes(tmp_path, monkeypatch):
    store = Store(tmp_path/'state')
    source = tmp_path/'source.wav'
    _, speech = samples()
    write_wav(source, speech)
    sha = filehash(source)

    class Interrupted(Exception):
        pass

    with monkeypatch.context() as scoped:
        def stop(point):
            if point == 'before_voice_promote':
                raise Interrupted
        scoped.setattr('audiobook.core.fault', stop)
        with pytest.raises(Interrupted):
            save_voice(store, 'example', source, 'A verified reference.')
    assert not (store.root/'voices'/'example').exists()
    ready = save_voice(store, 'example', source, 'A verified reference.')
    assert ready['preparation']['status'] == 'baseline'
    assert filehash(Path(ready['original_reference'])) == sha
    receipt = loadjson(Path(ready['preparation_receipt']))
    assert receipt['source_sha256'] == sha
    assert receipt['reference_sha256'] == ready['reference_sha256']
    assert not cleanup(store, apply=True)['candidates']
    assert filehash(Path(ready['reference'])) == ready['reference_sha256']


def test_stereo_phase_can_be_recovered_by_channel_selection(tmp_path):
    store = Store(tmp_path/'state')
    file = tmp_path/'stereo.wav'
    _, speech = samples()
    write_wav(file, np.column_stack((speech, -speech)), channels=2)
    with pytest.raises(Error, match='usable audio'):
        save_voice(store, 'phase', file, 'The phase control.')
    saved = save_voice(store, 'phase', file, 'The phase control.', channel=2)
    receipt = loadjson(Path(saved['preparation_receipt']))
    assert receipt['media']['channels'] == 2
    assert receipt['channel'] == 2


def test_auto_candidate_changes_only_when_local_checks_pass(tmp_path, monkeypatch):
    store = Store(tmp_path/'state')
    hum, speech = samples()
    original = tmp_path/'original.wav'
    answer = tmp_path/'candidate.wav'
    write_wav(original, speech+hum)
    write_wav(answer, speech)
    monkeypatch.setattr(hardware, 'inspect_host', lambda: {
        'system':'Linux','arch':'x86_64','ram_available_bytes':1024**3})
    monkeypatch.setattr(wash_setup, 'configured', lambda *args: {'test': True})
    monkeypatch.setattr(preparation, 'candidate', lambda *args: (answer, {'id':'candidate'}, True))
    monkeypatch.setattr(preparation, 'verify_transcript', lambda *args: False)
    rejected = save_voice(store, 'unsafe', original, 'The source remains.')
    assert loadjson(Path(rejected['preparation_receipt']))['error_code'] == 'wash_transcript'
    monkeypatch.setattr(preparation, 'verify_transcript', lambda *args: True)
    accepted = save_voice(store, 'safe', original, 'The source remains.')
    assert accepted['reference_sha256'] == filehash(answer)
    assert accepted['preparation']['cache_reused']


def test_lighter_profile_is_reported_on_small_cpu(tmp_path):
    store = Store(tmp_path/'state')
    low = {'system':'Linux','arch':'x86_64','ram_available_bytes':300*1024**2}
    description = wash_setup.describe(store, low)
    assert description['recommended_profile'] == 'fastenhancer-tiny'
    assert description['profiles'][0]['automatic_for'] == ['steady_mains_hum']
    assert not description['profiles'][0]['compatible']
    assert description['profiles'][1]['compatible']


def test_unknown_install_has_a_recovery_command_and_never_activates(tmp_path):
    store = Store(tmp_path/'state')
    source = tmp_path/'source.wav'
    _, speech = samples()
    write_wav(source, speech)
    with pytest.raises(Error) as caught:
        save_voice(store, 'missing', source, 'Inspect the source.', wash='dpdfnet-hr')
    assert caught.value.code == 'wash_unavailable'
    assert 'setup --wash-only --wash-profile dpdfnet-hr' in caught.value.action
    assert not (store.root/'voices'/'missing').exists()


def test_cached_treatment_binds_the_output_and_native_audio(tmp_path):
    from audiobook.core import writejson

    identity = {'recipe':'test'}
    wav, native = tmp_path/'reference.wav', tmp_path/'native.f32le'
    _, speech = samples()
    write_wav(wav, speech)
    native.write_bytes(speech.astype('<f4').tobytes())
    receipt = {'identity': identity, 'candidate_sha256': filehash(wav),
               'native_pcm_sha256': filehash(native)}
    writejson(tmp_path/'receipt.json', receipt)
    assert preparation._read_cache(tmp_path, identity) == (wav, receipt)
    native.write_bytes(b'corrupt')
    assert preparation._read_cache(tmp_path, identity) is None
