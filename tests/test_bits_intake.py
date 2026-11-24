from pathlib import Path
from zipfile import ZipFile

import pytest

from audiobook.core import Error, loadjson
from audiobook.intake import extract_location, inspect_saved, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe

NS = 'xmlns:xi="http://www.w3.org/2001/XInclude" xmlns:xlink="http://www.w3.org/1999/xlink"'
META = '<book-meta><book-title-group><book-title>Harbor Almanac</book-title></book-title-group></book-meta>'


def part(title,text,more=''):
    return ('<book-part><book-part-meta><title-group><title>'+title+'</title></title-group></book-part-meta>'
            '<body><p>'+text+'</p>'+more+'</body></book-part>')


def lines(doc):
    return [u['text'] for u in plan(doc)['utterances']]


def archive(path,data):
    with ZipFile(path,'w') as out:
        for name,raw in data.items():
            out.writestr(name,raw)


def test_wrapper_uses_part_title_and_shared_note_and_caption(tmp_path):
    source = tmp_path/'selected.nxml'
    chapter = part('Morning Watch','The bell rang<fn id="bell"><label>1</label><p>It carried across the bay.</p></fn>'
                   '<xref rid="bell" ref-type="fn">1</xref>.',
                   '<fig><caption><p>The lighthouse.</p></caption><alt-text>A bright lamp.</alt-text>'
                   '<graphic xlink:href="lamp.png"/></fig>')
    source.write_text(f'<book-part-wrapper dtd-version="2.2" {NS}>{META}{chapter}</book-part-wrapper>')
    doc = inspect_source(source)
    assert (doc.title,doc.metadata['parent_book_title'])==('Morning Watch','Harbor Almanac')
    assert not plan(doc)['issues']
    spoken = ' '.join(lines(doc))
    assert spoken.startswith('Morning Watch The bell rang. Note. It carried across the bay. End note.')
    assert 'A bright lamp.' in spoken and 'Harbor Almanac' not in spoken
    assert sum(link['kind']=='note' for block in doc.blocks for link in block.links)==1


def test_authored_local_includes_select_the_edition_and_keep_physical_sources(tmp_path):
    chapter = part('Departure','The ferry left.')
    descriptor = (f'<book dtd-version="2.2" {NS}>{META}<book-body xml:base="parts/">'
                  '<xi:include href="z.nxml"/><xi:include href="a.nxml" xpointer="element(selected)"/>'
                  '<xi:include href="z.nxml"/></book-body></book>')
    wrapped = (f'<book-part-wrapper {NS}>{META}'
               +part('Arrival','A lantern shone.').replace('<book-part>','<book-part id="selected">')
               +'</book-part-wrapper>')
    edition = tmp_path/'edition'
    subtree = edition/'parts'
    subtree.mkdir(parents=True)
    (edition/'master.nxml').write_text(descriptor)
    (subtree/'a.nxml').write_text(wrapped)
    (subtree/'z.nxml').write_text(chapter)
    path = tmp_path/'full.tar.zip'
    archive(path,{'vol/parts/a.nxml':wrapped,'vol/parts/z.nxml':chapter,'vol/master.nxml':descriptor})
    assert describe(edition).member_name=='master.nxml'
    for candidate in (edition,edition/'master.nxml',path):
        doc = inspect_source(candidate)
        assert doc.title=='Harbor Almanac' and not plan(doc)['issues']
        assert lines(doc)==['Harbor Almanac','Departure','The ferry left.','Arrival','A lantern shone.',
                            'Departure','The ferry left.']
        occurrences = [block for block in doc.blocks if block.source=='The ferry left.']
        assert len({b.locator['location_id'] for b in occurrences})==2
        assert all(b.locator['member'].endswith('parts/z.nxml') and b.locator['xml_path']=='/book-part/body/p'
                   for b in occurrences)
        entries = [i for i in doc.inventory if i.get('kind')=='xml_include']
        assert [i['href'] for i in entries]==['z.nxml','a.nxml','z.nxml']
    attempt = tmp_path/'state'
    attempt.mkdir()
    immutable,selection = capture(edition,attempt)
    (subtree/'z.nxml').write_text('<changed/>')
    assert lines(inspect_saved(immutable,selection))==lines(inspect_source(path))


def test_local_fallback_and_cycle_remain_scoped_to_the_include(tmp_path):
    path = tmp_path/'book.bits'
    path.write_text(f'<book {NS}>{META}<book-body>'
                    '<xi:include href="missing.nxml"><xi:fallback>'
                    +part('Arrival','An early boat arrived.')
                    +'</xi:fallback></xi:include>\n'
                    '<xi:include href="book.bits"/></book-body></book>')
    doc = inspect_source(path)
    result = plan(doc)
    assert lines(doc)==['Harbor Almanac','Arrival','An early boat arrived.']
    issue, = result['issues']
    assert 'cycle' in issue['reason'] and issue['location']['xml_path']
    entries = [row for row in doc.inventory if row.get('kind')=='xml_include']
    assert [row['status'] for row in entries]==['fallback','unavailable']


