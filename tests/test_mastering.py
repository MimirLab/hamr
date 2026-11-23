import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from audiobook import mastering
from audiobook.core import Error, RATE, filehash
from audiobook.jobs import final_path, render, repair
from audiobook.media import packet_hash
from test_media import prepared


def cli(store, *args, env=None):
    return subprocess.run([sys.executable, '-m', 'hamr', '--home', str(store.root), *args, '--json'],
                          capture_output=True, text=True, env=env)


def test_legacy_book_remasters_and_recovers_without_touching_approved_takes(tmp_path, monkeypatch):
    source = tmp_path / 'book.md'
    source.write_text('# One\n\nThe room remains quiet.\n\n# Two\n\nThe second line still follows.')
    store, job = prepared(tmp_path, source)
    jid = job['id']
    assert job.pop('mastering') == 'natural'
    store.save(job)
    assert json.loads(cli(store, 'status', jid).stdout)['mastering'] == 'off'

    original = Path(render(store, jid, allow_synthesis=False)['output'])
    audio_hash = filehash(original / 'book.m4b')
    inputs = {path: filehash(path) for path in (store.root/'clips').glob('*/audio.pcm')}
    assert json.loads((original / 'report.json').read_text())['mastering'] == {'mode': 'off'}
    previous_plan = store.get(jid)['plan']
    changed = cli(store, 'repair', jid, '--mastering', 'natural')
    assert changed.returncode == 0, changed.stderr
    revised = store.get(jid)
    assert revised['plan'] == previous_plan
    assert 'metadata_repackage' not in revised

    crash = cli(store, 'resume', jid, '--no-synthesis',
                env={**os.environ, 'AUDIOBOOK_FAULT': 'after_mastering_analysis'})
    assert crash.returncode == 86
    assert not final_path(revised).exists()
    assert json.loads(cli(store, 'status', jid).stdout)['status'] == 'paused'
    cache_path = store.path(revised) / 'mastering-analysis.json'
    cache_hash = filehash(cache_path)

    finished = cli(store, 'resume', jid, '--no-synthesis')
    assert finished.returncode == 0, finished.stderr
    result = json.loads(finished.stdout)
    final = Path(result['output'])
    receipt = json.loads((final / 'report.json').read_text())['mastering']
    assert result['metrics']['clips_rendered'] == 0
    assert receipt['mode'] == 'natural' and receipt['analysis_cache_reused']
    assert receipt['decoded']['true_peak_dbtp'] <= mastering.BOOK['export_ceiling_dbtp']
    assert receipt['applied_gain_db'] != 0
    assert filehash(cache_path) == cache_hash
    assert filehash(original / 'book.m4b') == audio_hash
    assert all(filehash(path) == sha for path, sha in inputs.items())
    for filename in ('companion.json', 'companion.vtt'):
        assert (original/filename).read_bytes() == (final/filename).read_bytes()

    repair(store, jid, metadata={'title': 'Revised title'})

    def unexpected_analysis(*args, **kwargs):
        pytest.fail('Metadata-only repair must stream-copy the mastered audio')

    monkeypatch.setattr(mastering, 'analyze', unexpected_analysis)
    remux = Path(render(store, jid, allow_synthesis=False)['output'])
    assert packet_hash(remux / 'book.m4b') == packet_hash(final / 'book.m4b')
    assert json.loads((remux / 'report.json').read_text())['mastering'] == {**receipt, 'audio_stream_copy': True}


