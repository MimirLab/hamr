from zipfile import ZipFile

import pytest

from audiobook.importers import markdown
from audiobook.intake import inspect_source
from audiobook.planner import plan


NESTED = '''At dawn, the harbor was quiet.[^tide]

[^tide]: The early tide was low.[^wind]
[^wind]: A warm wind came in from the west.
'''
HTML = '''<p>At dawn, the harbor was quiet.<a role="doc-noteref" href="#tide">1</a></p>
<aside role="doc-footnote" id="tide">The early tide was low.<a role="doc-noteref" href="#wind">2</a></aside>
<aside role="doc-footnote" id="wind">A warm wind came in from the west.</aside>'''
EXPECTED = 'At dawn, the harbor was quiet. Note. The early tide was low. A warm wind came in from the west. End note.'


def book(root, text):
    chapter = root/'src/harbor.md'
    chapter.parent.mkdir(parents=True)
    (chapter.parent/'SUMMARY.md').write_text('# Summary\n\n- [Harbor](harbor.md)\n')
    chapter.write_text(text)
    return root


@pytest.mark.parametrize('route',['markdown','textpack','mdbook','html'])
def test_linked_notes_keep_text_and_destinations_across_supported_readers(tmp_path,route):
    if route=='mdbook':
        source = book(tmp_path/'book',NESTED)
    elif route=='textpack':
        source = tmp_path/'book.textpack'
        with ZipFile(source,'w') as archive:
            archive.writestr('info.json','{"version":1}')
            archive.writestr('text.md',NESTED)
    else:
        source = tmp_path/('book.html' if route=='html' else 'book.md')
        source.write_text(HTML if route=='html' else NESTED)
    document = inspect_source(source)
    result = plan(document)
    assert not result['issues']
    assert [unit['text'] for unit in result['utterances']]==[EXPECTED]
    body,tide,wind = document.blocks
    assert body.links[0]['destination']=='source.html#'+tide.id
    assert body.links[0]['destinations']==['source.html#'+tide.id,'source.html#'+wind.id]
    assert tide.links[0]['destinations']==['source.html#'+wind.id]
    assert 'A warm wind came in from the west.' in wind.locator['exact']


def test_dialect_uses_authoring_engine_precedence_and_keeps_conflicting_alternative(tmp_path):
    raw = 'The harbor was busy.[^tide]\n\n[^tide]: The tide was low.\n\n[^tide]: The tide was high.\n'
    standalone = plan(markdown(raw.encode()))
    assert 'conflicting definitions' in standalone['issues'][0]['reason']
    document = inspect_source(book(tmp_path/'book',raw))
    result = plan(document)
    assert not result['issues']
    assert [unit['text'] for unit in result['utterances']]==['The harbor was busy. Note. The tide was low. End note.']
    body,first,alternative = document.blocks
    assert body.links[0]['destination']=='source.html#'+first.id
    assert body.links[0]['destinations']==['source.html#'+first.id,'source.html#'+alternative.id]
    assert alternative.locator['exact']=='[^tide]: The tide was high.\n'


def test_identical_definitions_and_cyclic_references_read_once():
    raw = '''The harbor was quiet.[^tide]

[^tide]: The tide was low.[^wind]
[^wind]: The wind was warm.[^tide]

[^tide]: The tide was low.[^wind]
'''
    document = markdown(raw.encode())
    result = plan(document)
    assert not result['issues']
    assert [unit['text'] for unit in result['utterances']]==[
        'The harbor was quiet. Note. The tide was low. The wind was warm. End note.']
    assert len(document.blocks[0].links[0]['destinations'])==3


def test_note_indirection_consumes_no_pause_when_elsewhere_in_same_paragraph():
    raw = '''The harbor was quiet.[^alias][^tide]

[^alias]: [^tide]
[^tide]: The tide was low.
'''
    document = markdown(raw.encode())
    result = plan(document)
    assert not result['issues']
    assert [unit['text'] for unit in result['utterances']]==['The harbor was quiet. Note. The tide was low. End note.']
    assert document.blocks[0].links[0]['reference_only']


def test_missing_nested_note_is_reviewable_or_remains_in_companion():
    raw = 'The harbor was quiet.[^tide]\n\n[^tide]: The tide was low.[^unknown]\n'
    document = markdown(raw.encode())
    result = plan(document)
    assert 'markdown-note:unknown' in result['issues'][0]['reason']
    companion = plan(document,policy={'notes':'companion'})
    assert not companion['issues']
    assert [unit['text'] for unit in companion['utterances']]==[
        'The harbor was quiet. A note is available in the companion.']
