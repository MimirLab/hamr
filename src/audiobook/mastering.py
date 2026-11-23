"""Streaming programme gain and performance-aware take diagnostics."""
from collections import defaultdict
import json
import math
import re
from statistics import median
import subprocess
import tempfile

import numpy as np

from .core import RATE, Error, key, loadjson, run, writejson
from . import mastering_continuity as continuity


MODES = ('natural', 'off')
DEFAULT = 'natural'
PCM_INPUT = ('-f', 's16le', '-ar', str(RATE), '-ac', '1', '-i', 'pipe:0')
FLOAT_INPUT = ('-f', 'f32le', *PCM_INPUT[2:])
BOOK = {'target_lufs': -19, 'max_boost_db': 6, 'input_ceiling_dbtp': -2.5, 'export_ceiling_dbtp': -2}
GALLERY = {**BOOK, 'target_lufs': -22, 'max_boost_db': 12}
METER = 'ffmpeg-loudnorm-input-v1'
TAKE_METER = 'rms-active-100ms-v1'
OFFSET_DB = 6
CHUNK = RATE * 2


def rendered_pcm(takes):
    """Yield accepted mono PCM and planned silence in bounded chunks."""
    for path, pause in takes:
        with path.open('rb') as stream:
            while chunk := stream.read(CHUNK):
                yield chunk
        remaining = pause * 2
        while remaining:
            size = min(remaining, CHUNK)
            yield bytes(size)
            remaining -= size


def run_audio(args, chunks=None, *, error_code, message, action=""):
    """Spool diagnostics to disk so FFmpeg can consume a large pipe without deadlock."""
    with tempfile.TemporaryFile() as log:
        with subprocess.Popen(args, stdin=subprocess.PIPE if chunks is not None else subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=log) as process:
            try:
                if chunks is not None:
                    try:
                        for chunk in chunks:
                            process.stdin.write(chunk)
                        process.stdin.close()
                    except BrokenPipeError:
                        process.wait()
                        raise Error(error_code, message + _diagnostic(log), action) from None
                if process.wait():
                    raise Error(error_code, message + _diagnostic(log), action)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        return _tail(log, 65536)


def _tail(log, limit):
    length = log.seek(0, 2)
    log.seek(max(0, length - limit))
    return log.read().decode('utf-8', 'replace')


def _diagnostic(log):
    lines = _tail(log, 4096).strip().splitlines()
    return f' FFmpeg: {lines[-1]}' if lines else ''


def meter(source=None, *, chunks=None, pcm_input=PCM_INPUT):
    inputs = pcm_input if source is None else ('-i', str(source))
    log = run_audio(['ffmpeg', '-hide_banner', '-nostdin', '-xerror', '-nostats', '-v', 'info',
                     *inputs, '-map', '0:a:0', '-af',
                     'loudnorm=I=-19:TP=-2.5:LRA=11:dual_mono=false:print_format=json',
                     '-f', 'null', '-'], chunks, error_code='mastering_measure_failed',
                    message='FFmpeg could not measure the complete audio stream.')
    matches = re.findall(r'\{\s*"input_i"\s*:[^{}]+\}', log)
    try:
        data = json.loads(matches[-1])
        loudness, peak = (float(data[field]) for field in ('input_i', 'input_tp'))
        if any(not math.isfinite(number) and number != -math.inf for number in (loudness, peak)):
            raise ValueError
    except (IndexError, ValueError, KeyError, TypeError) as exc:
        raise Error('mastering_measure_failed', 'FFmpeg did not return a valid loudness and true peak reading.') from exc
    return {'integrated_lufs': loudness if math.isfinite(loudness) else None,
            'true_peak_dbtp': peak if math.isfinite(peak) else None}


def initial_gain(reading, recipe):
    loudness = reading['integrated_lufs']
    gain = 0 if loudness is None else min(recipe['target_lufs'] - loudness, recipe['max_boost_db'])
    peak = reading['true_peak_dbtp']
    if peak is not None:
        gain = min(gain, recipe['input_ceiling_dbtp'] - peak)
    return math.floor(gain * 100 + 1e-8) / 100


