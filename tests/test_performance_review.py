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
