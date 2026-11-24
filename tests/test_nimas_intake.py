import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest

from audiobook.core import Error, filehash, loadjson
from audiobook.intake import extract_location, inspect_saved, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe


BOOK = b'''<dtbook xmlns="http://www.daisy.org/z3986/2005/dtbook/" version="2005-3" xml:lang="en">
<head/><book>
<frontmatter><doctitle>Harbor Watch</doctitle><docauthor>Annie Hale</docauthor>
  <level1 class="contents"><h1>Printed Contents</h1><list type="pl"><li><lic>Arrival</lic>
  <lic class="pagenum">87</lic></li></list></level1>
</frontmatter>
<bodymatter><level1><h1>Arrival</h1><pagenum id="p87" page="normal">87</pagenum>
  <p>The sea was tran<pagenum id="p88" page="normal">88</pagenum>quil.<noteref idref="#n">7</noteref></p>
  <p>Consider <m:math xmlns:m="http://www.w3.org/1998/Math/MathML"><m:msup><m:mi>x</m:mi><m:mn>2</m:mn></m:msup></m:math> here.</p>
  <imggroup><img src="../art/harbor.png" alt="A beacon above the water."/>
    <caption>The entrance at low tide.</caption></imggroup>
  <table><tr><th>Object</th><th>Location</th></tr><tr><td>Beacon</td><td>Harbor</td></tr></table>
</level1></bodymatter>
<rearmatter><level1><note id="n"><p>The watch began at sunset.</p></note></level1></rearmatter>
</book></dtbook>'''

PACKAGE = b'''<package xmlns="http://openebook.org/namespaces/oeb-package/1.0/" unique-identifier="id">
<metadata><dc-metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:Format>NIMAS 1.1</dc:Format><dc:Title>Harbor Watch</dc:Title>
  <dc:Creator role="author">Annie Hale</dc:Creator><dc:Creator role="edt">Robin Rose</dc:Creator>
  <dc:Identifier id="id">Harbor-1</dc:Identifier><dc:Language>en</dc:Language>
</dc-metadata></metadata>
<manifest><item id="cover" href="copyright.pdf" media-type="application/pdf"/>
  <item id="figure" href="art/harbor.png" media-type="image/png"/>
  <item id="book" href="text/body.xml" media-type="application/x-dtbook+xml"/>
</manifest><spine><itemref idref="book"/></spine></package>'''


@pytest.fixture
def book_files():
    return {'harbor.opf':PACKAGE,'text/body.xml':BOOK,'art/harbor.png':b'\x89PNG\r\n\x1a\n',
            'copyright.pdf':b'%PDF-1.4\n'}


def write_archive(target,files):
    with ZipFile(target,'w') as archive:
        for name,raw in files.items():
            archive.writestr('edition/'+name,raw)


def test_dtbook_speech_is_the_same_for_loose_and_packaged_text(tmp_path,book_files):
    loose = tmp_path/'body.dtbook'
    loose.write_bytes(BOOK)
    target = tmp_path/'book.zip'
    write_archive(target,book_files)
    original,bundled = inspect_source(loose),inspect_source(target)
    assert bundled.metadata['intake']['route']=='nimas'
    assert bundled.metadata['authors']==['Annie Hale']
    assert bundled.metadata['contributors']==[{'name':'Robin Rose','role':'edt'}]
    assert bundled.metadata['source_identifiers']==['Harbor-1']
    assert bundled.source_sha256==filehash(target)
    plans = [plan(doc) for doc in (original,bundled)]
    assert plans[0]['chapters']==plans[1]['chapters']==['Harbor Watch','Arrival']
    assert not any(item['issues'] for item in plans)
    words = [' '.join(u['text'] for u in item['utterances']) for item in plans]
    assert words[0]==words[1]
    assert 'The sea was tranquil.' in words[0] and words[0].count('The watch began at sunset.')==1
    assert 'x to the power of 2' in words[0] and 'A beacon above the water.' in words[0]
    assert '87' not in words[0] and '88' not in words[0] and 'Printed Contents' not in words[0]
    page = next(block for block in bundled.blocks if block.source=='87')
    assert page.locator['member']=='edition/text/body.xml' and page.locator['lines']==[7,7]
    assert page.role=='hidden'
    assert any(r['member']=='edition/art/harbor.png' and r['status']=='referenced' for r in bundled.inventory)


