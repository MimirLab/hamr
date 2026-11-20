import json
from pathlib import Path
import wave

import pytest

from audiobook import cast, core
from audiobook.cli import main
from audiobook.core import RATE, Store, filehash, loadjson, writejson


@pytest.fixture
def character_assets(tmp_path, monkeypatch):
    root = tmp_path / 'package'
    root.mkdir()
    catalog = root / 'cast.json'
    entries = [
        {'id': 'nigel-thornberry', 'name': 'Nigel Thornberry', 'description': 'Expressive English.', 'language': 'en'},
        {'id': 'alba', 'name': 'Alba', 'description': 'A second voice.', 'language': 'en'},
    ]
    writejson(catalog, {'schema_version': 1, 'characters': entries})
    bundle = root / 'cast_assets'
    monkeypatch.setattr(cast, 'CATALOG', catalog)
    monkeypatch.setattr(cast, 'ASSETS', bundle)
    for entry in entries:
        assets = bundle / entry['id']
        assets.mkdir(parents=True)
        for label, length in (('reference', 3), ('sample', 1)):
            with wave.open(str(assets / (label + '.wav')), 'wb') as recording:
                recording.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
                recording.writeframes(b'\x00\x08' * RATE * length)
        writejson(assets / 'voice.json', {
            'reference_sha256': filehash(assets / 'reference.wav'),
            'sample_sha256': filehash(assets / 'sample.wav'),
            'transcript': 'This is the reference narration.',
        })
    return bundle


def call(home, capsys, *args, expected=0):
    assert main(['--home', str(home), *args]) == expected
    return json.loads(capsys.readouterr().out)


def test_browse_audition_select_and_queue(character_assets, tmp_path, monkeypatch, capsys):
    home = tmp_path / 'fresh state'
    source = tmp_path / 'document.md'
    source.write_text('Here is a complete source passage.\n')

    result = call(home, capsys, 'voice', 'cast', '--json')
    assert [entry['id'] for entry in result['characters']] == ['alba', 'nigel-thornberry']
    assert all(entry['available'] for entry in result['characters'])
    assert result['default_voice'] is None
    seen = []
    monkeypatch.setattr(core, 'run', lambda args: seen.append(args))
    result = call(home, capsys, 'voice', 'sample', 'nigel-thornberry', '--play', '--json')
    assert result['seconds'] == 1
    assert seen == [['mpv', '--no-video', result['sample']]]

    selection = call(home, capsys, 'voice', 'select', 'nigel-thornberry', '--json')
    assert selection['default_voice'] == 'cast:nigel-thornberry'
    queued = call(home, capsys, 'queue', str(source), '--json')
    j = Store(home).get(queued['job'])
    ref = home / 'voices' / '.cast'
    assert j['voice']['reference'].startswith(str(ref) + '/')
    assert j['voice']['reference_sha256'] == filehash(character_assets / 'nigel-thornberry/reference.wav')
    (character_assets / 'nigel-thornberry/reference.wav').unlink()
    call(home, capsys, 'voice', 'select', 'alba', '--json')
    assert Store(home).get(queued['job']) == j
    assert filehash(Path(j['voice']['reference'])) == j['voice']['reference_sha256']


def test_per_book_choice_and_saved_voice_keep_distinct_ids(character_assets, tmp_path, capsys):
    home = tmp_path / 'state'
    store = Store(home)
    source = tmp_path / 'document.md'
    source.write_text('Here is a complete source passage.\n')
    local = {'name': 'nigel-thornberry', 'origin': 'clone', 'language': 'en'}
    writejson(home / 'voices/nigel-thornberry/voice.json', local)

    queued = call(home, capsys, 'queue', str(source), '--voice', 'nigel-thornberry', '--json')
    assert store.get(queued['job'])['voice'] == local
    result = call(home, capsys, 'repair', queued['job'], '--voice', 'cast:nigel-thornberry', '--json')
    assert result['revision'] == 2
    assert store.get(queued['job'])['voice']['name'] == 'Nigel Thornberry'
    assert call(home, capsys, 'voice', 'cast', '--json')['default_voice'] is None


def test_corrupt_sample_and_missing_assets_fail_before_use(character_assets, tmp_path, monkeypatch, capsys):
    home = tmp_path / 'state'
    (character_assets / 'nigel-thornberry/sample.wav').write_bytes(b'wrong')
    seen = []
    monkeypatch.setattr(core, 'run', lambda args: seen.append(args))
    result = call(home, capsys, 'voice', 'sample', 'nigel-thornberry', '--play', '--json', expected=2)
    assert result['error']['code'] == 'cast_asset_changed'
    assert not seen

    (character_assets / 'nigel-thornberry/voice.json').unlink()
    result = call(home, capsys, 'voice', 'select', 'nigel-thornberry', '--json', expected=2)
    assert result['error']['code'] == 'cast_assets_missing'
    assert not (home / 'preferences.json').exists()