def test_take_diagnostics_only_compare_comparable_speech_and_reuse_unaffected_pcm(tmp_path, monkeypatch):
    blocks = []
    units = []
    takes = []
    receipts = []
    for i, amplitude in enumerate((6000, 6000, 13000, 6000, 1200, 21000, 3200, 0, 27000)):
        block = str(i)
        blocks.append({'id': block, 'role': 'body', 'locator': {'line': i + 1}})
        unit = {'id': f'u{i}', 'chapter': 'One', 'sources': [block]}
        if i in (4, 5, 6):
            unit['performance_candidate'] = {'recipe': {4: 'whisper', 5: 'forceful', 6: 'sigh'}[i]}
        units.append(unit)
        pcm = (np.sin(np.arange(RATE) * 2 * np.pi * 311 / RATE) * amplitude).astype('<i2')
        path = tmp_path / (block + '.pcm')
        path.write_bytes(pcm.tobytes())
        takes.append((path, 400))
        receipts.append({'sha256': filehash(path), 'samples': RATE})
    blocks.insert(-1, {'id': 'break', 'role': 'scene_break', 'locator': {'line': 9}})
    job = {'document': {'blocks': blocks}, 'plan': {'utterances': units, 'decisions': []}}
    cache = tmp_path / 'analysis.json'

    reading, levels, reused = mastering.analyze(cache, job, takes, receipts)
    assert not reused
    assert reading['true_peak_dbtp'] is not None
    assert levels['findings'] == [{'utterance': 'u2', 'offset_db': pytest.approx(6.72, abs=.02),
                                   'nearby_median_dbfs': pytest.approx(-17.76, abs=.02),
                                   'source_locations': [{'line': 3}]}]
    assert [r['protection'] for r in levels['takes'][4:8]] == ['directed_or_possible_performance'] * 3 + ['too_little_active_speech']

    checked = []
    original = mastering._take_reading

    def count(path):
        checked.append(path.name)
        return original(path)

    monkeypatch.setattr(mastering, '_take_reading', count)
    assert mastering.analyze(cache, job, takes, receipts)[2]
    assert checked == []
    path, _ = takes[2]
    path.write_bytes(takes[0][0].read_bytes()[::-1])
    receipts[2]['sha256'] = filehash(path)
    assert not mastering.analyze(cache, job, takes, receipts)[2]
    assert checked == ['2.pcm']
    takes[2] = (path, 800)
    assert not mastering.analyze(cache, job, takes, receipts)[2]
    assert checked == ['2.pcm']


def test_peak_retry_uses_same_source_with_extra_headroom(monkeypatch):
    measurements = iter(({'integrated_lufs': -18.8, 'true_peak_dbtp': -1.35},
                         {'integrated_lufs': -19.5, 'true_peak_dbtp': -2.17}))
    monkeypatch.setattr(mastering, 'meter', lambda path: next(measurements))
    gains = []
    receipt = mastering.encode_checked('book.m4b', {'integrated_lufs': -20, 'true_peak_dbtp': -3.5},
                                       mastering.BOOK, gains.append)
    assert gains == [1, .25]
    assert receipt['decoded']['true_peak_dbtp'] == -2.17
    assert receipt['peak_limited']
    assert mastering.initial_gain({'integrated_lufs': None, 'true_peak_dbtp': -10}, mastering.BOOK) == 0


def test_failed_master_stops_and_prints_working_recovery_choices(tmp_path, monkeypatch):
    source = tmp_path / 'book.md'
    source.write_text('Keep the original words.')
    store, job = prepared(tmp_path, source)

    def failed(*args):
        raise Error('mastering_peak', 'The decoded export exceeded the peak ceiling.')

    monkeypatch.setattr(mastering, 'encode_checked', failed)
    with pytest.raises(Error) as status:
        render(store, job['id'], allow_synthesis=False)
    assert status.value.code == 'mastering_peak'
    assert f"resume {job['id']} --no-synthesis" in status.value.action
    assert f"repair {job['id']} --mastering off" in status.value.action
    assert not final_path(job).exists()
    assert store.get(job['id'])['status'] == 'failed'
    assert cli(store, 'repair', job['id'], '--mastering', 'off').returncode == 0
    finished = cli(store, 'resume', job['id'], '--no-synthesis')
    assert json.loads(finished.stdout)['metrics']['clips_rendered'] == 0


