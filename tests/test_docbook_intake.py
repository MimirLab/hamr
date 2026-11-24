from pathlib import Path
from zipfile import ZipFile

from audiobook.core import loadjson
from audiobook.intake import extract_location, inspect_saved, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe

NS = 'xmlns="http://docbook.org/ns/docbook" xmlns:xi="http://www.w3.org/2001/XInclude"'
CHAPTER = '<chapter {ns}><title>{title}</title><para>{text}</para></chapter>'


def spoken(doc):
    return [item['text'] for item in plan(doc)['utterances']]


def test_docbook_project_reads_only_the_authored_edition_and_saves_the_closure(tmp_path):
    folder = tmp_path/'edition'
    (folder/'parts').mkdir(parents=True)
    source = folder/'master.dbk'
    source.write_text(f'<book {NS}><info><title>The Harbor Record</title>'
                      '<author><personname><firstname>Julia</firstname><surname>Case</surname></personname></author>'
                      '<revhistory><revision><revremark>Internal editorial remark</revremark></revision></revhistory>'
                      '<abstract><para>A record of the harbor.</para></abstract></info>'
                      '<xi:include href="parts/z.docbook"/><xi:include href="parts/a.docbook"/></book>')
    z = folder/'parts/z.docbook'
    z.write_text(CHAPTER.format(ns=NS,title='Before dawn',text='The bell rang.'))
    (folder/'parts/a.docbook').write_text(CHAPTER.format(ns=NS,title='At sunrise',text='The ferry left.'))
    path = tmp_path/'book.zip'
    with ZipFile(path,'w') as archive:
        for item in sorted(folder.rglob('*')):
            if item.is_file():
                archive.write(item,'edition/'+item.relative_to(folder).as_posix())
    assert describe(folder).member_name=='master.dbk'
    expected = ['The Harbor Record','A record of the harbor.','Before dawn','The bell rang.',
                'At sunrise','The ferry left.']
    for candidate in (source,folder,path):
        doc = inspect_source(candidate)
        assert doc.title=='The Harbor Record' and not plan(doc)['issues']
        assert doc.metadata['authors']==['Julia Case']
        assert spoken(doc)==expected
        bell, = [block for block in doc.blocks if block.source=='The bell rang.']
        assert bell.locator['member'].endswith('parts/z.docbook')
        assert bell.locator['xml_path']=='/*/*[2]'
    attempt = tmp_path/'capture'
    attempt.mkdir()
    saved,selection = capture(folder,attempt)
    z.write_text('<changed/>')
    assert spoken(inspect_saved(saved,selection))==expected


def test_standalone_legacy_docbook_chapter_keeps_title_and_inline_note(tmp_path):
    source = tmp_path/'legacy.xml'
    source.write_text('''<?xml version="1.0"?>
<!DOCTYPE chapter PUBLIC "-//OASIS//DTD DocBook XML V4.5//EN" "https://example.invalid/docbookx.dtd">
<chapter><chapterinfo><title>The Watch</title><titleabbrev>Watch</titleabbrev></chapterinfo>
<para>The light shone<footnote id="lamp"><para>It was visible across the bay.</para></footnote>.</para>
<para>The keeper checked the lamp<footnoteref linkend="lamp"/>.</para></chapter>''')
    doc = inspect_source(source,offline=True)
    assert doc.title=='The Watch' and not plan(doc)['issues']
    assert spoken(doc)==['The Watch','The light shone. Note. It was visible across the bay. End note.',
                        'The keeper checked the lamp. Note. It was visible across the bay. End note.']
    references = [span for block in doc.blocks for span in block.locator.get('inline_semantics',())
                  if span['kind']=='note_reference']
    assert [s['source_locator']['xml_path'] for s in references]==[
        '/chapter/para[1]/footnote','/chapter/para[2]/footnoteref']


def test_cals_notes_visuals_and_xrefs_use_common_preparation_and_exact_resources(tmp_path):
    from test_media import prepared

    source = tmp_path/'journal.docbook'
    source.write_text(f'''<article {NS}><title>Harbor Notes</title>
<section xml:id="weather"><info><title>Weather Log</title></info>
<para>See <xref linkend="signal"/>.</para>
<table><title>Daily Weather</title><tgroup cols="2">
<colspec colname="day"/><colspec colname="forecast"/>
<thead><row><entry>Day</entry><entry>Forecast</entry></row></thead>
<tbody><row><entry colname="day">Monday</entry><entry colname="forecast">Clear
<footnote xml:id="change"><para>Rain came later.</para></footnote></entry></row>
<row><entry colname="forecast">Rain<footnoteref linkend="change"/></entry></row></tbody>
</tgroup></table>
<figure xml:id="signal"><title>The lighthouse</title><mediaobject>
<imageobject role="html"><imagedata fileref="lamp.png"/></imageobject>
<imageobject role="print"><imagedata fileref="lamp.png"/></imageobject>
<textobject role="alt"><phrase>A lamp shines across the harbor.</phrase></textobject>
</mediaobject></figure></section></article>''')
    image = tmp_path/'lamp.png'
    image.write_bytes(b'original illustrated asset')
    doc = inspect_source(source,offline=True)
    assert not plan(doc)['issues']
    table, = [block for block in doc.blocks if block.role=='table']
    assert table.locator['rows']==[['Day','Forecast'],['Monday','Clear'],['','Rain']]
    assert table.locator['table_header_cells']==[0,1]
    assert sum(len(cell['notes']) for cell in table.locator['table_cell_semantics'])==2
    text = ' '.join(spoken(doc))
    assert 'See The lighthouse.' in text and 'The lighthouse. A lamp shines across the harbor.' in text
    assert text.count('A lamp shines across the harbor.')==1
    assert text.count('Rain came later.')==1
    store,job = prepared(tmp_path,source)
    moved = Path(render(store,job['id'],allow_synthesis=False)['output']).rename(tmp_path/'moved')
    comp = loadjson(moved/'companion.json')
    for original in (source,image):
        ident = next(key for key,value in comp['source_locations'].items() if value.get('member')==original.name)
        output = tmp_path/('recovered-'+original.name)
        extract_location(moved,ident,output)
        assert output.read_bytes()==original.read_bytes()