def test_reference_entry_plays_selects_and_queues_without_audition(character_assets, tmp_path, capsys):
    catalog = loadjson(cast.CATALOG)
    catalog['characters'][1]['sample_kind'] = 'reference'
    writejson(cast.CATALOG, catalog)
    (character_assets / 'alba/sample.wav').unlink()
    home = tmp_path / 'state'
    call(home, capsys, 'voice', 'select', 'nigel-thornberry', '--json')
    library = call(home, capsys, 'voice', 'cast', '--json')
    alba = next(entry for entry in library['characters'] if entry['id'] == 'alba')
    assert alba['sample_available'] and alba['available']
    sample = call(home, capsys, 'voice', 'sample', 'alba', '--json')
    assert sample['sample_kind'] == 'reference'
    assert Path(sample['sample']) == character_assets / 'alba/reference.wav'
    assert sample['seconds'] == 3
    selected = call(home, capsys, 'voice', 'select', 'alba', '--json')
    assert selected['default_voice'] == 'cast:alba'
    assert call(home, capsys, 'voice', 'cast', '--json')['default_voice'] == 'cast:alba'

    source = tmp_path / 'book.md'
    source.write_text('This is a complete paragraph for the queue test.\n')
    queued = call(home, capsys, 'queue', str(source), '--voice', 'cast:alba', '--json')
    pinned = Store(home).get(queued['job'])['voice']
    assert pinned['character'] == 'alba'
    assert pinned['reference_sha256'] == filehash(character_assets / 'alba/reference.wav')


def test_cast_cache_refuses_symlink(character_assets, tmp_path, capsys):
    home = tmp_path / 'state'
    Store(home)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (home / 'voices/.cast').symlink_to(outside, target_is_directory=True)
    result = call(home, capsys, 'voice', 'select', 'nigel-thornberry', '--json', expected=2)
    assert result['error']['code'] == 'unsafe_path'
    assert not list(outside.iterdir())


@pytest.fixture
def introduction_assets(character_assets, tmp_path, monkeypatch):
    previews = tmp_path / 'packaged previews'
    previews.mkdir()
    path = previews / 'nigel-thornberry.mp3'
    path.write_bytes(b'public introduction fixture, distinct from conditioning audio')
    writejson(previews / 'manifest.json', {'recordings': [
        {'character': 'nigel-thornberry', 'file': path.name, 'text': 'Come along on an extraordinary expedition!',
         'sha256': filehash(path), 'seconds': 8.5},
    ]})
    monkeypatch.setattr(cast, 'PREVIEWS', previews)
    catalog = loadjson(cast.CATALOG)
    catalog['characters'][0]['sample_kind'] = 'introduction'
    writejson(cast.CATALOG, catalog)
    return path


def test_preview_plays_introduction_and_queue_conditions_on_original_reference(introduction_assets, character_assets, tmp_path, monkeypatch, capsys):
    home = tmp_path / 'state'
    played = []
    monkeypatch.setattr(core, 'run', lambda args: played.append(args))
    sample = call(home, capsys, 'voice', 'sample', 'nigel-thornberry', '--play', '--json')
    assert sample['sample_kind'] == 'introduction'
    assert Path(sample['sample']) == introduction_assets
    assert sample['transcript'] == 'Come along on an extraordinary expedition!'
    assert played == [['mpv', '--no-video', str(introduction_assets)]]
    library = call(home, capsys, 'voice', 'cast', '--json')
    nigel = next(c for c in library['characters'] if c['id'] == 'nigel-thornberry')
    assert nigel['sample_kind'] == 'introduction' and nigel['sample_available']
    call(home, capsys, 'voice', 'select', 'nigel-thornberry', '--json')
    source = tmp_path / 'book.md'; source.write_text('An ordinary paragraph for a new book.\n')
    job = call(home, capsys, 'queue', str(source), '--json')
    voice = Store(home).get(job['job'])['voice']
    assert Path(voice['reference']).suffix == '.wav'
    assert voice['reference_sha256'] == filehash(character_assets / 'nigel-thornberry/reference.wav')
    assert voice['transcript'] == 'This is the reference narration.'
    assert voice['seconds'] == 3


@pytest.mark.parametrize('fault,code', [('missing', 'cast_sample_missing'), ('changed', 'cast_asset_changed'), ('traversal', 'cast_sample_metadata')])
def test_invalid_introduction_fails_before_playback(introduction_assets, tmp_path, monkeypatch, capsys, fault, code):
    if fault == 'missing': introduction_assets.unlink()
    elif fault == 'changed': introduction_assets.write_bytes(b'changed')
    else:
        manifest = loadjson(cast.PREVIEWS / 'manifest.json')
        manifest['recordings'][0]['file'] = '../../outside.mp3'
        writejson(cast.PREVIEWS / 'manifest.json', manifest)
    played = []
    monkeypatch.setattr(core, 'run', lambda args: played.append(args))
    result = call(tmp_path / 'state', capsys, 'voice', 'sample', 'nigel-thornberry', '--play', '--json', expected=2)
    assert result['error']['code'] == code
    assert played == []


def test_enabling_introductions_preserves_existing_conditioning_identity(introduction_assets, tmp_path):
    catalog = loadjson(cast.CATALOG)
    catalog['characters'][0]['sample_kind'] = 'reference'
    writejson(cast.CATALOG, catalog)
    store = Store(tmp_path / 'state')
    before = cast.resolve_voice(store, 'cast:nigel-thornberry')
    catalog['preview_kind'] = 'introduction'
    writejson(cast.CATALOG, catalog)
    assert cast.sample('nigel-thornberry')['sample_kind'] == 'introduction'
    assert cast.resolve_voice(store, 'cast:nigel-thornberry') == before
