import json
import wave

import pytest

from tools.performance_review import Inbox, render


def pack(tmp_path, name, *, single=False):
    directory = tmp_path / name
    directory.mkdir()
    takes = {'sample': 'sample.wav'} if single else {'A': 'a.wav', 'B': 'b.wav'}
    for index, filename in enumerate(takes.values()):
        with wave.open(str(directory / filename), 'wb') as out:
            out.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            out.writeframes(bytes((index, 0)) * 240)
    entry = {'id': 'opening', 'title': name, 'prompt': 'Listen to the transition.', 'words': 'The door closed.', **takes}
    (directory / 'listening-pack.json').write_text(json.dumps({'id': name, 'pairs': [entry]}))
    return directory


def test_inbox_keeps_picks_until_processed_and_retains_history(tmp_path):
    first = pack(tmp_path, 'first')
    second = pack(tmp_path, 'second', single=True)
    (first / 'listening-picks.json').write_text('{"opening": "B"}')
    inbox = Inbox(tmp_path / 'inbox')
    inbox.add(first)
    inbox.add(second)
    assert inbox.snapshot()[2] == {'first:opening': 'B'}
    assert inbox.record({'first:opening': 'A', 'second:opening': 'flag'}) is None
    inbox.add(first)
    restored = Inbox(tmp_path / 'inbox')
    page, media, picks = restored.snapshot()
    assert [item['id'] for item in page['pairs']] == ['first:opening', 'second:opening']
    assert picks == {'first:opening': 'A', 'second:opening': 'flag'}
    assert len(media) == 2 and all(path.is_file() for path in media.values())
    html = render(page, picks, inbox=True)
    assert all(f'id="state-{key}">Pick saved · awaiting follow-up' in html for key in picks)

    with pytest.raises(ValueError, match='No open entry matches'):
        restored.complete('first:opening', 'B', 'Stale result')
    restored.complete('first:opening', 'A', 'Used the preferred take')
    restored.add(first)
    page, _, picks = restored.snapshot()
    assert [item['id'] for item in page['pairs']] == ['second:opening'] and picks == {'second:opening': 'flag'}
    assert restored.record({'first:opening': 'B'}) == 409
    assert restored.record({'second:opening': []}) == 400
    assert restored.entries(completed=True)[0]['resolution'] == 'Used the preferred take'


def test_feedback_migrates_preserves_and_checks_readback(tmp_path):
    import sqlite3

    directory = tmp_path / 'inbox'
    directory.mkdir()
    with sqlite3.connect(directory / 'reviews.sqlite3') as db:
        db.execute('CREATE TABLE entries (id TEXT PRIMARY KEY, sample TEXT NOT NULL, source TEXT NOT NULL,'
                   ' pick TEXT, resolution TEXT)')
        db.execute('INSERT INTO entries VALUES (?, ?, ?, ?, ?)', ('earlier:opening',
                   '{"id":"earlier:opening","A":"/audio/a.wav","B":"/audio/b.wav"}',
                   'private', 'B', 'Already applied'))
    inbox = Inbox(directory)
    first, second = pack(tmp_path, 'first'), pack(tmp_path, 'second')
    (first / 'listening-comments.json').write_text('{"opening":"Imported setup"}')
    inbox.add(first)
    inbox.add(second)
    earlier = inbox.entries(completed=True)[0]
    assert earlier['pick'] == 'B' and earlier['comment'] == '' and earlier['resolution'] == 'Already applied'

    key = 'first:opening'
    note = '00:01\nThe entrance is loud. </script>'
    assert inbox.record({key: 'A'}, comments={key: note}) is None
    assert inbox.record({key: 'B'}, comments={'unknown': 'Should fail'}) == 400
    assert inbox.record({}, comments={key: 'too long ' * 1000}) == 400
    assert inbox.record({}, comments={key: 'Different'}, imported=True) == 409
    page, _, picks, comments = Inbox(directory).snapshot(include_comments=True)
    assert picks[key] == 'A' and comments[key] == note
    html = render(page, picks, comments, inbox=True)
    assert 'Pick saved · Comment saved' in html
    assert f'id="comment-{key}"' in html
    assert r'\u003c/script>' in html
    assert 'The entrance is loud. </script>' not in html

    with pytest.raises(ValueError, match='No open entry matches'):
        inbox.complete(key, 'A', 'Stale note', expected_comment='An old comment')
    inbox.complete(key, 'A', 'Kept the quieter entrance', expected_comment=note)
    assert inbox.record({}, comments={key: ''}) == 409
    assert inbox.entries(completed=True)[1]['comment'] == note

    only_comment = '00:03 the speaker changes register'
    assert inbox.record({}, comments={'second:opening': only_comment}) is None
    inbox.complete('second:opening', None, 'Investigated the change', expected_comment=only_comment)
    assert inbox.entries() == []


def test_review_displays_variant_notes_and_requires_a_description_for_each_option(tmp_path):
    from tools.performance_review import load_pack

    directory = pack(tmp_path, 'unblinded')
    path = directory / 'listening-pack.json'
    data = json.loads(path.read_text())
    data['pairs'][0]['variant_notes'] = {'A': 'Original', 'B': 'Opening <2 dB quieter'}
    path.write_text(json.dumps(data))
    bank, _ = load_pack(directory)
    html = render(bank, {})
    assert 'A · Original' in html
    assert 'B · Opening &lt;2 dB quieter' in html

    del data['pairs'][0]['variant_notes']['A']
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='describe every audio option'):
        load_pack(directory)
