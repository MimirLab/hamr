"""Audition alternate settings against the same saved passage."""
from contextlib import ExitStack
from io import BytesIO
import re
import wave

from . import performance
from .backend import Engine, validate_request
from .core import Error, RATE, atomic, digest, filehash, key, loadjson, writejson


VARIANT_FIELDS = {'seed', 'instruction', 'markers', 'cfg_scale', 'frame_guidance', *performance.SAMPLING_LIMITS}


def read_controls(path):
    try:
        return loadjson(path)
    except (OSError, ValueError) as exc:
        raise Error('preview_compare_file', f'Could not read the comparison JSON at {path}.',
                    'Supply a readable JSON object mapping take names to their settings.') from exc


def requests(unit, voice, backend, controls):
    if not isinstance(controls, dict) or not controls:
        raise Error('preview_compare', 'Supply a JSON object with at least one named take.')
    saved_text, saved_options = performance.request(unit)
    parallel = backend['settings'].get('parallel', 1)
    for name, controls_for_take in controls.items():
        if not name or not name.isprintable() or not isinstance(controls_for_take, dict):
            raise Error('preview_compare', 'Each take needs a printable name and a settings object.')
        if controls_for_take.keys() - VARIANT_FIELDS:
            unknown = sorted(controls_for_take.keys() - VARIANT_FIELDS)
            raise Error('preview_compare', f'{name}: unknown comparison setting {", ".join(unknown)}.')
        options = {**saved_options}
        seed = controls_for_take.get('seed', options['seed'])
        if type(seed) is not int or not -(2**31) <= seed < 2**31:
            raise Error('preview_compare_seed', f'{name}: choose a signed 32-bit integer seed.')
        options['seed'] = seed
        instruction = controls_for_take.get('instruction', options.get('instruction'))
        if instruction is not None and (not isinstance(instruction, str) or not instruction.strip()):
            raise Error('preview_compare_instruction', f'{name}: use nonempty instruction text or null.')
        options['instruction'] = instruction
        markers = controls_for_take.get('markers', True)
        if type(markers) is not bool:
            raise Error('preview_compare_markers', f'{name}: markers must be true or false.')
        text = saved_text if markers else unit['text']
        saved_schedule = options.pop('frame_guidance', None)
        schedule = controls_for_take.get('frame_guidance', saved_schedule)
        if schedule is not None:
            if 'cfg_scale' in controls_for_take:
                raise Error('frame_guidance', 'Set base_scale on the preview schedule.')
            options['frame_guidance'] = performance.validated_frame_guidance(schedule)
            options['cfg_scale'] = options['frame_guidance']['base_scale']
        else:
            cfg_scale = controls_for_take.get('cfg_scale', 1 if saved_schedule else options.get('cfg_scale', 1))
            performance.validate_guidance(cfg_scale, parallel)
            options['cfg_scale'] = float(cfg_scale)
        sampling = performance.validated_sampling({
            **options.get('sampling', {}),
            **{field: controls_for_take[field] for field in performance.SAMPLING_LIMITS
               if field in controls_for_take},
        })
        if sampling:
            options['sampling'] = sampling
        validate_request(text, voice, instruction)
        yield name, text, options


def saved_take(root, record, request_key):
    if record.is_symlink():
        return None
    try:
        envelope = loadjson(record)
        take = envelope['take']
        audio_hash = take['audio_sha256']
        if (envelope['sha256'] != key([request_key, take])
                or not re.fullmatch(r'[0-9a-f]{64}', audio_hash)):
            return None
        audio = root / f'{audio_hash}.wav'
        if take['file'] == audio.name and not audio.is_symlink() and filehash(audio) == audio_hash:
            return take
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


def audition(store, job, unit, controls):
    """Save checked transport receipts and preview WAVs outside the book."""
    voice, backend = job['voice'], job['backend']
    cases = list(requests(unit, voice, backend, controls))
    root = store.path(job) / 'auditions'
    if root.is_symlink() or root.is_junction():
        raise Error('unsafe_path', 'The comparison directory cannot be a symlink or junction.')
    root.mkdir(mode=0o700, exist_ok=True)
    takes, rendered = [], {}
    reused_takes = 0
    with ExitStack() as stack:
        engine = None
        for name, text, options in cases:
            request_key = key([backend, voice, text, options])
            if request_key not in rendered:
                record = root / f'{request_key}.take.json'
                take = saved_take(root, record, request_key)
                if take:
                    reused_takes += 1
                else:
                    if engine is None:
                        engine = stack.enter_context(Engine(store, backend))
                    data, receipt = engine.speech(text, voice, **options)
                    buffer = BytesIO()
                    with wave.open(buffer, 'wb') as output:
                        output.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
                        output.writeframes(data)
                    audio = buffer.getvalue()
                    audio_hash = digest(audio)
                    filename = f'{audio_hash}.wav'
                    atomic(root / filename, audio)
                    take = {'file': filename, 'audio_sha256': audio_hash, 'receipt': receipt}
                    writejson(record, {'take': take, 'sha256': key([request_key, take])})
                rendered[request_key] = take
            takes.append({'name': name, **rendered[request_key]})
    report = {
        'schema_version': 1, 'kind': 'preview_comparison', 'ready': False,
        'job': job['id'], 'revision': job['revision'], 'utterance': unit['id'],
        'plan_sha256': job['plan']['plan_sha256'], 'source_sha256': job['document']['source_sha256'],
        'expected_words': unit['text'], 'voice': voice['name'],
        'sources': [b['locator'] for b in job['document']['blocks'] if b['id'] in unit['sources']],
        'backend': {field: backend.get(field) for field in
                    ('profile', 'runtime_revision', 'binary_sha256', 'model_sha256')},
        'takes': takes,
    }
    manifest = root / f'{key(report)}.json'
    writejson(manifest, report)
    return {'schema_version': 1, 'kind': 'preview_comparison', 'ready': False, 'manifest': str(manifest),
            'new_takes': len(rendered) - reused_takes, 'reused_takes': reused_takes,
            'takes': [{'name': take['name'], 'audio': str(root / take['file']),
                       'seconds': take['receipt']['seconds'], 'audio_sha256': take['audio_sha256']}
                      for take in takes]}
