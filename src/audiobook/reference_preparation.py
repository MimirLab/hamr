"""Prepare a voice reference once. Books consume only the selected PCM and transcript."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import wave

import numpy as np

from .core import Error, RATE, atomic, digest, fault, filehash, key, loadjson, run, writejson
from . import wash_setup

SOURCE_RATE = 48000
CANONICALIZATION = 'ffmpeg-s16le-24khz-v1'


def convert(source, target, *, channel=None):
    filters = [] if channel is None else ['-af', f'pan=mono|c0=c{channel-1}']
    run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', source,
         '-map', '0:a:0', '-t', '31', *filters, '-ar', RATE, '-ac', '1', '-c:a', 'pcm_s16le', target], timeout=90)


def read_pcm(path):
    with wave.open(str(path), 'rb') as reader:
        if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, RATE):
            raise Error('reference_format', 'Breeze references require 24 kHz mono PCM16.')
        frames = reader.getnframes()
        data = reader.readframes(frames)
        if len(data) != frames * 2:
            raise Error('reference_format', 'Reference audio is truncated.')
    return np.frombuffer(data, dtype='<i2').astype(np.float32) / 32768


def properties(path):
    result = run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-select_streams', 'a:0',
                  '-show_entries', 'stream=codec_name,sample_rate,channels,channel_layout',
                  '-of', 'json', path], timeout=30)
    streams = json.loads(result.stdout).get('streams', [])
    if not streams:
        raise Error('reference_format', 'The reference has no readable audio stream.')
    stream = streams[0]
    return {'codec': stream.get('codec_name'), 'sample_rate': int(stream.get('sample_rate', 0)),
            'channels': int(stream.get('channels', 0)), 'layout': stream.get('channel_layout')}


def diagnostics(pcm):
    if not 2 * RATE <= len(pcm) <= 30 * RATE:
        raise Error('reference_duration', 'Reference must be 2–30 seconds of clean speech.')
    ac = pcm - np.mean(pcm)
    level = float(np.sqrt(np.mean(ac ** 2)))
    if level < .0003:
        raise Error('reference_silence', 'The reference has no usable audio after channel conversion.',
                    'Select one audible channel with --channel, or use a clean source excerpt.')
    size = 1024
    frames = ac[:len(ac)//size*size].reshape(-1, size)
    power = np.mean(np.abs(np.fft.rfft(frames * np.hanning(size))) ** 2, axis=0)
    bandwidth = np.searchsorted(np.cumsum(power), .995 * np.sum(power)) * RATE / size
    return {'samples': len(pcm), 'rms': level, 'dc': float(np.mean(pcm)),
            'occupied_bandwidth_estimate_hz': round(float(bandwidth)),
            'clipped_fraction': float(np.mean(np.abs(pcm) >= .999))}


def electrical_hum(pcm):
    """Look for a stable mains fundamental and harmonic during quiet parts of speech."""
    size = RATE // 2
    chunks = pcm[:len(pcm)//size*size].reshape(-1, size)
    levels = np.sqrt(np.mean(chunks ** 2, axis=1))
    quiet = (levels <= np.quantile(levels, .4)) & (levels <= .5 * np.quantile(levels, .9))
    if np.sum(quiet) < 3:
        return None
    spectrum = np.fft.rfft(chunks[quiet] * np.hanning(size)) / (size / 4)
    signal = np.abs(spectrum).mean(axis=0)
    consistent = np.abs(spectrum.mean(axis=0))
    for frequency in (50, 60):
        first, harmonic = frequency // 2, frequency
        peaks = consistent[[first, harmonic]]
        neighbors = np.maximum(signal[[first-2, harmonic-2]], signal[[first+2, harmonic+2]])
        if ((peaks > [.007, .003]) & (peaks > [6, 2] * neighbors)
                & (peaks > [.85, .75] * signal[[first, harmonic]])).all():
            return frequency, quiet, float(np.linalg.norm(peaks))
    return None


def hum_reduced(pcm, evidence):
    frequency, quiet, before = evidence
    size = RATE // 2
    chunks = pcm[:len(pcm)//size*size].reshape(-1, size)
    local = np.fft.rfft(chunks[quiet] * np.hanning(size)) / (size / 4)
    return np.linalg.norm(local.mean(axis=0)[[frequency // 2, frequency]]) <= .70 * before


def _read_cache(path, identity):
    if path.is_symlink() or path.is_junction():
        raise Error('unsafe_path', 'Prepared voice cache must use an owned directory.')
    try:
        record = path/'receipt.json'
        if record.is_symlink():
            raise Error('unsafe_path', 'Prepared voice receipts must be local regular files.')
        receipt = loadjson(record)
        candidate = path/'reference.wav'
        native = path/'native.f32le'
        if (receipt['identity'] == identity and not candidate.is_symlink()
                and not native.is_symlink() and native.stat().st_size <= 4 * 31 * SOURCE_RATE
                and filehash(native) == receipt['native_pcm_sha256']
                and candidate.stat().st_size < 2 * 31 * RATE + 4096
                and filehash(candidate) == receipt['candidate_sha256']):
            return candidate, receipt
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def candidate(store, source, channel, source_sha, profile, recipe, scratch):
    from .jobs import rename_new

    worker_sha = filehash(wash_setup.WORKER)
    identity = {'source_sha256': source_sha, 'channel': channel, 'recipe': profile,
                'model_sha256': recipe['model_sha256'], 'environment_sha256': recipe['lock_sha256'],
                'worker_sha256': worker_sha, 'canonicalization': CANONICALIZATION,
                'ffmpeg_version': digest(run(['ffmpeg', '-version'], timeout=20).stdout)}
    cache = store.root/'assets'/'voice-wash'
    if cache.is_symlink() or cache.is_junction():
        raise Error('unsafe_path', 'Prepared voice cache must use an owned directory.')
    cache = cache/'prepared'
    if cache.is_symlink() or cache.is_junction():
        raise Error('unsafe_path', 'Prepared voice cache must use an owned directory.')
    cache.mkdir(parents=True, exist_ok=True)
    target = cache/key(identity)
    if existing := _read_cache(target, identity):
        return *existing, True
    if recipe['model'].is_symlink() or filehash(recipe['model']) != recipe['model_sha256']:
        raise Error('wash_asset', 'The installed voice preparation model differs from the pinned file.')
    filters = [] if channel is None else ['-af', f'pan=mono|c0=c{channel-1}']
    native = run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', source,
                  '-map', '0:a:0', '-t', '31', *filters, '-ar', SOURCE_RATE, '-ac', '1',
                  '-f', 'f32le', 'pipe:1'], timeout=90).stdout
    source_array, result_array = scratch/'source.npy', scratch/'result.npy'
    signal = np.frombuffer(native, dtype='<f4')
    np.save(source_array, signal, allow_pickle=False)
    report = wash_setup.execute_worker(recipe, source_array, result_array)
    if result_array.stat().st_size > len(native) + 4096:
        raise Error('wash_output', 'Preparation returned an invalid audio file size.')
    result = np.load(result_array, allow_pickle=False)
    if (result.shape != signal.shape or not np.isfinite(result).all() or report.get('samples') != len(signal)
            or report.get('sample_rate') != SOURCE_RATE or filehash(recipe['model']) != recipe['model_sha256']):
        raise Error('wash_output', 'Preparation changed sample coverage or returned invalid audio.')
    staged = scratch/'entry'
    staged.mkdir()
    reference = staged/'reference.wav'
    raw = staged/'native.f32le'
    atomic(raw, result.astype('<f4', copy=False).tobytes())
    run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'f32le', '-ar', SOURCE_RATE, '-ac', '1', '-i', raw,
         '-ar', RATE, '-ac', '1', '-c:a', 'pcm_s16le', reference], timeout=90)
    receipt = {'identity': identity, 'candidate_sha256': filehash(reference),
               'native_pcm_sha256': filehash(raw), 'worker': report}
    writejson(staged/'receipt.json', receipt)
    fault('before_voice_wash_cache')
    if target.exists():
        store.safe_remove(target)
    rename_new(staged, target)
    return target/'reference.wav', receipt, False


def verify_transcript(store, scratch, path, text):
    from . import checks_setup, qa_cache
    from .speech_compare import comparison_tokens

    python, primary, secondary = checks_setup.observer(store)
    if secondary is None:
        raise Error('wash_checkers', 'Two local speech checkers are needed to validate a prepared reference.')
    pcm = scratch/'observation.pcm'
    with wave.open(str(path), 'rb') as reader:
        seconds = reader.getnframes() / RATE
        atomic(pcm, reader.readframes(reader.getnframes()))
    clip = {'utterance': 'reference', 'path': str(pcm)}
    expected = comparison_tokens(text)
    for model in (primary, secondary):
        observation, = qa_cache.recognize(python, model, [clip])
        if comparison_tokens(qa_cache.audible_text(observation, seconds)) != expected:
            return False
    return True


def preserves(baseline, processed):
    if len(baseline) != len(processed):
        return False
    level = np.sqrt(np.mean(baseline ** 2))
    scale = np.sqrt(np.mean(processed ** 2)) / level
    agreement = float(np.mean(baseline * processed)) / (scale * level ** 2) if scale else 0
    if not (.65 <= scale <= 1.15 and agreement >= .88):
        return False
    size = RATE // 10
    starts = range(0, len(baseline), size)
    active = [start for start in starts if np.sqrt(np.mean(baseline[start:start+size]**2)) > .35 * level]
    return all(np.linalg.norm(processed[start:start+size]) >= .55 * np.linalg.norm(baseline[start:start+size])
               for start in (active[0], active[-1])) if active else False


def prepare(store, source, target, transcript, source_sha, requested, channel=None):
    info = properties(source)
    if channel is not None and not 1 <= channel <= info['channels']:
        raise Error('reference_channel', f'The reference has {info["channels"]} audio channels.')
    convert(source, target, channel=channel)
    original = read_pcm(target)
    summary = diagnostics(original)
    receipt = {'schema_version': 1, 'status': 'baseline', 'reason': 'Washing disabled.',
               'requested': requested, 'source_sha256': source_sha, 'media': info, 'channel': channel,
               'span_seconds': [0, len(original)/RATE], 'transcript_sha256': digest(transcript.encode()),
               'baseline_sha256': filehash(target), 'diagnostics': summary, 'canonicalization': CANONICALIZATION}
    if requested == 'off':
        return receipt
    hum = electrical_hum(original)
    receipt['mains_hum_hz'] = hum[0] if hum else None
    if requested == 'auto' and hum is None:
        receipt['reason'] = 'No qualified source problem was detected.'
        return receipt
    if requested != 'auto' and requested not in wash_setup.PROFILES:
        raise Error('wash_profile', f'Unknown voice preparation profile {requested!r}.',
                    'Run hamr doctor for the installed choices.')
    from .hardware import inspect_host

    host = inspect_host()
    choices = wash_setup.automatic_profiles() if requested == 'auto' else (requested,)
    for profile in choices:
        recipe = wash_setup.configured(store, profile)
        if recipe and wash_setup.compatible(profile, host):
            break
    else:
        receipt['reason'] = 'No installed qualified profile fits this host.'
        if requested != 'auto':
            from .installation import command
            raise Error('wash_unavailable', receipt['reason'],
                        command(store.root, 'setup', '--wash-only', '--wash-profile', requested))
        return receipt
    try:
        with tempfile.TemporaryDirectory(dir=target.parent, prefix='wash-') as temporary:
            scratch = Path(temporary)
            path, treatment, reused = candidate(store, source, channel, source_sha, profile, recipe, scratch)
            processed = read_pcm(path)
            result = diagnostics(processed)
            receipt.update(profile=profile, cache_reused=reused, candidate=treatment, candidate_diagnostics=result)
            if (not preserves(original, processed)
                    or (hum is not None and not hum_reduced(processed, hum))
                    or result['clipped_fraction'] > max(summary['clipped_fraction'], .001)):
                raise Error('wash_preservation', 'The prepared candidate failed the signal or interference comparison.')
            if not verify_transcript(store, scratch, path, transcript):
                raise Error('wash_transcript', 'The prepared reference did not agree with the transcript in both speech checkers.')
            atomic(target, path.read_bytes())
            receipt.update(status='prepared', reason='The selected preparation passed the local reference checks.',
                           reference_sha256=filehash(target))
    except (Error, OSError, ValueError, wave.Error) as exc:
        if requested != 'auto' or (isinstance(exc, Error) and exc.code == 'unsafe_path'):
            if isinstance(exc, Error):
                raise
            raise Error('wash_output', 'Could not validate the prepared reference.') from exc
        receipt.update(reason=exc.message if isinstance(exc, Error) else 'The candidate could not be validated.',
                       error_code=getattr(exc, 'code', 'wash_output'))
    return receipt