def test_docbook_expands_literal_entities_and_holds_the_unavailable_occurrence(tmp_path):
    source = tmp_path/'edition.dbk'
    source.write_text(f'''<!DOCTYPE book [<!ENTITY boat "ferry"><!ENTITY product SYSTEM "not-supplied.ent">]>
<book {NS}><title>Harbor Log</title><chapter><title>Morning</title>
<para>The &boat; left before dawn.</para>
<para>The &product; parts arrived.</para>
<para>A lantern was on.</para></chapter></book>''')
    doc = inspect_source(source,offline=True)
    assert spoken(doc)==['Harbor Log','Morning','The ferry left before dawn.','A lantern was on.']
    issue, = plan(doc)['issues']
    span, = [item for item in issue['location']['inline_semantics'] if item['kind']=='source_review']
    assert span['source_locator']['xml_entity']=='product'
    assert span['source_locator']['lines']==[4,4]


def test_docbook_default_performance_matches_ordinary_html(tmp_path):
    line = '“Stay close,” she whispered.'
    source = tmp_path/'scene.dbk'
    source.write_text(f'<article {NS}><title>Scene</title><para><quote>Stay close,</quote> she whispered.</para></article>')
    other = tmp_path/'scene.html'
    other.write_text(f'<html><body><h1>Scene</h1><p>{line}</p></body></html>')
    left,right = (plan(inspect_source(item))['utterances'] for item in (source,other))
    match = lambda entries: [(entry['text'],entry.get('delivery'),entry.get('performance')) for entry in entries]
    assert match(left)==match(right)


def test_refentry_uses_normal_code_policy_and_projects_authored_syntax(tmp_path):
    source = tmp_path/'manual.xml'
    source.write_text(f'''<refentry {NS}><refmeta><refentrytitle>weather</refentrytitle><manvolnum>1</manvolnum></refmeta>
<refnamediv><refname>weather</refname><refname>forecast</refname><refpurpose>Report the forecast.</refpurpose></refnamediv>
<refsynopsisdiv><cmdsynopsis><command>weather</command><arg choice="opt" rep="repeat">place</arg>
<group choice="req"><arg choice="plain">--brief</arg><arg choice="plain">--detail</arg></group>
</cmdsynopsis></refsynopsisdiv></refentry>''')
    doc = inspect_source(source,offline=True)
    assert doc.title=='weather'
    assert spoken(doc)==['weather','Also called forecast.','Report the forecast.',
                         'A literal block is available in the companion.']
    code, = [block.source for block in doc.blocks if block.role=='code']
    assert code=='weather [place]... {--brief | --detail}'


def test_docbook_html_table_keeps_caption_and_column_headings(tmp_path):
    source = tmp_path/'journal.xml'
    source.write_text(f'''<article {NS}><title>Harbor Log</title><table><caption>Daily readings</caption>
<thead><tr><td>Day</td><td>Weather</td></tr></thead><tbody><tr><td>Monday</td><td>Rain</td></tr></tbody>
</table></article>''')
    doc = inspect_source(source,offline=True)
    table, = [block for block in doc.blocks if block.role=='table']
    assert table.locator['table_header_cells']==[0,1]
    assert 'Daily readings' in ' '.join(spoken(doc))
    assert 'Day: Monday; Weather: Rain' in ' '.join(spoken(doc))


def test_simplelist_preserves_list_item_and_inline_boundaries(tmp_path):
    source = tmp_path/'journal.xml'
    source.write_text(f'''<article {NS}><title>Harbor Log</title>
<para>We passed <simplelist type="inline"><member>the lighthouse</member><member>the ferry</member></simplelist>.</para>
<simplelist type="vert"><member>The bell</member><member>A bright lantern</member></simplelist>
</article>''')
    doc = inspect_source(source)
    assert 'We passed the lighthouse, the ferry.' in spoken(doc)
    assert 'The bell. A bright lantern.' in ' '.join(spoken(doc))