def encode_checked(path, reading, recipe, encode):
    """Retry only from the approved input stream, with static gain and codec headroom."""
    gain = initial_gain(reading, recipe)
    attempts = []
    for _ in range(3):
        encode(gain)
        measured = meter(path)
        attempts.append({'gain_db': gain, **measured})
        peak = measured['true_peak_dbtp']
        if peak is None and reading['true_peak_dbtp'] is not None:
            raise Error('mastering_measure_failed', 'The decoded export lost all measurable signal.')
        if peak is None or peak <= recipe['export_ceiling_dbtp']:
            loudness = reading['integrated_lufs']
            peak_limited = (loudness is not None and gain < min(recipe['target_lufs'] - loudness,
                         recipe['max_boost_db']) - .011)
            return {'meter': METER, 'recipe': recipe, 'input': reading, 'applied_gain_db': gain,
                    'decoded': measured, 'peak_limited': peak_limited, 'attempts': attempts}
        gain = math.floor((gain - (peak - recipe['export_ceiling_dbtp']) - .1) * 100 + 1e-8) / 100
    raise Error('mastering_peak', f'The decoded export reached {peak:.2f} dBTP after three attempts. '
                f'The required ceiling is {recipe["export_ceiling_dbtp"]} dBTP.')


def _take_reading(path):
    # Clip receipts cap duration. Read at most one second of samples at a time.
    peak = 0
    energy = 0.0
    active_samples = 0
    with path.open('rb') as stream:
        while chunk := stream.read(CHUNK):
            samples = np.frombuffer(chunk, dtype='<i2').astype(np.float64)
            peak = max(peak, float(np.max(np.abs(samples))))
            window = RATE // 10
            count = len(samples) // window
            windows = samples[:count * window].reshape(count, window)
            squares = np.sum(windows * windows, axis=1)
            lengths = np.full(count, window)
            if len(tail := samples[count * window:]) >= window // 2:
                squares = np.append(squares, np.dot(tail, tail))
                lengths = np.append(lengths, len(tail))
            active = squares >= lengths * (32768 * 10 ** (-48 / 20)) ** 2
            energy += float(squares[active].sum())
            active_samples += int(lengths[active].sum())

    return {'sample_peak_dbfs': round(20 * math.log10(peak / 32768), 2) if peak else None,
            'active_rms_dbfs': round(10 * math.log10(energy / active_samples / 32768**2), 2) if active_samples else None,
            'active_samples': active_samples}


def _valid_db(value):
    return value is None or (type(value) in (int, float) and math.isfinite(value))


def _valid_take(reading, samples):
    return (isinstance(reading, dict) and {'active_samples', 'sample_peak_dbfs', 'active_rms_dbfs'} == reading.keys()
            and type(reading['active_samples']) is int and 0 <= reading['active_samples'] <= samples
            and all(_valid_db(reading[field]) for field in ('sample_peak_dbfs', 'active_rms_dbfs'))
            and (reading['active_rms_dbfs'] is not None) == bool(reading['active_samples']))


