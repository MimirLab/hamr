from pathlib import Path
from zipfile import ZipFile

from PIL import Image

from audiobook.core import loadjson
from audiobook.intake import extract_location, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan

NS = 'http://www.tei-c.org/ns/1.0'


def tei(body,*,appendix=''):
    return (f'<TEI xmlns="{NS}" xml:lang="en"><teiHeader><fileDesc><titleStmt>'
            '<title type="main">The Harbor Record</title><author>Alice Morgan</author>'
            '</titleStmt></fileDesc></teiHeader><text><body>'+body+'</body></text>'+appendix+'</TEI>')


def speech(doc):
    return ' '.join(row['text'] for row in plan(doc)['utterances'])


def test_tei_uses_one_authored_reading_and_preserves_alternatives(tmp_path):
    path = tmp_path/'record.tei'
    path.write_text(tei(
        '<div><head>Departure</head><p>The <choice><orig>olde</orig><reg>old</reg></choice> ferry '
        '<app><lem wit="#main">left</lem><rdg wit="#alternate">arrived</rdg></app> '
        'past the <choice><sic>becon</sic><corr>beacon</corr></choice> at dawn.</p></div>'))
    doc = inspect_source(path)
    assert not plan(doc)['issues']
    assert doc.title=='The Harbor Record' and doc.language=='en' and doc.metadata['authors']==['Alice Morgan']
    assert speech(doc)=='Departure The old ferry left past the beacon at dawn.'
    variants = [item for item in doc.inventory if item['kind']=='text_variant']
    assert len(variants)==3
    main = variants[1]
    assert [reading['text'] for reading in main['readings']]==['left','arrived']
    assert [reading['selected'] for reading in main['readings']]==[True,False]
    assert main['readings'][1]['witnesses']==['#alternate']
    assert main['readings'][1]['xml_path'] and main['lines']==[1,1]


def test_stand_off_bindings_keep_the_base_and_review_competing_inline_readings(tmp_path):
    path = tmp_path/'record.xml'
    path.write_text(tei(
        '<p xml:id="line">The <w xml:id="point">ferry</w> turned north.</p>'
        '<p>The bell rang <app><rdg>at noon</rdg><rdg>at dawn</rdg></app>.</p>'
        '<p>The watch saw <gap reason="illegible"/> in the fog.</p>',
        appendix='<standOff><listApp><app from="#point"><rdg wit="#later">ship</rdg></app></listApp></standOff>'))
    doc = inspect_source(path)
    result = plan(doc)
    assert [u['text'] for u in result['utterances']]==['The ferry turned north.']
    assert ['competing readings' in issue['reason'] for issue in result['issues']]==[True,False]
    for issue in result['issues']:
        span, = [x for x in issue['location']['inline_semantics'] if x['kind']=='source_review']
        assert span['source_locator']['xml_path']
    linked, = [item for item in doc.inventory if item['status']=='base_text']
    assert linked['readings'][0]['text']=='ship'
    assert linked['base_reference']['from']['reference']=='#point'
    assert linked['base_reference']['from']['targets'][0]['xml_path']


def test_inline_and_explicit_notes_use_the_same_graph_and_keep_label_offsets(tmp_path):
    path = tmp_path/'record.tei'
    path.write_text(tei(
        '<p>The boat passed the pier<note>The far shore was dark.</note>.</p>'
        '<p>The light came on<ref type="note" target="#n2">1</ref>.</p>'
        '<div type="notes"><note xml:id="n2"><p>It came from the outer pier.</p></note></div>'))
    doc = inspect_source(path)
    assert not plan(doc)['issues']
    assert speech(doc).count('The far shore was dark.')==1
    assert speech(doc).count('It came from the outer pier.')==1
    assert 'on1' not in speech(doc)
    note_blocks = {b.id for b in doc.blocks if b.role=='note'}
    links = [link for b in doc.blocks for link in b.links if link['kind']=='note']
    assert len(links)==2 and all(link['destinations'][0].split('#')[1] in note_blocks for link in links)


def test_reuses_package_selection_and_recovers_exact_xml_and_graphic(tmp_path):
    from test_media import prepared

    article = tei('<p>A light shone <choice><orig>thro</orig><reg>through</reg></choice> the fog.</p>'
                  '<figure><head>The harbor signal.</head><figDesc>A light flashes.</figDesc>'
                  '<graphic url="signal.png"/></figure>')
    directory = tmp_path/'edition'
    directory.mkdir()
    (directory/'record.tei').write_text(article)
    picture = directory/'signal.png'
    Image.new('L',(1,1),255).save(picture)
    members = {'record.tei':article.encode(),'signal.png':picture.read_bytes()}
    archive = tmp_path/'publication.zip'
    with ZipFile(archive,'w') as output:
        for member,data in members.items():
            output.writestr('bundle/'+member,data)
    assert speech(inspect_source(directory))==speech(inspect_source(archive))
    store,job = prepared(tmp_path,archive)
    moved = Path(render(store,job['id'],allow_synthesis=False)['output']).rename(tmp_path/'moved')
    companion = loadjson(moved/'companion.json')
    assert 'thro' in (moved/'source.html').read_text()
    assert 'Text variants' in (moved/'source.html').read_text()
    assert (moved/companion['original_source']).read_bytes()==archive.read_bytes()
    for member,data in members.items():
        key = next(key for key,value in companion['source_locations'].items() if value.get('member')=='bundle/'+member)
        target = tmp_path/member
        extract_location(moved,key,target)
        assert target.read_bytes()==data
