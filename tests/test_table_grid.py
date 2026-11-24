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
                         'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
                         'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                         f'<office:body><office:text>{body}</office:text></office:body></office:document-content>')
    return path


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
