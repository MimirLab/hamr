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


def test_rails_guide_uses_shared_literal_and_ruby_semantics(tmp_path):
    folder = tmp_path/'Guide'
    folder.mkdir()
    source = folder/'guide.textile'
    source.write_text('''h2. The Harbor Guide

The river was still.

endprologue.

h3. View templates

TIP: Save the view before you reload it.

<erb>
<!DOCTYPE html>
<html lang="fr">
<head><title>A sample only</title></head>
<body>

<%= 2 <plus> 3 %>
</body>
</html>
</erb>

<ruby>
puts "AN EXAMPLE"
</ruby>

Use +status_code+ to check the response.

Use <tt>RAILS_ENV=production</tt> for this run.

|_.File |_.Purpose|
|gate.rb | Opens the gate.|

Ask in the "support channel":irc://irc.example.invalid/harbor.

<code class="html">
<h1>Watch the harbor</h1>
</code>

<ruby>
gate<rt role="definition">entrance</rt>
</ruby>

h3. At the gate

"Quiet now," she whispered.
''')
    archive = tmp_path/'Guide.zip'
    with ZipFile(archive,'w') as package:
        package.write(source,'Guide/guide.textile')
    documents = [inspect_source(path,offline=True) for path in (source,folder,archive)]
    assert speech(documents[0])==speech(documents[1])==speech(documents[2])
    for document in documents:
        spoken = speech(document)
        assert document.title=='The Harbor Guide'
        assert document.language!='fr'
        assert 'The river was still.' in spoken and 'Save the view before you reload it.' in spoken
        assert 'File: gate.rb; Purpose: Opens the gate.' in spoken
        assert 'Ask in the support channel.' in spoken and 'irc.example.invalid' not in spoken
        assert 'A sample only' not in spoken and 'endprologue' not in spoken
        blocks = [block.source for block in document.blocks if block.role=='code']
        assert len(blocks)==3
        assert '<body>\n\n<%= 2 + 3 %>' in blocks[0]
        assert blocks[1:] == ['puts "AN EXAMPLE"','<h1>Watch the harbor</h1>']
        spans = [span for block in document.blocks for span in block.locator.get('inline_semantics',())]
        assert any(span['kind']=='literal' and span['source']=='status_code' for span in spans)
        assert any(span['kind']=='literal' and span['source']=='RAILS_ENV=production' for span in spans)
        assert any(span['kind']=='ruby' and span['annotation']=='entrance' for span in spans)
        narration = plan(document)
        assert not narration['issues']
        assert any('Whisper' in option.get('instruction','') for unit in narration['utterances']
                   if (option:=unit.get('performance_candidate')))


def test_rails_guide_missing_example_end_is_localized(tmp_path):
    source = tmp_path/'unfinished.textile'
    source.write_text('h2. Guide\n\nendprologue.\n\nh3. Start\n\n<erb>\n<p>Example!</p>\n')
    with pytest.raises(Error) as report:
        inspect_source(source,offline=True)
    assert report.value.code=='source_textile'
    assert '<erb> example at line 7' in str(report.value)


def test_embedded_html_body_does_not_replace_its_source_fragment(tmp_path):
    source = tmp_path/'Fragment.html'
    source.write_text('''<h1>Harbor Watch</h1>
<p>The lookout spoke first.</p>
<aside><html lang="fr"><head><title>Sample Metadata</title></head><body></body></html></aside>
<p>The lookout spoke again.</p>''')
    fragment = inspect_source(source,offline=True)
    assert fragment.title=='Fragment' and fragment.language!='fr'
    assert speech(fragment)=='Harbor Watch The lookout spoke first. The lookout spoke again.'
    source.write_text('''<html lang="en-CA"><head><title>Document title</title></head>
<body><p>The real document survived.</p></body></html>''')
    full = inspect_source(source,offline=True)
    assert (full.title,full.language)==('Document title','en-CA')
    assert speech(full)=='The real document survived.'
