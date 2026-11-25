from pathlib import Path
from zipfile import ZipFile

from bs4 import BeautifulSoup
import pytest

from audiobook.core import Error
from audiobook.intake import inspect_source
from audiobook.jobs import render
from audiobook.planner import plan


def source(tmp_path,body):
    path = tmp_path/'edition.rtf'
    path.write_bytes(b'{\\rtf1\\ansi\\ansicpg1252 '+body+b'}')
    return path


def test_saved_list_markers_use_their_declared_rtf_kind_and_source_span(tmp_path):
    source = tmp_path/'numbers.rtf'
    raw = (
        br'{\rtf1\ansi{\*\listtable'
        br'{\list{\listlevel\levelnfc1\levelstartat3}\listid4}'
        br'{\list{\listlevel\levelnfc23}\listid5}}'
        br'{\*\listoverridetable{\listoverride\listid4\listoverridecount0\ls7}'
        br'{\listoverride\listid5\listoverridecount0\ls8}}'
        br'\pard{\listtext III.\tab}\ilvl0\ls7 III. Open the gate.\par'
        br'\pard Stay near the gate.\par'
        br'\pard{\listtext IV.\tab}\ilvl0\ls7 Check the lamp.\par'
        br'\pard{\listtext \bullet\tab}\ilvl0\ls8 Park the cart.\par'
        br'\pard{\pntext 8.\tab}Watch the road.\par'
        br'\pard\ilvl0\ls7 Inspect the latch.\par}'
    )
    source.write_bytes(raw)
    document = inspect_source(source)
    result = plan(document)
    assert [unit['text'] for unit in result['utterances']]==[
        'Number three, Open the gate.','Stay near the gate.','Number four, Check the lamp.',
        'Park the cart.','Number eight, Watch the road.']
    labels = [marker for row in result['decisions'] for marker in row.get('source_numbering',())]
    assert [marker['authored_marker'] for marker in labels]==['III.','IV.','8.']
    assert labels[0]['source']=='III. '
    first,last = labels[0]['marker_location']['byte_range']
    assert raw[first:last]==br'\listtext III.\tab}'
    assert len(result['issues'])==1 and result['issues'][0]['block']==document.blocks[-1].id


def test_rtf_reads_outline_inheritance_and_an_explicit_body_override(tmp_path):
    raw = br'''{\stylesheet{\s1\outlinelevel0 Heading;}{\s2\sbasedon1 Custom;}
{\*\cs4\outlinelevel1 Inline styling;}{\s3\sbasedon4 Cycle;}{\s4\sbasedon3 Cycle;}}
{\pard\s2 Before dawn}\par
\pard\s1\outlinelevel9 The keeper crossed at dawn.\par
{\*\unrecognized {\footnote Do not read this.}Ignore this control.}
\pard\s3 The gates opened.\par
\pard\outlinelevel0 At noon\par
\pard The ferry left.\line The fog lifted.\par'''
    result = plan(inspect_source(source(tmp_path,raw)))
    assert not result['issues']
    assert result['chapters']==['Before dawn','At noon']
    assert [unit['text'] for unit in result['utterances']]==[
        'Before dawn','The keeper crossed at dawn.','The gates opened.',
        'At noon','The ferry left.\nThe fog lifted.']


def test_rtf_notes_keep_exact_anchors_and_visible_unicode(tmp_path):
    raw = (br'\pard The caf\uc1\u233\'3f had closed\chftn{\*\footnote'
           br' \chftn It shut just after dawn\par The keeper still had the key.}.'
           br'\par The ferry left\chftn{\footnote\ftnalt\chftn The tide was rising.}.\par'
           br'{\footer Page furniture{\footnote An ignored footer note.}}')
    path = source(tmp_path,raw)
    doc = inspect_source(path)
    result = plan(doc)
    assert not result['issues']
    assert [unit['text'] for unit in result['utterances']]==[
        'The café had closed. Note. It shut just after dawn. The keeper still had the key. End note.',
        'The ferry left. Note. The tide was rising. End note.']
    notes = [block for block in doc.blocks if block.role=='note']
    assert [block.locator['note_kind'] for block in notes]==['footnote','endnote']
    for note in notes:
        start,end = note.locator['byte_range']
        assert path.read_bytes()[start:end].startswith(br'\footnote')
    body = [block for block in doc.blocks if block.role=='body']
    assert all(block.links[0]['destination']=='source.html#'+note.id for block,note in zip(body,notes,strict=True))
    first, = body[0].locator['inline_semantics']
    assert first['source_start']==first['source_end']==len('The café had closed')
    companion = plan(doc,{'notes':'companion'})
    assert 'End note.' not in ' '.join(unit['text'] for unit in companion['utterances'])


