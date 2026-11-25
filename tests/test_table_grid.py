from zipfile import ZipFile

from audiobook.intake import inspect_source
from audiobook.planner import plan
from test_upstream_epub import book
from test_word_intake import package, paragraph


HTML = ('<table><thead><tr><th rowspan="2">Station</th><th colspan="2">Watch reports</th></tr>'
        '<tr><th>Water</th><th>Wind</th></tr></thead><tbody>'
        '<tr><td rowspan="2">North harbor</td><td>The water stayed calm.</td><td>The breeze stayed light.</td></tr>'
        '<tr><td colspan="2"><p>The gate was shut</p><p>before dusk.</p>'
        '<table><tr><td>The lamp stayed lit.</td></tr></table><p>The keeper returned after dark.</p>'
        '</td></tr></tbody></table>')
READING = ('Table. Station: North harbor. Watch reports, Water: The water stayed calm. '
           'Wind: The breeze stayed light. Watch reports: The gate was shut before dusk. '
           'Table. The lamp stayed lit. The keeper returned after dark.')


def word_cell(text='',props='',*,content=None):
    return f'<w:tc><w:tcPr>{props}</w:tcPr>{paragraph(text) if content is None else content}</w:tc>'


def word_row(*cells,header=False):
    mark = '<w:trPr><w:tblHeader/></w:trPr>' if header else ''
    return '<w:tr>'+mark+''.join(cells)+'</w:tr>'


def word_table():
    group = '<w:gridSpan w:val="2"/>'
    start = '<w:vMerge w:val="restart"/>'
    bottom = '<w:vMerge/>'
    nested = '<w:tbl>'+word_row(word_cell('The lamp stayed lit.'))+'</w:tbl>'
    rich = paragraph('The gate was shut')+paragraph('before dusk.')+nested+paragraph('The keeper returned after dark.')
    return '<w:tbl>'+''.join((
        word_row(word_cell('Station',start),word_cell('Watch reports',group),header=True),
        word_row(word_cell(props=bottom),word_cell('Water'),word_cell('Wind'),header=True),
        word_row(word_cell('North harbor',start),word_cell('The water stayed calm.'),word_cell('The breeze stayed light.')),
        word_row(word_cell(props=bottom),word_cell(props=group,content=rich)),
    ))+'</w:tbl>'


def odt_cell(text='',attributes='',*,content=None):
    return f'<table:table-cell {attributes}>{f"<text:p>{text}</text:p>" if content is None else content}</table:table-cell>'


def odt_row(*cells):
    return '<table:table-row>'+''.join(cells)+'</table:table-row>'


def odt_table():
    group = 'table:number-columns-spanned="2"'
    height = 'table:number-rows-spanned="2"'
    covered = '<table:covered-table-cell/>'
    nested = '<table:table>'+odt_row(odt_cell('The lamp stayed lit.'))+'</table:table>'
    rich = '<text:p>The gate was shut</text:p><text:p>before dusk.</text:p>'+nested+'<text:p>The keeper returned after dark.</text:p>'
    return ('<table:table><table:table-header-rows>'
            +odt_row(odt_cell('Station',height),odt_cell('Watch reports',group),covered)
            +odt_row(covered,odt_cell('Water'),odt_cell('Wind'))+'</table:table-header-rows>'
            +odt_row(odt_cell('North harbor',height),odt_cell('The water stayed calm.'),odt_cell('The breeze stayed light.'))
            +odt_row(covered,odt_cell(attributes=group,content=rich),covered)+'</table:table>')


