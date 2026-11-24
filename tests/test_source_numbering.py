from zipfile import ZipFile

from audiobook.intake import inspect_source
from audiobook.planner import plan


ITEMS = ('The lamp stayed lit','The tide rose 3.5 feet.','The bell rang.')
EXPECTED = ['Number one, '+ITEMS[0],'Number two, The tide rose three point five feet.','Number three, '+ITEMS[2]]


def test_printed_paragraph_numbers_match_plain_markup_and_ordered_lists(tmp_path):
    sources = {
        'sequence.txt': '\n'.join(f'{i}. {text}' for i,text in enumerate(ITEMS,1)),
        'sequence.pml': '\n\n'.join(f'{i}. {text}' for i,text in enumerate(ITEMS,1)),
        'sequence.html': '<ol>'+''.join(f'<li><p>{text}</p></li>' for text in ITEMS)+'</ol>',
        'sequence.md': '\n'.join(f'1. {text}' for text in ITEMS),
    }
    for name,source in sources.items():
        path = tmp_path/name
        path.write_text(source,encoding='utf-8')
        document = inspect_source(path)
        result = plan(document)
        assert [u['text'] for u in result['utterances']]==EXPECTED, name
        original_label = next(row for row in result['decisions'] if row.get('source_numbering'))
        assert original_label['source_numbering'][0]['ordinal_values']==[1]
        assert original_label['source']==document.blocks[0].source
        if path.suffix=='.txt':
            assert original_label['source_numbering'][1]['source']=='2. '


def test_running_number_with_two_adjacent_identifiers_keeps_both(tmp_path):
    path = tmp_path/'excerpt.pml'
    path.write_text('4. The tide turned.\n\n5,6. The lights went on\n\n7. The boat arrived.',encoding='ascii')
    assert [u['text'] for u in plan(inspect_source(path))['utterances']]==[
        'Number four, The tide turned.','Numbers five and six, The lights went on','Number seven, The boat arrived.']


def test_roman_headings_convert_only_in_an_authored_sequence(tmp_path):
    path = tmp_path/'heading.html'
    path.write_text('<h1>VIII. The shore</h1><p>Body.</p><h1>IX. A new light</h1><p>Body.</p>'
                    '<h1>I. Am here</h1><p>Body.</p>',encoding='utf-8')
    document = inspect_source(path)
    result = plan(document)
    headings = [row for row in result['decisions'] if row['source']!='Body.']
    assert [row['spoken'] for row in headings]==['Eight, The shore','Nine, A new light','I. Am here']
    assert result['chapters']==['VIII. The shore','IX. A new light','I. Am here']
    assert [u['text'] for u in plan(document,rule_revision='faithful-25')['utterances']][0]=='VIII.'


def test_generated_list_ordinals_follow_saved_start_reverse_and_value(tmp_path):
    source = tmp_path/'steps.html'
    source.write_text('<ol start="8" reversed><li>The first check</li><li value="3">The next check</li>'
                      '<li>2. The final check</li></ol><p>In 1944, 5.2 feet was the reading.</p>',encoding='utf-8')
    result = plan(inspect_source(source))
    assert [u['text'] for u in result['utterances']]==[
        'Number eight, The first check','Number three, The next check','Number two, The final check',
        'In 1944, five point two feet was the reading.']


def test_roman_labels_keep_notation_and_nested_items_under_the_common_reading(tmp_path):
    source = tmp_path/'roman.html'
    source.write_text('<ol type="I" start="5" reversed><li><p>Open the gate.</p>'
                      '<ol type="i" start="2"><li>Inspect the latch.</li></ol>'
                      '<p>Watch the road.</p></li><li value="3">III. Check the lamp.</li>'
                      '<li style="list-style-type: none">No marker here.</li>'
                      '<li type="1">Close the gate.</li></ol>',encoding='utf-8')
    doc = inspect_source(source)
    result = plan(doc)
    assert not result['issues']
    assert [u['text'] for u in result['utterances']]==[
        'Number five, Open the gate.','Number two, Inspect the latch.',
        'Watch the road.','Number three, Check the lamp.','No marker here.',
        'Number one, Close the gate.']
    labels = [a for row in result['decisions'] for a in row.get('source_numbering',())]
    assert [row.get('authored_marker') for row in labels]==['V.','ii.','III.',None]
    assert labels[2]['source']=='III. ' and labels[2]['source_end']==5


