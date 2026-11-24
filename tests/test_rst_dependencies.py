import io
import shutil
import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest

from audiobook.intake import extract_location, inspect_saved, inspect_source
from audiobook.jobs import render
from audiobook.source_bundle import capture


@pytest.fixture
def manuscript(tmp_path):
    book = tmp_path/'input'
    parts = book/'parts'
    parts.mkdir(parents=True)
    root = book/'Harbor log.rst'
    root.write_text('Harbor Log\n==========\n\nThe bell rang.\n\n.. include:: parts/arrival.inc\n\nIt grew quiet.\n')
    (parts/'arrival.inc').write_text('A ferry arrived.\n\n.. include:: weather.inc\n   :start-line: 1\n   :end-line: 2\n')
    (parts/'weather.inc').write_bytes(b'Before.\r\nFog covered the shore.\r\nAfter.\r\n')
    return root


def test_loose_snapshot_and_companion_bind_included_bytes(tmp_path,manuscript):
    from test_media import prepared

    (manuscript.parent/'unused.txt').write_text('This file belongs to another book.')
    weather = manuscript.parent/'parts/weather.inc'
    original = weather.read_bytes()
    preview = inspect_source(manuscript)
    assert [b.source for b in preview.blocks]==[
        'Harbor Log','The bell rang.','A ferry arrived.','Fog covered the shore.','It grew quiet.']
    where = preview.blocks[3].locator
    assert where['member']=='parts/weather.inc' and where['lines']==[2,2]
    store,job = prepared(tmp_path,manuscript)
    snapshot = Path(job['snapshot'])
    with ZipFile(snapshot) as archive:
        assert archive.namelist()==['Harbor log.rst','parts/arrival.inc','parts/weather.inc']
    weather.write_text('A changed working copy.')
    saved = inspect_saved(snapshot,job['source_selection'])
    assert saved.json()==preview.json()
    result = render(store,job['id'],allow_synthesis=False)
    moved = tmp_path/'moved'
    shutil.move(result['output'],moved)
    extracted = tmp_path/'weather.inc'
    extract_location(moved,where['location_id'],extracted)
    assert extracted.read_bytes()==original


@pytest.mark.parametrize('kind',['zip','tar'])
def test_raw_archive_resolves_from_member_and_keeps_selected_book(tmp_path,kind):
    source = tmp_path/('books.'+kind)
    files = {
        'other.txt':'Separate book.\n',
        'log/chapter.rst':'The bell rang.\n\n.. include:: ../shared/voices.inc\n',
        'shared/voices.inc':'Someone answered.\n',
    }
    if kind=='zip':
        with ZipFile(source,'w') as archive:
            for name,data in files.items():
                archive.writestr(name,data)
    else:
        with tarfile.open(source,'w') as archive:
            for name,data in files.items():
                raw = data.encode()
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                archive.addfile(member,io.BytesIO(raw))
    doc = inspect_source(source,entry='2')
    assert [b.source for b in doc.blocks]==['The bell rang.','Someone answered.']
    assert [b.locator['archive_entry'] for b in doc.blocks]==[2,3]
    assert [b.locator['member'] for b in doc.blocks]==['log/chapter.rst','shared/voices.inc']


def test_holds_use_directive_line_and_keep_unrelated_text_out(tmp_path):
    folder = tmp_path/'book'
    folder.mkdir()
    (tmp_path/'private.inc').write_text('Outside the selected source.')
    root = folder/'main.rst'
    root.write_text('First.\n\n.. include:: part.inc\n\n.. include:: ../private.inc\n\nLast.\n')
    (folder/'part.inc').write_text('Included.\n\n.. include:: empty.inc\n\n.. include:: main.rst\n')
    doc = inspect_source(root)
    issues = [(b.locator['member'],b.locator['lines'][0],b.issue) for b in doc.blocks if b.issue]
    assert [row[:2] for row in issues]==[('part.inc',3),('part.inc',5),('main.rst',5)]
    assert 'missing' in issues[0][2]
    assert 'Circular' in issues[1][2]
    assert 'leaves the source' in issues[2][2]
    assert [b.source for b in doc.blocks if not b.issue]==['First.','Included.','Last.']


def test_literal_and_lexical_includes_do_not_read_files(tmp_path):
    source = tmp_path/'guide.rst'
    source.write_text('Read this example::\n\n   .. include:: private.inc\n\n'
                      '.. A comment with .. include:: private.inc\n')
    (tmp_path/'private.inc').write_text('Private example.\n')
    target = tmp_path/'snapshot'
    target.mkdir()
    path,selection = capture(source,target)
    assert path.read_bytes()==source.read_bytes() and selection.kind=='file'


def test_includes_inherit_the_legacy_encoding_and_can_override_it(tmp_path):
    source = tmp_path/'guide.rst'
    source.write_bytes('Départ.\n\n.. include:: inherit.inc\n\n.. include:: utf.inc\n   :encoding: utf-8\n'.encode('cp1252'))
    (tmp_path/'inherit.inc').write_bytes('André arrived.\n'.encode('cp1252'))
    (tmp_path/'utf.inc').write_text('Łódź came into view.\n')
    doc = inspect_source(source,encoding='windows-1252')
    assert [b.source for b in doc.blocks]==['Départ.','André arrived.','Łódź came into view.']
