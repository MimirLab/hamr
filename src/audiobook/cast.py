"""Built-in character presets and durable copies of their voice references."""
from io import BytesIO
from pathlib import Path
import re
import wave

from .core import Error, RATE, atomic, digest, filehash, key, loadjson, writejson

CATALOG = Path(__file__).with_name('cast.json')
ASSETS = Path(__file__).with_name('cast_assets')
PREFIX = 'cast:'
MAX_AUDIO_BYTES = RATE * 2 * 60 + 4096


def characters():
    return loadjson(CATALOG)['characters']


def character(identifier):
    if re.fullmatch(r'[a-z0-9-]{1,64}', identifier):
        for entry in characters():
            if entry['id'] == identifier:
                return entry
    raise Error('unknown_character', 'No built-in character has that ID.',
                'Run hamr voice cast to see the available characters.')


def describe(entry):
    root = ASSETS / entry['id']
    has_metadata = (root / 'voice.json').is_file()
    has_reference = (root / 'reference.wav').is_file()
    sample_kind = entry.get('sample_kind', 'audition')
    sample_file = 'reference.wav' if sample_kind == 'reference' else 'sample.wav'
    sample_available = has_metadata and (root / sample_file).is_file()
    return {
        **entry,
        'voice': PREFIX + entry['id'],
        'sample_kind': sample_kind,
        'sample_available': sample_available,
        'available': has_metadata and has_reference,
    }


def preference(store):
    path = store.root / 'preferences.json'
    return loadjson(path) if path.exists() else {}


def library(store):
    selected = preference(store).get('voice')
    return {
        'schema_version': 1,
        'default_voice': selected,
        'characters': sorted((describe(entry) for entry in characters()), key=lambda entry: entry['name'].casefold()),
    }


def assets(entry):
    root = ASSETS / entry['id']
    try:
        return root, loadjson(root / 'voice.json')
    except FileNotFoundError as exc:
        raise Error('cast_assets_missing', f"The installed build does not include audio for {entry['name']}.",
                    'Install a build that includes this character.') from exc


def audio(path, expected, minimum, maximum):
    try:
        with path.open('rb') as source:
            data = source.read(MAX_AUDIO_BYTES + 1)
    except FileNotFoundError as exc:
        raise Error('cast_assets_missing', f'The installed build is missing {path.name}.',
                    'Install a build that includes this character.') from exc
    if len(data) > MAX_AUDIO_BYTES or digest(data) != expected:
        raise Error('cast_asset_changed', f'The packaged character file {path.name} failed its checksum.')
    try:
        with wave.open(BytesIO(data), 'rb') as wav:
            frames = wav.getnframes()
            seconds = frames / wav.getframerate()
            if ((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, RATE)
                    and minimum <= seconds <= maximum and len(wav.readframes(frames)) == frames * 2):
                return data, seconds
    except (EOFError, wave.Error):
        pass
    raise Error('cast_audio_format', f'The packaged character file {path.name} has an invalid format.')


def sample(identifier):
    entry = character(identifier)
    root, metadata = assets(entry)
    kind = entry.get('sample_kind', 'audition')
    if kind == 'reference':
        path = root / 'reference.wav'
        _, seconds = audio(path, metadata['reference_sha256'], 2, 30)
    else:
        path = root / 'sample.wav'
        _, seconds = audio(path, metadata['sample_sha256'], 0.2, 60)
    return {'schema_version': 1, 'character': identifier, 'name': entry['name'],
            'sample': str(path), 'seconds': seconds, 'sample_kind': kind}


def resolve_voice(store, name):
    if not name.startswith(PREFIX):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
            raise Error('voice_name', 'Use a saved voice name or cast:CHARACTER.')
        return loadjson(store.root / 'voices' / name / 'voice.json')

    entry = character(name.removeprefix(PREFIX))
    root, metadata = assets(entry)
    transcript = metadata['transcript'].strip()
    if not transcript or len(transcript.encode()) > 1024:
        raise Error('cast_transcript', 'A packaged voice needs a nonempty, bounded reference transcript.')
    expected = metadata['reference_sha256']
    data, seconds = audio(root / 'reference.wav', expected, 2, 30)
    identity = key({'entry': entry, 'reference_sha256': expected, 'transcript': transcript})

    # The pinned data lives outside the installed package so upgrades can retain old takes.
    cache = store.root / 'voices' / '.cast'
    target = cache / identity
    for directory in (cache, target):
        if directory.is_symlink():
            raise Error('unsafe_path', 'The character voice cache cannot be a symlink.')
        directory.mkdir(exist_ok=True, mode=0o700)
    reference = target / 'reference.wav'
    if reference.is_symlink():
        raise Error('unsafe_path', 'The character reference cannot be a symlink.')
    if not reference.is_file() or filehash(reference) != expected:
        atomic(reference, data)
    return {
        'name': entry['name'], 'origin': 'clone', 'character': entry['id'],
        'language': entry['language'], 'description': entry['description'],
        'reference': str(reference), 'reference_sha256': expected,
        'original_reference': str(reference), 'original_reference_sha256': expected,
        'transcript': transcript, 'seconds': seconds,
    }


def select(store, identifier):
    name = PREFIX + identifier
    with store.lock():
        voice = resolve_voice(store, name)
        writejson(store.root / 'preferences.json', {**preference(store), 'voice': name})
    return {'schema_version': 1, 'default_voice': name, 'name': voice['name']}