def test_rtf_holds_missing_note_and_preserves_known_cell_reading(tmp_path):
    raw = (br'\pard The lost record\chftn is not available.\par'
           br'\pard The other page survived.\par'
           br'\trowd\cellx500\cellx1000\intbl Gate\chftn'
           br'{\*\footnote\chftn The gate was checked.}\cell Count\cell\row\pard')
    doc = inspect_source(source(tmp_path,raw))
    p = plan(doc)
    issue, = p['issues']
    assert 'note' in issue['reason'].lower()
    assert [u['text'] for u in p['utterances']][0]=='The other page survived.'
    table, = [block for block in doc.blocks if block.role=='table']
    entry, = table.locator['table_cell_semantics']
    assert (entry['row'],entry['column'],entry['source'])==(0,0,'Gate')
    assert entry['notes'][0]['reading']=='The gate was checked.'
    assert 'The gate was checked.' in ' '.join(unit['text'] for unit in p['utterances'])


def test_rtf_ignores_binary_delimiters_and_resets_fallback_at_the_group(tmp_path):
    raw = br'\uc2\u233 \bin3 {\}Z\par{\uc1\u241} page\par'
    result = plan(inspect_source(source(tmp_path,raw)))
    assert [row['text'] for row in result['utterances']]==['é ñ page']
    with pytest.raises(Error,match='binary object is truncated'):
        inspect_source(source(tmp_path,br' text\bin50 a'))


def test_hidden_paragraph_mark_separates_visible_text(tmp_path):
    raw = br'C {\v\par}{D}{\v\par}{\v Private words\par}{E}\par'
    doc = inspect_source(source(tmp_path,raw))
    assert [block.source for block in doc.blocks]==['C','D','E']


def test_rtf_final_revisions_keep_insertions_and_remove_deleted_breaks(tmp_path):
    raw = (br'{\revtbl{Unknown;}{Editor;}}'
           br'A {\revised warm}{\deleted cold{ \plain removed}} wind lifted.\par '
           br'The road was {\deleted\line}quiet as dawn broke.\par '
           br'The trail led {\deleted\par}over the hill.\par '
           br'{\v\par}The keeper waited. {\deleted wrong\deleted0 Then she left.}\par')
    doc = inspect_source(source(tmp_path,raw))
    assert [block.source for block in doc.blocks]==[
        'A warm wind lifted.','The road was quiet as dawn broke.',
        'The trail led over the hill.','The keeper waited. Then she left.']
    assert not plan(doc)['issues']


def test_rtf_final_revisions_keep_only_current_notes_and_table_words(tmp_path):
    raw = (br'The caf{\deleted\uc2\u97??}\uc1\u233? had closed'
           br'{\deleted\chftn{\footnote\chftn An old note.}{\pict picture data}}'
           br'{\revised\chftn{\footnote\chftn The keeper {\deleted lost}{\revised kept} the key.}}.\par'
           br'\trowd\cellx500\cellx1000\intbl \deleted North\cell\deleted0 South\cell\row\pard')
    doc = inspect_source(source(tmp_path,raw))
    result = plan(doc)
    assert not result['issues']
    body,table,note = doc.blocks
    assert body.source=='The café had closed.'
    assert body.links[0]['destination']=='source.html#'+note.id
    assert note.source=='The keeper kept the key.'
    assert table.locator['rows']==[['','South']]
    assert all('North' not in utterance['text'] for utterance in result['utterances'])


def test_rtf_final_revisions_keep_move_destinations(tmp_path):
    raw = (br'Before {\*\mvfmf Move1 0100768baa46}{\mvf\deleted the light}{\*\mvfml Move1}'
           br'the gate opened.\par Then {\*\mvtof Move1 0100768baa46}{\mvt\revised the light}'
           br'{\*\mvtol Move1} came on.\par')
    doc = inspect_source(source(tmp_path,raw))
    assert [block.source for block in doc.blocks]==['Before the gate opened.','Then the light came on.']
    assert not plan(doc)['issues']


def test_rtf_reads_author_text_shape_but_keeps_shape_properties_out(tmp_path):
    raw = (br'\pard Before dawn.\par {\shp{\*\shpinst\shpleft0\shpright4000'
           br'{\sp{\sn displayName}{\sv Internal shape name}}'
           br'{\shptxt \pard The frame has text\chftn'
           br'{\*\footnote\chftn It stays with its anchor.}.}}}'
           br'\pard At noon.\par')
    book = plan(inspect_source(source(tmp_path,raw)))
    assert not book['issues']
    assert [p['text'] for p in book['utterances']]==[
        'Before dawn.','The frame has text. Note. It stays with its anchor. End note.','At noon.']


def test_rtf_uses_declared_font_encodings_in_body_and_notes(tmp_path):
    raw = (br'{\fonttbl{\f0\fcharset0 Main;}{\f1\fcharset204 Cyrillic;}'
           br'{\f2\fcharset0\cpg1251 Explicit;}}'
           br'\pard \f0 Before.\par {\f1 '+ 'Основной текст'.encode('cp1251')+
           br'\chftn{\footnote\chftn \f2 '+'Текст сноски'.encode('cp1251')+
           br'.}.}\par\f0 After.\par')
    doc = inspect_source(source(tmp_path,raw))
    assert [block.source for block in doc.blocks]==['Before.','Основной текст.','After.','Текст сноски.']
    note = next(b for b in doc.blocks if b.role=='note')
    assert doc.blocks[1].links[0]['destination']=='source.html#'+note.id