def test_moved_book_recovers_exact_included_chapter_and_asset(tmp_path):
    from test_media import prepared

    data = {
        'publication/main.nxml':f'<book {NS}>{META}<book-body><xi:include href="parts/ch.nxml"/></book-body></book>',
        'publication/parts/ch.nxml':f'<book-part {NS}><book-part-meta><title-group><title>Watch</title></title-group>'
                                   '</book-part-meta><body><p>The lamp came on.</p><fig><alt-text>A lamp glows.</alt-text>'
                                   '<graphic xlink:href="../fig.png"/></fig></body></book-part>',
        'publication/fig.png':b'original image bytes',
    }
    path = tmp_path/'book.zip'
    archive(path,data)
    store,job = prepared(tmp_path,path)
    moved = Path(render(store,job['id'],allow_synthesis=False)['output']).rename(tmp_path/'moved')
    comp = loadjson(moved/'companion.json')
    assert (moved/comp['original_source']).read_bytes()==path.read_bytes()
    for filename,raw in data.items():
        ident = next(key for key,loc in comp['source_locations'].items() if loc.get('member')==filename)
        output = tmp_path/Path(filename).name
        extract_location(moved,ident,output)
        assert output.read_bytes()==(raw.encode() if isinstance(raw,str) else raw)


def bookshelf_fixture():
    metadata = META.replace('<book-meta>','<book-meta><book-id book-id-type="pmcid">harbor</book-id>')

    def entry(ident,title,children=''):
        return (f'<toc-entry><title>{title}</title><nav-pointer nav-pointer-type="point">'
                f'<related-object link-type="booklink" source-id="harbor" document-id="{ident}"/>'
                f'</nav-pointer>{children}</toc-entry>')

    def chapter(ident,title,body,note):
        content = part(title,body+'<fn id="one"><p>'+note+'</p></fn><xref ref-type="fn" rid="one">1</xref>.')
        content = content.replace('<book-part-meta>',f'<book-part-meta><book-part-id book-part-id-type="pmcid">'
                                  f'{ident}</book-part-id>')
        return f'<book-part-wrapper id="{ident}">{metadata}{content}</book-part-wrapper>'

    toc = (f'<book-part-wrapper content-type="toc">{metadata}<toc>'
           +entry('Z','Before dawn',entry('Z','Section link'))+entry('A','At sunrise')
           +'</toc></book-part-wrapper>')
    return {'TOC.nxml':toc,'TOC.pdf':b'rendition',
            'A.nxml':chapter('A','At sunrise','The crew set sail','It was a calm day.'),
            'Z.nxml':chapter('Z','Before dawn','The bell rang','It woke the keeper.'),
            'A.pdf':b'rendition','Z.pdf':b'rendition','license.txt':b'publication license'}


def test_bookshelf_opens_as_one_ordered_publication_in_a_folder_and_zip(tmp_path):
    data = bookshelf_fixture()
    folder = tmp_path/'issued'
    folder.mkdir()
    for path,raw in data.items():
        (folder/path).write_bytes(raw.encode() if isinstance(raw,str) else raw)
    path = tmp_path/'issued.zip'
    archive(path,{'edition/'+name:raw for name,raw in data.items()})
    for candidate in (folder,folder/'TOC.nxml',path):
        doc = inspect_source(candidate,offline=True)
        result = plan(doc)
        assert doc.title=='Harbor Almanac' and not result['issues']
        assert ' '.join(lines(doc))==('Harbor Almanac Before dawn The bell rang. Note. It woke the keeper. '
                                      'End note. At sunrise The crew set sail. Note. It was a calm day. End note.')
        assert [Path(row['member']).name for row in doc.metadata['source_resources']
                if row['role']=='include']==['Z.nxml','A.nxml']
        assert {Path(row['member']).name for row in doc.metadata['source_resources']
                if row['role']=='asset'}=={'TOC.pdf','Z.pdf','A.pdf','license.txt'}
    assert inspect_source(folder/'Z.nxml').title=='Before dawn'


def test_bookshelf_links_hold_missing_chapters_and_leave_other_books_selectable(tmp_path):
    data = bookshelf_fixture()
    data['A.nxml'] = data['A.nxml'].replace('>harbor<','>different-identity<')
    data['Outside.txt'] = 'Another publication.'
    path = tmp_path/'mixed.zip'
    archive(path,data)
    with pytest.raises(Error,match='Choose the publication') as caught:
        describe(path)
    choices = {row['name']:row['id'] for row in caught.value.candidates}
    assert set(choices)=={'Harbor Almanac','At sunrise','A.pdf','Outside.txt'}
    doc = inspect_source(path,entry=choices['Harbor Almanac'],offline=True)
    assert all('The crew' not in text for text in lines(doc))
    issue, = plan(doc)['issues']
    assert 'At sunrise' in issue['reason']
    assert issue['location']['member']=='TOC.nxml'
    assert issue['location']['xml_path']=='/book-part-wrapper/toc/toc-entry[2]'


@pytest.mark.parametrize('suffix,layout', [
    ('.html','<html><body>{table}</body></html>'),
    ('.nxml',f'<book>{META}<book-body>'+part('Examples','Here are two examples.','{table}')+'</book-body></book>'),
    ('.xml','<book><chapter><title>Examples</title>{table}</chapter></book>'),
])
@pytest.mark.parametrize('span,needs_review',[('1',False),('2',True)])
def test_unit_table_spans_are_not_merge_operations(tmp_path,suffix,layout,span,needs_review):
    table = (f'<table><tr><th rowspan="{span}" colspan="1">Day</th><th>Weather</th></tr>'
             '<tr><td>Monday</td><td>Rain</td></tr></table>')
    path = tmp_path/('table'+suffix)
    path.write_text(layout.format(table=table))
    doc = inspect_source(path,offline=True)
    block, = [b for b in doc.blocks if b.role=='table']
    assert bool(block.issue)==needs_review
    assert block.locator['rows']==[['Day','Weather'],['Monday','Rain']]
