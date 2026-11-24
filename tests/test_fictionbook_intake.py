from pathlib import Path
from zipfile import ZipFile

import pytest

from audiobook.core import Error, loadjson
from audiobook.intake import extract_location, inspect_saved, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe

BASE = 'http://www.fictionbook.org/FictionBook3/'
OPC = 'http://schemas.openxmlformats.org/package/2006/'
BODY = 'Parts/Caf%C3%A9'
FOLLOWUP = '<p>The ferry left.</p>'


def relationships(*links):
    return '<Relationships xmlns="'+OPC+'relationships">'+''.join(
        f'<Relationship Id="{ident}" Type="{kind}" Target="{target}"/>' for ident,kind,target in links
    )+'</Relationships>'


def edition():
    types = f'''<Types xmlns="{OPC}content-types">
<Override PartName="/parts/Caf%c3%a9" ContentType="application/fb3-body+xml"/>
<Override PartName="/meta/book.xml" ContentType="application/fb3-description+xml"/>
<Default Extension="SVG" ContentType="image/svg+xml"/>
</Types>'''
    description = (f'<fb3-description xmlns="{BASE}description" id="book">'
                   '<title><main>The Harbor Record</main></title><lang>en</lang>'
                   '<fb3-relations><subject link="author"><title>Julia Case</title></subject></fb3-relations>'
                   '</fb3-description>')
    body = f'''<fb3-body xmlns="{BASE}body" id="book">
<title><p>The Harbor Record</p></title>
<section id="opening"><title><p>Before dawn</p></title>
<p>The old bell rang before dawn<note href="n1">1</note>.</p>
<img src="lamp" alt="A light shines across the bay."/>
</section>
<section id="closing"><title><p>At noon</p></title>{FOLLOWUP}</section>
<notes show="0"><title><p>Private notes title</p></title><notebody id="n1">
<title><p>1</p></title><p>The bell could be heard across the bay.</p></notebody></notes>
</fb3-body>'''
    data = {
        '[Content_Types].xml':types,
        '_rels/.rels':relationships(('book',BASE+'relationships/Book','meta/book.XML')),
        'Meta/BOOK.xml':description,
        'Meta/_RELS/BOOK.XML.RELS':relationships(('body',BASE+'relationships/body','../PARTS/caf%C3%A9')),
        BODY:body,
        'Parts/_rels/Caf%C3%A9.rels':relationships(('lamp',BASE+'relationships/image','../Graphics/A%20Lamp.SVG')),
        'Graphics/A Lamp.svg':b'<svg xmlns="http://www.w3.org/2000/svg"/>',
        'Graphics/unused.svg':b'<svg xmlns="http://www.w3.org/2000/svg"/>',
    }
    return {name:value.encode() if isinstance(value,str) else value for name,value in data.items()}


def write_archive(path,files,prefix=''):
    with ZipFile(path,'w') as archive:
        for filename,raw in files.items():
            archive.writestr(prefix+filename,raw)


def texts(doc):
    return [item['text'] for item in plan(doc)['utterances']]


def test_declared_fb3_is_one_book_in_native_zip_folder_and_wrapper(tmp_path):
    files = edition()
    native = tmp_path/'book.fb3'
    same = tmp_path/'book.zip'
    nested = tmp_path/'wrapper.zip'
    directory = tmp_path/'exported'
    for name,raw in files.items():
        output = directory/(name.upper() if name=='[Content_Types].xml' else name)
        output.parent.mkdir(exist_ok=True,parents=True)
        output.write_bytes(raw)
    write_archive(native,files)
    write_archive(same,files)
    write_archive(nested,files,prefix='publisher/')
    expected = ['The Harbor Record','Before dawn',
                'The old bell rang before dawn. Note. The bell could be heard across the bay. End note.',
                'A light shines across the bay.','At noon','The ferry left.']
    for path in (native,same,directory,nested):
        assert describe(path).route=='fb3'
        doc = inspect_source(path,offline=True)
        assert doc.title=='The Harbor Record' and doc.language=='en' and doc.metadata['authors']==['Julia Case']
        assert texts(doc)==expected
        assert not plan(doc)['issues']
        body, = [item for item in doc.metadata['source_resources'] if item['role']=='text']
        assert body['member'].endswith(BODY)
        asset, = [item for item in doc.metadata['source_resources'] if item['status']=='referenced']
        assert asset['member'].endswith('Graphics/A Lamp.svg')
        assert 'note' in {link['kind'] for b in doc.blocks for link in b.links}
    destination = tmp_path/'snapshot'
    destination.mkdir()
    saved,source = capture(directory,destination)
    (directory/BODY).write_text('<changed/>')
    assert texts(inspect_saved(saved,source))==expected


