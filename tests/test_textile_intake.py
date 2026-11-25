from pathlib import Path
from zipfile import ZipFile

from PIL import Image
import pytest

from audiobook.core import Error
from audiobook.intake import inspect_saved, inspect_source
from audiobook.planner import plan
from audiobook.source_bundle import capture


def speech(document):
    return ' '.join(unit['text'] for unit in plan(document)['utterances'])


def test_textile_uses_shared_notes_tables_code_and_default_delivery(tmp_path):
    source = tmp_path/'River.textile'
    source.write_text('''h1. The River Gate

"The gate is open," she whispered.[1]

"Keep still," he whispered.

At dawn the watch changed.[#weather]

notelist.

|=. Hourly log. |
|_. Hour |_. Event |
| Dawn | The gate opened. |

bc.. printf 'hello <world>'

printf 'another line'

p. The light went out.

The rabbits left the garden.[2!]

fn1. The keeper heard every word.

fn2. This note has no linked callout.

note#weather. Fog rose over the river.
''')
    inspected = inspect_source(source,offline=True)
    actual = plan(inspected)
    assert actual['plan_sha256']==plan(inspect_source(source,offline=True))['plan_sha256']
    assert not actual['issues']
    said = speech(inspected)
    table = 'Table. Hour: Dawn; Event: The gate opened.'
    assert said.index('Hourly log.') < said.index(table)
    assert '“The gate is open,” she whispered. Note. The keeper heard every word. End note.' in said
    assert 'At dawn the watch changed. Note. Fog rose over the river. End note.' in said
    code, = [block for block in inspected.blocks if block.role=='code']
    assert code.source=="printf 'hello <world>'\n\nprintf 'another line'"
    assert 'printf' not in said and 'notelist.' not in said
    assert 'The rabbits left the garden.' in said and 'no linked callout' not in said
    assert any('Whisper' in option.get('instruction','') for unit in actual['utterances']
               if (option:=unit.get('performance_candidate')))
    note_blocks = {block.id for block in inspected.blocks if block.role=='note'}
    links = [link for block in inspected.blocks for link in block.links if link['kind']=='note']
    assert len(links)==2 and all(link['destinations'][0].split('#')[1] in note_blocks for link in links)


def test_textile_claims_only_its_authored_illustrations_and_recovers_them(tmp_path):
    root = tmp_path/'Edition'
    parts = root/'text'
    images = root/'assets'
    parts.mkdir(parents=True)
    images.mkdir()
    source = parts/'book.textile'
    source.write_text('h1. The River Gate\n\n!../assets/harbor%20signal.png(A light shines.)!\n')
    image = images/'harbor signal.png'
    Image.new('L',(3,3),128).save(image)
    archive = tmp_path/'Edition.zip'
    pairs = {'text/book.textile':source.read_bytes(),'assets/harbor signal.png':image.read_bytes()}
    with ZipFile(archive,'w') as package:
        for name,raw in pairs.items():
            package.writestr('Edition/'+name,raw)
    for path in (root,archive):
        document = inspect_source(path,offline=True)
        assert document.format=='textile'
        assert speech(document)=='The River Gate A light shines.'
        assert any(link['kind']=='source_resource' for block in document.blocks for link in block.links)
    scoped = inspect_source(source,offline=True)
    assert not scoped.metadata.get('source_resources')
    area = tmp_path/'job'
    area.mkdir()
    saved,tracked = capture(root,area,offline=True)
    with ZipFile(saved) as package:
        assert {name:package.read(name) for name in package.namelist()}==pairs
    (root/'independent.png').write_bytes(image.read_bytes())
    with pytest.raises(Error) as error:
        inspect_source(root,offline=True)
    assert error.value.code=='source_selection'
    assert [item['id'] for item in error.value.candidates]==['pages','text/book.textile']
    source.unlink()
    image.unlink()
    assert speech(inspect_saved(saved,tracked))=='The River Gate A light shines.'


def test_textile_retains_badge_rows_and_preserves_other_image_review(tmp_path):
    source = tmp_path/'manual.textile'
    source.write_text('''h1. The River Gate

!https://img.shields.io/project.svg! !https://github.com/example/project/actions/workflows/tests.yml/badge.svg!

The gate opened at dawn.

<img src="decoration.png" alt="">

!https://example.invalid/river.png!
''')
    document = inspect_source(source,offline=True)
    badges, = [item for item in document.inventory if item['kind']=='project_badges']
    assert badges['status']=='companion' and len(badges['images'])==2
    issue, = plan(document)['issues']
    assert 'authored accessible description' in issue['reason']
    assert 'The gate opened at dawn.' in speech(document)