def test_unresolved_inline_ordered_marker_reports_its_own_item(tmp_path):
    source = tmp_path/'custom.html'
    source.write_text('<ol style="list-style-type: custom-counter"><li>Inspect the latch.</li></ol>'
                      '<ul><li>Watch the road.</li></ul>',encoding='utf-8')
    doc = inspect_source(source)
    result = plan(doc)
    assert len(result['issues'])==1 and result['issues'][0]['block']==doc.blocks[0].id
    assert [unit['text'] for unit in result['utterances']]==['Watch the road.']


def test_writer_number_starts_restarts_and_nested_ownership_share_the_list_policy(tmp_path):
    namespaces = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                  'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                  'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"')
    styles = ('<office:styles><text:list-style style:name="Steps">'
              '<text:list-level-style-number text:level="1" style:num-format="I" style:num-suffix="." text:start-value="3"/>'
              '<text:list-level-style-number text:level="2" style:num-format="a" style:num-suffix=")"/>'
              '</text:list-style><text:list-style style:name="Bullets">'
              '<text:list-level-style-bullet text:level="1" text:bullet-char="•"/>'
              '</text:list-style></office:styles>')
    body = ('<office:body><office:text><text:list text:style-name="Steps" xml:id="one">'
            '<text:list-header><text:p>Watch the road.</text:p></text:list-header>'
            '<text:list-item><text:p>III. Open the gate.</text:p><text:list>'
            '<text:list-item><text:p>Inspect the latch.</text:p></text:list-item></text:list>'
            '<text:p>Stay near the gate.</text:p></text:list-item>'
            '<text:list-item text:start-value="7"><text:p>Check the lamp.</text:p></text:list-item></text:list>'
            '<text:list text:style-name="Steps" text:continue-list="one">'
            '<text:list-item><text:p>Close the gate.</text:p></text:list-item></text:list>'
            '<text:list text:style-name="Bullets"><text:list-item><text:p>Park the cart.</text:p>'
            '</text:list-item></text:list></office:text></office:body>')
    flat = tmp_path/'list.fodt'
    portable = tmp_path/'list.odt'
    mime = 'application/vnd.oasis.opendocument.text'
    flat.write_text(f'<office:document {namespaces} office:mimetype="{mime}">{styles}{body}</office:document>',
                    encoding='utf-8')
    with ZipFile(portable,'w') as archive:
        archive.writestr('mimetype',mime)
        archive.writestr('content.xml',f'<office:document-content {namespaces}>{body}</office:document-content>')
        archive.writestr('styles.xml',f'<office:document-styles {namespaces}>{styles}</office:document-styles>')
    for source in (flat,portable):
        result = plan(inspect_source(source,offline=True))
        assert not result['issues']
        assert [unit['text'] for unit in result['utterances']]==[
            'Watch the road.','Number three, Open the gate.','Letter A, Inspect the latch.',
            'Stay near the gate.','Number seven, Check the lamp.','Number eight, Close the gate.',
            'Park the cart.']
        labels = [a for row in result['decisions'] for a in row.get('source_numbering',())]
        assert [a['authored_marker'] for a in labels]==['III.','a)','VII.','VIII.']
        assert labels[0]['source']=='III. '
        assert labels[0]['marker_location']['source_file']==('styles.xml' if source==portable else flat.name)


def test_writer_keeps_conflicting_saved_marker_evidence_at_the_exact_list_item(tmp_path):
    path = tmp_path/'broken.fodt'
    path.write_text('''<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        office:mimetype="application/vnd.oasis.opendocument.text"><office:body><office:text>
        <text:list><text:list-item><text:number>III.</text:number>
        <text:p>Open the gate.</text:p></text:list-item></text:list>
        <text:p>Watch the road.</text:p></office:text></office:body></office:document>''',encoding='utf-8')
    document = inspect_source(path)
    affected,normal = document.blocks
    result = plan(document)
    assert affected.locator['ordered_marker']['notation']=='III.' and not normal.issue
    assert result['issues'][0]['block']==affected.id
    assert [unit['text'] for unit in result['utterances']]==['Watch the road.']
