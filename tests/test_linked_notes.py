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


def test_epub_resolves_cross_resource_notes_and_keeps_original_fragments(tmp_path):
    from test_upstream_epub import book as epub_book

    source = tmp_path/'linked.epub'
    source.write_bytes(epub_book({
        'chapter':'<p>At dawn, the harbor was quiet.<sup><a epub:type="noteref" href="notes.xhtml#tide">1</a></sup></p>',
        'notes':'<aside epub:type="footnote" id="tide">The early tide was low.<a epub:type="noteref" href="extra.xhtml#wind">2</a></aside>',
        'extra':'<aside epub:type="endnote" id="wind">A warm wind came in from the west.</aside>',
    },['chapter']))
    document = inspect_source(source)
    outcome = plan(document)
    assert not outcome['issues']
    assert [unit['text'] for unit in outcome['utterances']]==[EXPECTED]
    body,tide,wind = document.blocks
    assert body.links[0]['destinations']==['source.html#'+tide.id,'source.html#'+wind.id]
    assert tide.links[0]['destinations']==['source.html#'+wind.id]
    assert (wind.locator['path'],wind.locator['anchor'])==('extra.xhtml','wind')


@pytest.mark.parametrize('route',['html','epub'])
def test_dpub_endnote_collections_bind_list_items_without_reading_backlinks(tmp_path,route):
    from test_upstream_epub import book as epub_book

    body = '<p>The harbor was quiet.<a role="doc-noteref" id="ref1" href="#tide">1</a></p>'
    notes = ('<section role="doc-endnotes"><ol><li id="tide"><p>The tide was low.'
             '<a role="doc-backlink" href="#ref1">Return</a></p></li></ol>'
             '<section><div role="list"><div role="listitem" id="next">A second note.</div></div></section>'
             '</section>')
    source = tmp_path/f'book.{route}'
    if route=='epub':
        source.write_bytes(epub_book({'chapter':body+notes},['chapter']))
    else:
        source.write_text(body+notes,encoding='utf-8')
    doc = inspect_source(source)
    result = plan(doc)
    assert not result['issues']
    assert [unit['text'] for unit in result['utterances']]==[
        'The harbor was quiet. Note. The tide was low. End note.']
    reading = next(block for block in doc.blocks if block.role=='body')
    assert reading.links[0]['reading']=='The tide was low.'
    linked = next(block for block in doc.blocks if block.role=='note' and block.locator.get('anchor')=='tide')
    assert 'source.html#'+linked.id in reading.links[0]['destinations']
    if route=='epub':
        with ZipFile(source) as package:
            assert b'<li id="tide">' in package.read(linked.locator['path'])
    else:
        assert '<li id="tide">' in linked.locator['exact']


def test_epub_does_not_pick_between_duplicate_note_ids(tmp_path):
    from test_upstream_epub import book as epub_book

    source = tmp_path/'ambiguous.epub'
    source.write_bytes(epub_book({
        'chapter':'<p>The harbor was quiet.<a epub:type="noteref" href="notes.xhtml#tide">1</a></p>',
        'notes':'<aside id="tide">Uncertain reading.</aside><aside id="tide">Conflicting reading.</aside>',
    },['chapter']))
    outcome = plan(inspect_source(source))
    assert len(outcome['issues'])==1
    assert 'cited note is unavailable' in outcome['issues'][0]['reason']


def test_rendered_mdbook_uses_exact_main_and_note_targets(tmp_path):
    source = tmp_path/'rendered.html'
    source.write_text('''<html><body><p>Keyboard shortcuts.</p><svg><path/></svg>
      <div id="mdbook-content" class="content"><main>
      <p>At dawn, the harbor was quiet.<sup class="footnote-reference" id="fr-tide-1"><a href="#footnote-tide">1</a></sup></p>
      <ol class="footnote-definition">
      <li id="footnote-tide"><p>The early tide was low.<sup class="footnote-reference" id="fr-wind-1"><a href="#footnote-wind">2</a></sup> <a href="#fr-tide-1">↩</a></p></li>
      <li id="footnote-wind"><p>A warm wind came in from the west. <a href="#fr-wind-1">↩</a></p></li>
      </ol>
      </main></div></body></html>''')
    document = inspect_source(source)
    outcome = plan(document)
    assert not outcome['issues']
    assert [unit['text'] for unit in outcome['utterances']]==[EXPECTED]
    assert 'footnote-reference' in document.blocks[0].locator['exact']
    assert '↩' in document.blocks[-1].locator['exact']
    source.write_text('<p>Unclassified count<sup>2</sup> remains.</p>')
    assert 'Styled superscript' in plan(inspect_source(source))['issues'][0]['reason']


@pytest.mark.parametrize('suffix',['.md','.html','.epub'])
def test_table_notes_use_cell_owners_and_read_nested_note_once(tmp_path,suffix):
    from test_upstream_epub import book as epub_book

    md = '''| Station | Status |
| --- | --- |
| Harbor[^tide] | Quiet[^wind] |

[^tide]: The tide was low.[^wind]
[^wind]: A warm wind came from the west.
'''
    markup = ('<table><tr><th>Station</th><th>Status</th></tr>'
              '<tr><td>Harbor<a role="doc-noteref" href="#tide">1</a></td>'
              '<td>Quiet<a role="doc-noteref" href="#wind">2</a></td></tr></table>'
              '<aside role="doc-footnote" id="tide">The tide was low.<a role="doc-noteref" href="#wind">2</a></aside>'
              '<aside role="doc-footnote" id="wind">A warm wind came from the west.</aside>')
    path = tmp_path/('table'+suffix)
    path.write_bytes(epub_book({'chapter':markup},['chapter']) if suffix=='.epub'
                    else (md if suffix=='.md' else markup).encode())
    document = inspect_source(path)
    outcome = plan(document)
    assert not outcome['issues']
    assert [unit['text'] for unit in outcome['utterances']]==[
        'Table. Station: Harbor; Status: Quiet. Note. The tide was low. End note. Note. A warm wind came from the west. End note.']
    cell_one,cell_two = document.blocks[0].locator['table_cell_semantics']
    assert [(cell['row'],cell['column']) for cell in (cell_one,cell_two)]==[(1,0),(1,1)]
    assert all(len(cell['notes'])==1 for cell in (cell_one,cell_two))


def test_missing_table_note_only_holds_a_spoken_note(tmp_path):
    path = tmp_path/'table.md'
    path.write_text('| Station | Status |\n| --- | --- |\n| Harbor[^missing] | Quiet |\n')
    document = inspect_source(path)
    assert plan(document)['issues']
    companion = plan(document,{'notes':'companion'})
    assert not companion['issues']
    assert 'Harbor; Status: Quiet.' in companion['utterances'][0]['text']