def test_rtf_character_styles_preserve_exact_unicode_spans_and_nested_resets(tmp_path):
    raw = (br'{\stylesheet{\s5\outlinelevel0 Heading;}'
           br'{\*\cs5\i Italic;}{\*\cs6\sbasedon5\b Strong italic;}'
           br'{\*\cs7\sbasedon8\i Broken one;}{\*\cs8\sbasedon7\b Broken two;}}'
           br'\s5 Gate house\par\pard'
           br'{\cs6 Caf\'e9 \uc0\u-10179{\i0\u-8704}{\i0 upright}\plain plain}'
           br'\chftn{\footnote\chftn The {\b note} stays.}{\rtlch\ai\ltrch  Latin}.'
           br'\par{\cs8 One} {\cs7 two}.\par'
           br'\trowd\cellx500\cellx1000\intbl {\b West}\cell East\cell\row\pard')
    path = source(tmp_path,raw)
    doc = inspect_source(path)
    heading,body,cycle,table,note = doc.blocks
    assert heading.role=='heading' and heading.source=='Gate house'
    assert body.source=='Café 😀uprightplain Latin.'
    anchor,italic,bold = body.locator['inline_semantics']
    assert anchor['source_start']==anchor['source_end']==len('Café 😀uprightplain')
    assert body.links[0]['destination']=='source.html#'+note.id
    assert [(span['kind'], span['source']) for span in (italic,bold)]==[
        ('font_style','Café 😀'),('emphasis','Café 😀upright')]
    for mark,style_id,control in ((italic,5,br'\i Italic'),(bold,6,br'\b Strong')):
        origin = mark['style_definition']
        assert origin['id']==style_id
        assert path.read_bytes()[origin['byte']:].startswith(control)
    assert [(s['kind'],s['source']) for s in cycle.locator['inline_semantics']]==[
        ('font_style','two'),('emphasis','One')]
    assert note.locator['inline_semantics'][0]['source']=='note'
    cell, = table.locator['table_cell_semantics']
    assert (cell['row'],cell['column'],cell['spans'][0]['source'])==(0,0,'West')


def test_rtf_and_wrapped_rtf_use_the_same_dialogue_emphasis_as_html(tmp_path):
    raw = (br'\u8220?Did {\rtlch\ai\ltrch\hich\ai\dbch\ai\loch\i y}{\i ou}'
           br' see who left?\u8221? the doctor asked.\par')
    path = source(tmp_path,raw)
    package = tmp_path/'book.zip'
    with ZipFile(package,'w') as archive:
        archive.write(path,'Manuscript/edition.rtf')
    html = tmp_path/'edition.html'
    html.write_text('<p>“Did <em>you</em> see who left?” the doctor asked.</p>',encoding='utf-8')
    for variant in (path,package,html):
        doc = inspect_source(variant)
        utterance, = plan(doc)['utterances']
        assert utterance['text']=='“Did you see who left?” the doctor asked.'
        candidate = utterance['performance_candidate']
        assert candidate['recipe']=='contrastive_emphasis'
        assert candidate['evidence'][0]['target_span']==[5,8]


def test_scrivener_rebinds_each_note_to_its_own_companion_block(tmp_path):
    from test_media import prepared
    from test_scrivener_intake import body,document,item,write

    book = tmp_path/'Harbor.scriv'
    write(book,'Harbor.scrivx',document(item('Text',1,'First')+item('Text',2,'Second')))
    raw = br'{\rtf1\ansi A {\i gate}\chftn{\*\footnote\chftn The gate was checked.} opened.\par}'
    write(book,body(1),raw)
    write(book,body(2),raw)
    package = tmp_path/'export.zip'
    with ZipFile(package,'w') as z:
        for p in book.rglob('*'):
            if p.is_file():
                z.write(p,book.name+'/'+p.relative_to(book).as_posix())
    doc = inspect_source(package)
    paragraphs = [block for block in doc.blocks if block.role=='body']
    notes = [block for block in doc.blocks if block.role=='note']
    assert [block.locator['inline_semantics'][1]['source'] for block in paragraphs]==['gate','gate']
    assert [block.links[0]['destination'] for block in paragraphs]==['source.html#'+block.id for block in notes]
    assert len({block.id for block in notes})==2
    store,job = prepared(tmp_path,package)
    output = Path(render(store,job['id'],allow_synthesis=False)['output'])
    page = BeautifulSoup((output/'source.html').read_text(),'html.parser')
    for block in notes:
        assert page.find(id=block.id)
        assert page.find('a',href='#'+block.id) or page.find('a',href='source.html#'+block.id)