def test_nimas_folder_and_descriptor_capture_declared_members(tmp_path,book_files):
    folder = tmp_path/'edition'
    for name,data in {**book_files,'unrelated.txt':b'A different book.'}.items():
        path = folder/name
        path.parent.mkdir(exist_ok=True,parents=True)
        path.write_bytes(data)
    with pytest.raises(Error) as exc:
        describe(folder)
    assert {item['id'] for item in exc.value.candidates}=={'harbor.opf','unrelated.txt'}
    preview = inspect_source(folder,entry='harbor.opf')
    assert inspect_source(folder/'harbor.opf').json()==preview.json()
    stage = tmp_path/'stage'
    stage.mkdir()
    snapshot,selected = capture(folder,stage,entry='harbor.opf')
    (folder/'text/body.xml').unlink()
    assert inspect_saved(snapshot,selected).json()==preview.json()
    with ZipFile(snapshot) as archive:
        assert set(archive.namelist())==book_files.keys()


def test_mixed_zip_only_claims_declared_nimas_components(tmp_path,book_files):
    target = tmp_path/'mixed.zip'
    write_archive(target,{**book_files,'other.txt':b'An unrelated book.'})
    with pytest.raises(Error) as exc:
        describe(target)
    assert exc.value.code=='source_selection'
    choices = {item['name']:item['id'] for item in exc.value.candidates}
    assert choices.keys()=={'edition/other.txt','Harbor Watch'}
    assert describe(target,entry=choices['Harbor Watch']).route=='nimas'
    assert describe(target,entry=choices['edition/other.txt']).route=='text'


def test_missing_spine_is_held_at_the_descriptor(tmp_path,book_files):
    target = tmp_path/'incomplete.zip'
    del book_files['text/body.xml']
    write_archive(target,book_files)
    result = inspect_source(target)
    issue, = plan(result)['issues']
    assert issue['location']['member']=='edition/harbor.opf'
    assert 'text/body.xml' in issue['reason']


def test_zip_finds_xml_root_after_prolog_and_ignores_book_words_in_xml_comment(tmp_path):
    target = tmp_path/'mixed.zip'
    prolog = '<?xml version="1.0" encoding="UTF-16"?>\n<!-- '+('wrapper data '*110)+' -->\n'
    with ZipFile(target,'w') as archive:
        archive.writestr('book.xml',(prolog+BOOK.decode()).encode('utf-16'))
        archive.writestr('sidecar.xml',b'<!-- <book> sample text -->\n<alto/>')
    doc = inspect_source(target)
    assert doc.metadata['archive_entry']['name']=='book.xml'
    assert doc.title=='Harbor Watch'


def test_moved_publication_recovers_nimas_descriptor_xml_and_assets(tmp_path,book_files):
    from test_media import prepared

    target = tmp_path/'book.zip'
    write_archive(target,book_files)
    store,job = prepared(tmp_path,target)
    moved = tmp_path/'moved'
    shutil.move(render(store,job['id'],allow_synthesis=False)['output'],moved)
    companion = loadjson(moved/'companion.json')
    assert filehash(moved/companion['original_source'])==filehash(target)
    for name,data in book_files.items():
        location = next(location for location,where in companion['source_locations'].items()
                        if where.get('member')=='edition/'+name)
        output = tmp_path/('original-'+Path(name).name)
        extract_location(moved,location,output)
        assert output.read_bytes()==data
