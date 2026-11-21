"""Render matched Breeze voice controls and a blind listening sheet, with local receipts."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import random
import threading
import time
import wave

from audiobook.backend import Engine
from audiobook.cast import resolve_voice
from audiobook.core import RATE, Store, filehash, key, loadjson, writejson

CASES = {
    'field-notes': ('nigel-thornberry', 'A blackbird landed on the fence and waited. For a while, the whole garden seemed to be listening.'),
    'precise-reading': ('cave-johnson', 'Doctor Reed recorded fifty-seven readings. At zero point zero eight, stop the test. Do not open the valve until the pressure falls below eight.'),
    'continuous-narration': ('garak', 'I saw the lights of the village long before I reached the bridge. The keeper used to leave a lamp in the window, even at noon, because afternoon rain made the path almost impossible to see. On the other side of the river, a row of houses leaned into the hill. The baker was bringing in the board from the pavement. A woman in a red scarf stood beside him with a letter in her hand. She watched me cross the last few yards and then folded the letter carefully, as though the paper might break. I thought she meant to pass it to me. But she put it back in her pocket and asked whether I had eaten. We went inside, where it was warm, and she set a bowl of soup in front of me. Only then did I notice the second place at the table. From the hall behind us came the slow ticking of a clock, followed by the sound of a chair being drawn back.'),
}
VRAM = sorted(Path('/sys/class/drm').glob('card*/device/mem_info_vram_used'))


def memory_sample(pid):
    result = {}
    if pid:
        try:
            for line in Path(f'/proc/{pid}/status').read_text().splitlines():
                if line.startswith(('VmRSS:', 'VmHWM:')):
                    name, value, _ = line.split()
                    result[name.rstrip(':')] = int(value) * 1024
        except OSError:
            pass
    for device in VRAM:
        try:
            result[f'{device.parts[-3]}_vram'] = int(device.read_text())
        except (OSError, ValueError):
            pass
    return result


def compare(root, settings, seeds):
    store = Store(root / 'state')
    rows = {}
    idle_vram = memory_sample(None)
    for path in settings:
        candidate = loadjson(path)
        voice = {role: resolve_voice(store, 'cast:' + item[0]) for role, item in CASES.items()}
        planned = []
        for case, (character, text) in CASES.items():
            for seed in seeds if case != 'continuous-narration' else seeds[:1]:
                binding = key([candidate, character, voice[case]['reference_sha256'], voice[case]['transcript'], text, seed])
                base = root / 'takes' / binding
                metadata = base.with_suffix('.json')
                wav = base.with_suffix('.wav')
                if metadata.is_file() and wav.is_file():
                    row = loadjson(metadata)
                    if row.get('wav_sha256') == filehash(wav):
                        rows.setdefault((case, seed), []).append((wav, row))
                        continue
                planned.append((case, character, text, seed, metadata, wav))
        if not planned:
            continue
        peaks = {}
        startup_seconds = None
        phase = 'startup'
        engine = Engine(store, candidate)
        def sampler():
            while not stopped.is_set():
                values = memory_sample(engine.process.pid if engine.process else None)
                bucket = peaks.setdefault(phase, {})
                for name, size in values.items():
                    bucket[name] = max(bucket.get(name, 0), size)
                stopped.wait(.2)
        stopped = threading.Event()
        thread = threading.Thread(target=sampler, daemon=True)
        baseline = memory_sample(None)
        deadline = time.monotonic() + 15
        while (any(baseline.get(name, 0) - value > 512 * 1024**2 for name, value in idle_vram.items())
               and time.monotonic() < deadline):
            time.sleep(.25)
            baseline = memory_sample(None)
        profile = candidate['profile']
        print(f'Starting {profile}', flush=True)
        started = time.monotonic()
        thread.start()
        try:
            with engine:
                startup_seconds = time.monotonic() - started
                phase = 'generation'
                for case, character, text, seed, metadata, wav in planned:
                    print(f'{profile}: {case}, seed {seed}', flush=True)
                    started = time.monotonic()
                    audio, receipt = engine.speech(text, voice[case], seed=seed)
                    elapsed = time.monotonic() - started
                    wav.parent.mkdir(exist_ok=True)
                    partial = wav.with_suffix('.wav.part')
                    with wave.open(str(partial), 'wb') as f:
                        f.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
                        f.writeframes(audio)
                    partial.replace(wav)
                    row = {'profile': profile, 'runtime_revision': candidate['runtime_revision'],
                           'engine_sha256': candidate['binary_sha256'], 'model_sha256': candidate['model_sha256'],
                           'codec': 'Qwen audio codec embedded in identified GGUF',
                           'actual_device': receipt['actual_device'], 'voice': character,
                           'reference_sha256': voice[case]['reference_sha256'],
                           'reference_text_sha256': hashlib.sha256(voice[case]['transcript'].encode()).hexdigest(),
                           'case': case, 'seed': seed, 'text': text, 'wav_sha256': filehash(wav),
                           'request_seconds': elapsed, 'audio_seconds': receipt['seconds']}
                    writejson(metadata, row)
                    rows.setdefault((case, seed), []).append((wav, row))
        finally:
            stopped.set()
            thread.join()
            writejson(root / f'{profile}-memory.json',
                      {'sampling_interval_seconds': .2, 'desktop_baseline': baseline, 'peaks_bytes': peaks,
                       'startup_wall_seconds': startup_seconds,
                       'scope': 'AMD sysfs totals include other display clients; process RSS excludes device memory.'})
    return rows


def make_review(root, rows):
    playlist = root / 'blind'
    playlist.mkdir(exist_ok=True)
    mapping = {}
    sections = []
    for index, ((case, seed), tracks) in enumerate(sorted(rows.items()), 1):
        if len(tracks) < 2:
            continue
        tracks = list(tracks)
        random.Random(key([case, seed, 'review-order'])).shuffle(tracks)
        items = []
        for letter, (source, row) in zip('ABC', tracks):
            name = f'{index:02d}-{letter}.wav'
            target = playlist / name
            if not target.is_file() or filehash(target) != row['wav_sha256']:
                partial = target.with_suffix('.new')
                partial.unlink(missing_ok=True)
                partial.hardlink_to(source)
                partial.replace(target)
            mapping[name] = row
            items.append(f'<label>{letter} <audio controls preload="none" src="blind/{name}"></audio></label>')
        text = html.escape(tracks[0][1]['text'])
        sections.append(f'<section><h2>{index}. {html.escape(case.replace("-", " "))}</h2><p>{text}</p>{"".join(items)}</section>')
    writejson(root / 'answer-key.json', mapping)
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Breeze listening comparison</title><style>body{font:17px/1.5 system-ui;max-width:46rem;margin:2rem auto;padding:0 1rem}'
            'section{border-top:1px solid #888;margin-top:2rem}label{display:block;margin:1rem 0}audio{width:100%}</style>'
            '<h1>Breeze listening comparison</h1><p>Compare the clips in each group for missing or extra words, likeness, expression and clarity. '
            'On the long passage, listen for changes near the end. Use headphones if you can.</p>')
    (root / 'listen.html').write_text(page + ''.join(sections), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('settings', nargs='+', type=Path, help='Two or three configured settings.json files from isolated setups')
    args = parser.parse_args()
    if not 2 <= len(args.settings) <= 3:
        parser.error('Pass two or three settings files.')
    args.output.mkdir(parents=True, exist_ok=True)
    with Store(args.output / 'state').lock('comparison'):
        make_review(args.output, compare(args.output, args.settings, [42, 43]))
    print(args.output / 'listen.html')


if __name__ == '__main__':
    main()