def test_fb3_external_and_conflicting_body_parts_are_never_substituted(tmp_path):
    files = edition()
    target = tmp_path/'conflict.fb3'
    files['parts/caf%C3%A9'] = files[BODY]
    write_archive(target,files)
    with pytest.raises(Error,match='one local declared body part') as caught:
        inspect_source(target)
    assert caught.value.code=='source_fb3_package'
    del files['parts/caf%C3%A9']
    files['Meta/_RELS/BOOK.XML.RELS'] = files['Meta/_RELS/BOOK.XML.RELS'].replace(
        b'../PARTS/caf%C3%A9',b'https://example.invalid/body')
    write_archive(target,files)
    with pytest.raises(Error,match='one local declared body part'):
        inspect_source(target)


def test_moved_fb3_recovers_every_original_member(tmp_path):
    from test_media import prepared

    files = edition()
    path = tmp_path/'edition.fb3'
    write_archive(path,files)
    store,job = prepared(tmp_path,path)
    moved = Path(render(store,job['id'],allow_synthesis=False)['output']).rename(tmp_path/'moved')
    companion = loadjson(moved/'companion.json')
    assert (moved/companion['original_source']).read_bytes()==path.read_bytes()
    for index,(name,raw) in enumerate(files.items()):
        ident = next(ident for ident,loc in companion['source_locations'].items() if loc.get('member')==name)
        output = tmp_path/f'exact-{index}'
        extract_location(moved,ident,output)
        assert output.read_bytes()==raw


def test_explicit_package_relationships_allow_distinct_description_and_body_ids(tmp_path):
    files = edition()
    files[BODY] = files[BODY].replace(b'id="book"',b'id="revised-body"')
    source = tmp_path/'separate-identifiers.fb3'
    write_archive(source,files)
    doc = inspect_source(source,offline=True)
    assert not plan(doc)['issues']
    record, = [item for item in doc.inventory if item.get('kind')=='fb3_identifiers']
    assert (record['member'],record['body_member'],record['status'])==('Meta/BOOK.xml',BODY,'different')


def test_fb2_uses_the_same_note_and_authored_verse_preparation(tmp_path):
    source = tmp_path/'edition.fb2'
    lines = [f'The lantern stands beside the harbor on clear evening number {index}.'
             for index in range(1,35)]
    source.write_text('''<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"
xmlns:xlink="http://www.w3.org/1999/xlink">
<description><title-info><book-title>Harbor verse</book-title><lang>en</lang></title-info></description>
<body><section><title><p>Opening</p></title><p>The bell rang <a type="note" xlink:href="#bell">1</a>.</p>
<poem><stanza>'''+''.join(f'<v>{line}</v>' for line in lines)+'''</stanza></poem></section></body>
<body name="notes"><section id="bell"><title><p>1</p></title>
<p>A keeper heard it</p></section></body><binary id="portrait">Never narrate embedded bytes.</binary>
</FictionBook>''')
    doc = inspect_source(source)
    book = plan(doc)
    assert doc.title=='Harbor verse' and not book['issues']
    verse = [block for block in doc.blocks if block.locator.get('authored_line_breaks')]
    assert len(verse)==1 and verse[0].source.splitlines()==lines
    readings = [u for u in book['utterances'] if u.get('delivery_boundary')=='authored_lines']
    assert len(readings)>1
    assert [line for unit in readings for line in unit['text'].splitlines()]==lines
    assert 'The bell rang. Note. A keeper heard it. End note.' in texts(doc)
    assert 'embedded bytes' not in ' '.join(texts(doc))