def test_continuity_edits_are_local_and_companion_tracks_real_pause_samples(tmp_path):
    from audiobook.backend import validate_pcm
    from audiobook.core import atomic, writejson
    from audiobook.jobs import clip_key, timeline

    source = tmp_path / 'book.md'
    source.write_text('# First\n\n' + '\n\n'.join(f'The narrator reads passage number {i} aloud.' for i in range(1, 7))
                      + '\n\n# Second\n\nThe next chapter begins here.')
    store, job = prepared(tmp_path, source)
    units = job['plan']['utterances']
    quiet = np.zeros(RATE // 15, dtype='<i2')
    tone = (8000 * np.sin(np.arange(RATE) * 2 * np.pi * 220 / RATE)).astype('<i2')
    receipts = []
    raw_hashes = {}
    for index, unit in enumerate(units):
        speech = tone if index != 3 else (tone // 4).astype('<i2')
        pcm = np.concatenate((quiet, speech, quiet)).astype('<i2')
        if index == 2:
            pcm[-1] = 2000
        payload = pcm.tobytes()
        _, receipt = validate_pcm(payload)
        k = clip_key(unit, job)
        folder = store.root/'clips'/k
        atomic(folder/'audio.pcm', payload)
        writejson(folder/'receipt.json', {**receipt, 'key': k})
        receipts.append(receipt)
        raw_hashes[folder/'audio.pcm'] = receipt['sha256']

    original_rows, original_chapters, original_samples = timeline(units, receipts)
    output = Path(render(store, job['id'], allow_synthesis=False)['output'])
    report = json.loads((output/'report.json').read_text())
    companion = json.loads((output/'companion.json').read_text())
    edits = report['mastering']['take_levels']['continuity']['edits']
    assert edits[units[2]['id']]['fade_out_samples'] == 8
    assert edits[units[3]['id']]['gain_db'] == 3
    assert len([e for e in edits.values() if 'gain_db' in e]) == 1
    assert units[0]['id'] not in edits
    assert units[-2]['id'] not in edits
    saved_pause = sum(e.get('planned_pause_samples', 0) - e.get('pause_samples', 0) for e in edits.values())
    assert saved_pause > 0 and companion['samples'] == original_samples - saved_pause
    assert companion['chapters'][1]['start_sample'] == original_chapters[1]['start_sample'] - saved_pause
    assert all(row['end_sample'] - row['start_sample'] == r['samples']
               for row, r in zip(companion['segments'], receipts, strict=True))
    adjusted = next(row for row in companion['segments'] if row.get('planned_pause_samples'))
    assert adjusted['pause_samples'] < adjusted['planned_pause_samples']
    assert all(filehash(path) == digest for path, digest in raw_hashes.items())

    repair(store, job['id'], metadata={'title': 'Replacement title'})
    copy = Path(render(store, job['id'], allow_synthesis=False)['output'])
    for filename in ('companion.vtt', 'companion.json'):
        assert (copy/filename).read_bytes() == (output/filename).read_bytes()
    assert packet_hash(copy/'book.m4b') == packet_hash(output/'book.m4b')


def test_isolated_step_detector_does_not_treat_a_quiet_initial_consonant_or_plosive_as_a_click(tmp_path):
    from audiobook import mastering_continuity as continuity

    leading = np.zeros(RATE, dtype='<i2')
    leading[0] = 4000
    leading[29:429] = np.resize([185, -185], 400)
    leading[-1] = -5500
    path = tmp_path / 'take.pcm'
    path.write_bytes(leading.tobytes())
    edges = continuity.observe_edges(path, len(leading))
    assert edges['leading_step'] == 0
    assert edges['trailing_step'] == 8
    units = [{'id': 'a'}]
    audio = np.frombuffer(b''.join(continuity.render([(path, 0)], units, {'a': {'fade_out_samples': 8}})), dtype='<f4')
    assert np.array_equal(audio[:-8], leading[:-8] / 32768)
    assert len(audio) == len(leading) and audio[-1] == 0


def test_mastering_protects_single_quote_dialogue_without_treating_apostrophes_as_quotes():
    from audiobook.mastering_continuity import _protected_form

    assert _protected_form({'text': "'Stay here,' she said."})
    assert _protected_form({'text': '‘Stay here,’ she said.'})
    assert not _protected_form({'text': "The fox's tail is long, isn't it?"})