def _assess(job, receipts, readings):
    blocks = {block['id']: block for block in job['document']['blocks']}
    pauses = {entry['block'] for entry in job['plan']['decisions'] if entry['action'] == 'pause'}
    scenes = {}
    scene = 0
    for bid, block in blocks.items():
        scenes[bid] = scene
        if bid in pauses or block['role'] == 'scene_break':
            scene += 1

    levels = []
    groups = defaultdict(list)
    for unit, receipt in zip(job['plan']['utterances'], receipts, strict=True):
        reading = readings[receipt['sha256']]
        chapter = unit.get('chapter_id') or unit['chapter']
        scene_ids = {scenes[bid] for bid in unit['sources'] if bid in scenes}
        roles = {blocks[bid]['role'] for bid in unit['sources'] if bid in blocks}
        protection = None
        if unit.get('performance') or unit.get('performance_candidate') or unit.get('performance_state'):
            protection = 'directed_or_possible_performance'
        elif (len(scene_ids) != 1 or roles != {'body'} or unit.get('literal_repeat')
              or unit.get('delivery_boundary') == 'authored_lines'
              or any(bid in pauses for bid in unit['sources'])):
            protection = 'source_structure'
        elif reading['active_samples'] < max(RATE // 2, receipt['samples'] // 5):
            protection = 'too_little_active_speech'
        row = {'utterance': unit['id'], 'source_blocks': unit['sources'], 'chapter': chapter,
               'samples': receipt['samples'], 'seconds': receipt['samples'] / RATE, **reading,
               'protection': protection,
               'scene': next(iter(scene_ids)) if len(scene_ids) == 1 else None}
        levels.append(row)
        if protection is None:
            groups[chapter, next(iter(scene_ids))].append(row)

    findings = []
    for group in groups.values():
        for index, row in enumerate(group):
            nearby = group[max(0, index - 4):index] + group[index + 1:index + 5]
            if len(nearby) < 3:
                continue
            baseline = median(other['active_rms_dbfs'] for other in nearby)
            offset = round(row['active_rms_dbfs'] - baseline, 2)
            if abs(offset) >= OFFSET_DB:
                findings.append({'utterance': row['utterance'], 'offset_db': offset,
                                 'nearby_median_dbfs': round(baseline, 2),
                                 'source_locations': [{k: v for k, v in blocks[bid]['locator'].items()
                                                       if k not in ('exact', 'rows')}
                                                      for bid in row['source_blocks']]})
    return {'meter': TAKE_METER, 'offset_threshold_db': OFFSET_DB, 'takes': levels, 'findings': findings}


def analyze(cache_path, job, takes, receipts):
    try:
        cache = loadjson(cache_path)
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    old_takes = cache.get('takes') if cache.get('take_meter') == TAKE_METER else None
    readings = {}
    for (path, _), receipt in zip(takes, receipts, strict=True):
        sha = receipt['sha256']
        if sha not in readings:
            saved = old_takes.get(sha) if isinstance(old_takes, dict) else None
            readings[sha] = saved if _valid_take(saved, receipt['samples']) else _take_reading(path)
    updated = {'take_meter': TAKE_METER, 'takes': readings}
    units = job['plan']['utterances']
    diagnostics = _assess(job, receipts, readings)
    stream = [(receipt['sha256'], receipt['samples'], pause)
              for receipt, (_, pause) in zip(receipts, takes, strict=True)]
    edits = {}
    source = rendered_pcm
    pcm_input = PCM_INPUT
    if job.get('mastering_contract', 1) >= 2:
        margins = {}
        saved = cache.get('edges') if cache.get('edge_meter') == continuity.EDGE_METER else None
        for (path, _), receipt in zip(takes, receipts, strict=True):
            sha = receipt['sha256']
            if sha in margins:
                continue
            margin = saved.get(sha) if isinstance(saved, dict) else None
            margins[sha] = margin if continuity.valid_edges(margin, receipt['samples']) else continuity.observe_edges(path, receipt['samples'])
        updated.update(edge_meter=continuity.EDGE_METER, edges=margins)
        edits = continuity.plan(job, receipts, diagnostics['takes'], margins)
        edit_identity = continuity.identity(receipts, units, edits)
        diagnostics['continuity'] = {'recipe': continuity.RECIPE, 'identity': edit_identity, 'edits': edits}
        if edits:
            stream = edit_identity
            pcm_input = FLOAT_INPUT

            def source(takes):
                return continuity.render(takes, units, edits)

    identity = key({'meter': METER, 'ffmpeg': run(['ffmpeg', '-version']).stdout.decode(errors='replace'),
                    'rate': RATE, 'recipe': BOOK, 'stream': stream})
    reading = cache.get('program')
    reused = (cache.get('program_key') == identity and isinstance(reading, dict)
              and set(reading) == {'integrated_lufs', 'true_peak_dbtp'} and all(map(_valid_db, reading.values())))
    if not reused:
        if any(cache.get(field) != value for field, value in updated.items()):
            writejson(cache_path, updated)
        reading = meter(chunks=source(takes), pcm_input=pcm_input)
    updated.update(program_key=identity, program=reading)
    if updated != cache:
        writejson(cache_path, updated)
    return reading, diagnostics, reused