def odt(path,body):
    with ZipFile(path,'w') as archive:
        archive.writestr('mimetype','application/vnd.oasis.opendocument.text')
        archive.writestr('content.xml','<office:document-content '
                         'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                         'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
                         'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
                         'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                         f'<office:body><office:text>{body}</office:text></office:body></office:document-content>')
    return path


def test_anchored_writer_frame_reads_one_editable_story_and_its_table(tmp_path):
    body = ('<text:p>The keeper <draw:frame text:anchor-type="as-char"><draw:text-box>'
            '<text:p>The following readings were verified.</text:p>'+odt_table()+
            '</draw:text-box><draw:image/></draw:frame> closed the ledger.</text:p>'
            '<text:p>She put out the lamp.</text:p>')
    document = inspect_source(odt(tmp_path/'framed.odt',body))
    result = plan(document)
    assert not result['issues']
    joined = ' '.join(unit['text'] for unit in result['utterances'])
    assert joined==('The keeper closed the ledger. The following readings were verified. '+READING+
                    ' She put out the lamp.')
    assert any('/draw:frame/draw:text-box/table:table' in block.locator['element']
               for block in document.blocks)


def test_table_origins_headers_and_nesting_match_in_four_readers_and_zip(tmp_path):
    web = tmp_path/'watch.html'
    web.write_text(HTML)
    ebook = tmp_path/'watch.epub'
    ebook.write_bytes(book({'watch':HTML},['watch']))
    word = package(tmp_path/'watch.docx',word_table())
    writer = odt(tmp_path/'watch.odt',odt_table())
    wrapped = tmp_path/'edition.zip'
    with ZipFile(wrapped,'w') as archive:
        archive.write(word,'reading/watch.docx')
    order = [(0,0,2,1),(0,1,1,2),(1,1,1,1),(1,2,1,1),
             (2,0,2,1),(2,1,1,1),(2,2,1,1),(3,1,1,2)]
    for path in (web,ebook,word,writer,wrapped):
        doc = inspect_source(path,offline=True)
        block, = doc.blocks
        grid = block.locator['table_grid']
        assert [(x['row'],x['column'],x['rows'],x['columns']) for x in grid['cells']]==order
        assert all(cell['location'] for cell in grid['cells'])
        result = plan(doc)
        assert not result['issues'],path
        assert ' '.join(u['text'] for u in result['utterances'])==READING,path
        context = [row for row in result['decisions'][0]['context_structure']
                   if row['kind']=='authored_cell' and row['source']=='The breeze stayed light.']
        assert [h['label'] for h in context[0]['header_path']]==['Watch reports','Wind']


def test_complex_html_validates_header_ids_and_uses_nested_note(tmp_path):
    source = ('<table><thead><tr><th id="group" colspan="2">Reports</th></tr>'
              '<tr><th id="one">Name</th><th id="two">State</th></tr></thead>'
              '<tr><td headers="group one">Harbor</td><td headers="group two">'
              '<p>Clear skies.</p><table><tr><td>Checked<a role="doc-noteref" href="#n">1</a>.</td></tr></table>'
              '</td></tr></table><aside role="doc-footnote" id="n">The keeper checked the lamp.</aside>')
    path = tmp_path/'nested.html'
    path.write_text(source)
    doc = inspect_source(path)
    result = plan(doc)
    assert not result['issues']
    text = ' '.join(u['text'] for u in result['utterances'])
    assert text.count('The keeper checked the lamp.')==1
    assert doc.blocks[0].links[0]['destination']=='source.html#'+doc.blocks[1].id
    path.write_text(source.replace('headers="group two"','headers="group missing"'))
    bad = plan(inspect_source(path))
    assert 'Authored table header references' in bad['issues'][0]['reason']


def test_grid_omission_and_empty_origin_remain_distinct(tmp_path):
    body = ('<w:tbl><w:tr><w:trPr><w:gridBefore w:val="1"/></w:trPr>'
            +word_cell('The next cell is present.')+'</w:tr>'
            +word_row(word_cell(),word_cell('The earlier cell is explicitly empty.'))+'</w:tbl>')
    doc = inspect_source(package(tmp_path/'empty.docx',body))
    grid = doc.blocks[0].locator['table_grid']
    assert [(c['row'],c['column'],len(c['parts'])) for c in grid['cells']]==[(0,1,1),(1,0,0),(1,1,1)]
    assert [unit['text'] for unit in plan(doc)['utterances']]==[
        'Table. The next cell is present. The earlier cell is explicitly empty.']


def test_writer_web_package_routes_as_text_even_when_it_contains_a_thumbnail(tmp_path):
    original = odt(tmp_path/'watch.odt',odt_table())
    web = tmp_path/'watch.oth'
    with ZipFile(original) as source, ZipFile(web,'w') as package_file:
        package_file.writestr('mimetype','application/vnd.oasis.opendocument.text-web')
        package_file.writestr('content.xml',source.read('content.xml'))
        package_file.writestr('Thumbnails/thumbnail.png',b'\x89PNG\r\n\x1a\n')
    zipped = tmp_path/'raw.zip'
    with ZipFile(zipped,'w') as outer:
        outer.write(web,'edition/watch.oth')
    for path in (web,zipped):
        doc = inspect_source(path)
        assert doc.format=='odt'
        assert ' '.join(u['text'] for u in plan(doc)['utterances'])==READING


WRITER_REVISION_NAMESPACES = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:loext="urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0"')
WRITER_REVISION_STYLES = '''<office:styles>
  <style:style style:name="Base" style:family="table-row">
    <style:table-row-properties loext:text-changes-only="false"/>
  </style:style>
  <style:style style:name="Row" style:family="table-row" style:parent-style-name="Base"/>
  <style:style style:name="Cell" style:family="table-cell">
    <style:table-cell-properties loext:text-changes-only="false"/>
  </style:style></office:styles>'''


def writer_revisions(path, rows, deletions, *, extra_records=''):
    records = ''.join(f'<text:changed-region xml:id="{ident}"><text:deletion>'
                      '<text:p>Deleted content.</text:p></text:deletion></text:changed-region>'
                      for ident in deletions)
    body = (f'<office:body><office:text><text:tracked-changes>{records}{extra_records}'
            f'</text:tracked-changes><table:table>{rows}</table:table></office:text></office:body>')
    ns = WRITER_REVISION_NAMESPACES
    if path.suffix=='.fodt':
        path.write_text(f'<office:document {ns} office:mimetype="application/vnd.oasis.opendocument.text">'
                        f'{WRITER_REVISION_STYLES}{body}</office:document>')
    else:
        with ZipFile(path,'w') as archive:
            archive.writestr('mimetype','application/vnd.oasis.opendocument.text')
            archive.writestr('content.xml',f'<office:document-content {ns}>{body}</office:document-content>')
            archive.writestr('styles.xml',f'<office:document-styles {ns}>{WRITER_REVISION_STYLES}'
                             '</office:document-styles>')
    return path


def writer_point(ident):
    return f'<text:change text:change-id="{ident}"/>'


def writer_revision_row(*cells):
    return odt_row(*cells).replace('<table:table-row>','<table:table-row table:style-name="Row">',1)


def test_writer_structural_edits_share_the_same_reading_in_packages_and_flat_xml(tmp_path):
    rows = (
        writer_revision_row(odt_cell(writer_point('goneRow')))
        +odt_row(odt_cell(writer_point('goneCell'),'table:style-name="Cell"'),odt_cell('Keep the ledger.'))
        +writer_revision_row(odt_cell('<text:change-start text:change-id="added"/>The lamp stayed lit.'
                                     '<text:change-end text:change-id="added"/>'))
        +odt_row(odt_cell(writer_point('emptyCell')),odt_cell('The cell beside this one is blank.')))
    declarations = ('<text:changed-region xml:id="added"><text:insertion/>'
                    '</text:changed-region>')
    paths = [writer_revisions(tmp_path/f'ledger{ext}',rows,('goneRow','goneCell','emptyCell'),
                              extra_records=declarations) for ext in ('.odt','.fodt')]
    wrapped = tmp_path/'reading.zip'
    with ZipFile(wrapped,'w') as archive:
        archive.write(paths[0],'book/ledger.odt')
    for source in (*paths,wrapped):
        doc = inspect_source(source,offline=True)
        result = plan(doc)
        assert not result['issues']
        grid = doc.blocks[0].locator['table_grid']
        assert [(c['row'],c['column'],len(c['parts'])) for c in grid['cells']]==[
            (0,0,1),(1,0,1),(2,0,0),(2,1,1)]
        assert ' '.join(u['text'] for u in result['utterances'])==(
            'Table. Keep the ledger. The lamp stayed lit. The cell beside this one is blank.')
        omissions = [entry for entry in doc.inventory if entry['kind']=='odf_table_revision']
        assert {entry['element']:entry['deletions'][0]['id'] for entry in omissions}=={
            'table-row':'goneRow','table-cell':'goneCell'}
        assert all('text:deletion' in entry['deletions'][0]['xml_path'] for entry in omissions)


def test_writer_revision_style_alone_does_not_remove_live_or_ambiguous_cells(tmp_path):
    style = 'table:style-name="Cell"'
    nested = '<table:table>'+odt_row(odt_cell('The beacon flashed.'))+'</table:table>'
    rows = odt_row(
        odt_cell(writer_point('duplicated'),style),
        odt_cell(writer_point('duplicated'),style),
        odt_cell(writer_point('typed'),style+' office:value-type="float" office:value="14"'),
        odt_cell(attributes=style,content='<text:p>'+writer_point('nested')+'</text:p>'+nested),
        odt_cell(writer_point('live')+'The bell rang.',style))
    source = writer_revisions(tmp_path/'ledger.odt',rows,('duplicated','typed','nested','live'))
    doc = inspect_source(source,offline=True)
    assert not any(entry['kind']=='odf_table_revision' for entry in doc.inventory)
    assert len(doc.blocks[0].locator['table_grid']['cells'])==5
    result = plan(doc)
    assert not result['issues']
    speech = ' '.join(u['text'] for u in result['utterances'])
    assert 'The beacon flashed.' in speech and 'The bell rang.' in speech


def test_writer_merged_cells_follow_surviving_rows_and_columns(tmp_path):
    covered = '<table:covered-table-cell/>'
    origin = odt_cell('North harbor.','table:number-rows-spanned="2"')
    point = odt_cell(writer_point('removed'))
    spanning_deleted = odt_cell(writer_point('removed'),'table:style-name="Cell" table:number-columns-spanned="2"')
    cases = {
        'continuation': (odt_row(origin,odt_cell('The sea was calm.'))
                         +writer_revision_row(covered,point),[['North harbor.','The sea was calm.']]),
        'origin': (writer_revision_row(odt_cell(writer_point('removed'),'table:number-rows-spanned="2"'))
                   +odt_row(covered,odt_cell('The sea was calm.')),[['','The sea was calm.']]),
        'columns': (odt_row(spanning_deleted,covered,odt_cell('The sea was calm.')),[['The sea was calm.']]),
    }
    for name,(rows,expected) in cases.items():
        source = writer_revisions(tmp_path/f'{name}.odt',rows,('removed',))
        doc = inspect_source(source,offline=True)
        assert not plan(doc)['issues']
        assert doc.blocks[0].locator['rows']==expected


def test_long_table_requests_break_on_complete_sentences(tmp_path):
    source = tmp_path/'register.html'
    rows = ''.join(f'<tr><td>Station {n:02d}</td><td>The keeper checked the lamp and logged reading {n:02d}.</td></tr>'
                   for n in range(1,9))
    source.write_text('<table><thead><tr><th colspan="2">Evening register</th></tr>'
                      '<tr><th>Station</th><th>Observation</th></tr></thead><tbody>'+rows+'</tbody></table>')
    doc = inspect_source(source)
    fresh = plan(doc)
    old = plan(doc,rule_revision='faithful-24')
    assert not fresh['issues'] and len(fresh['utterances'])>len(old['utterances'])
    assert all(len(u['text'].split())<=55 and u['text'].endswith('.') for u in fresh['utterances'])
    assert ' '.join(u['text'] for u in fresh['utterances'])==old['utterances'][0]['text']
