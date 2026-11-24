import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup

from audiobook.core import Error, filehash
from audiobook.intake import extract_location, inspect_saved, inspect_source
from audiobook.importers import markdown
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.scan_intake import sequence
from audiobook.source_bundle import capture, describe
from test_media import prepared


DESCRIPTOR = b'{"version":2,"type":"net.daringfireball.markdown"}'
BODY = b'# The Garden\n\nSee the sketch.\n\n![A lantern by the gate](assets/lantern%20sketch.png)\n'
ASSET_NAME = 'assets/lantern sketch.png'
ASSET = b'original illustration bytes'


def write_folder(path):
    package = path/'Book.textbundle'
    (package/'assets').mkdir(parents=True)
    (package/'text.md').write_bytes(BODY)
    (package/'info.json').write_bytes(DESCRIPTOR)
    (package/ASSET_NAME).write_bytes(ASSET)
    return package


def pack(prefix='',body=BODY,descriptor=DESCRIPTOR,text='text.md'):
    raw = io.BytesIO()
    with ZipFile(raw,'w') as archive:
        archive.writestr(prefix+'info.json',descriptor)
        archive.writestr(prefix+text,body)
        archive.writestr(prefix+ASSET_NAME,ASSET)
    return raw.getvalue()


@pytest.mark.parametrize('kind',['folder','root','wrapped','nested'])
def test_package_uses_normal_markdown_and_keeps_original_asset_identity(tmp_path,kind):
    if kind=='folder':
        path = write_folder(tmp_path)
    elif kind=='nested':
        path = tmp_path/'Download.zip'
        with ZipFile(path,'w') as archive:
            archive.writestr('notes/Book.textpack',pack())
    else:
        path = tmp_path/'Book.textpack'
        path.write_bytes(pack('Book.textbundle/' if kind=='wrapped' else ''))
    document = inspect_source(path,offline=True)
    result = plan(document)
    assert result['intake']['route']=='textbundle' and not result['issues']
    expected = [unit['text'] for unit in plan(markdown(BODY))['utterances']]
    assert [unit['text'] for unit in result['utterances']]==expected
    asset = next(item for item in document.metadata['source_resources'] if item['role']=='asset')
    assert asset['status']=='referenced'
    assert (asset.get('inner_member') or asset)['member'].endswith(ASSET_NAME)
    figure = next(block for block in document.blocks if block.source.startswith('Figure.'))
    assert figure.links[0]['destinations']==['source.html#resource-'+asset['location_id']]


def test_package_siblings_remain_distinct_without_leaking_assets_into_scan(tmp_path):
    folder = tmp_path/'notes'
    package = write_folder(folder)
    assert describe(folder).route=='textbundle'
    (folder/'Other.txt').write_text('The other publication.')
    with pytest.raises(Error) as caught:
        describe(folder)
    assert [c['id'] for c in caught.value.candidates]==[package.name,'Other.txt']
    assert inspect_source(folder,entry=package.name).format=='markdown'

    path = tmp_path/'mixed.zip'
    path.write_bytes(pack('Book.textbundle/'))
    with ZipFile(path,'a') as archive:
        archive.writestr('page2.png',b'A separate scan')
        archive.writestr('Other.txt',b'The other publication.')
    with pytest.raises(Error) as caught:
        describe(path)
    assert {c['id'] for c in caught.value.candidates}=={'pages','5','bundle:1'}
    source = describe(path,entry='pages')
    with sequence(path,source) as (_,pages,_,__):
        assert [page['path'] for page in pages]==['page2.png']
    assert inspect_source(path,entry='bundle:1').format=='markdown'


@pytest.mark.parametrize('nested',[False,True])
def test_recover_body_and_asset_from_a_moved_package_book(tmp_path,nested):
    path = tmp_path/('Outer.zip' if nested else 'One.textpack')
    if nested:
        with ZipFile(path,'w') as archive:
            archive.writestr('wrapped/One.textpack',pack())
    else:
        path.write_bytes(pack())
    original = path.read_bytes()
    store,job = prepared(tmp_path,path)
    result = render(store,job['id'],allow_synthesis=False)
    moved = Path(result['output']).rename(tmp_path/'moved')
    path.unlink()
    companion = json.loads((moved/'companion.json').read_text())
    assert (moved/companion['original_source']).read_bytes()==original
    asset = next(item for item in companion['source_locations'].values() if item.get('role')=='asset')
    page = BeautifulSoup((moved/'source.html').read_text(),'html.parser')
    assert page.find(id='resource-'+asset['location_id']).code.get_text()==asset['location_id']
    assert page.find('a',href='#resource-'+asset['location_id']) or page.find('a',href='source.html#resource-'+asset['location_id'])
    recovered = tmp_path/'recovered.png'
    extract_location(moved,asset['location_id'],recovered)
    assert recovered.read_bytes()==ASSET
    body = tmp_path/'recovered.md'
    extract_location(moved,companion['decisions'][0]['block'],body)
    assert body.read_bytes()==BODY


def test_directory_capture_is_stable_after_the_working_bundle_changes(tmp_path):
    folder = write_folder(tmp_path)
    work = tmp_path/'capture'
    work.mkdir()
    snapshot,source = capture(folder,work)
    baseline = filehash(snapshot)
    (folder/'text.md').write_text('A different working file.')
    (folder/ASSET_NAME).unlink()
    document = inspect_saved(snapshot,source)
    assert document.source_sha256==baseline
    assert document.blocks[0].source=='The Garden'
    assert document.blocks[-1].links[0]['kind']=='source_resource'


@pytest.mark.parametrize(('declared','filename','data','expected'),[
    ('public.utf16-plain-text','text.txt','Café at dawn.'.encode('utf-16'),'Café at dawn.'),
    ('public.html','text.html',b'<p>Reading the complete book.</p>','Reading the complete book.'),
    ('public.rtf','text.rtf',br'{\rtf1 Reading the complete book.}','Reading the complete book.'),
])
def test_declared_body_types_use_existing_readers(tmp_path,declared,filename,data,expected):
    path = tmp_path/'Book.textpack'
    descriptor = json.dumps({'version':2,'type':declared}).encode()
    path.write_bytes(pack(body=data,descriptor=descriptor,text=filename))
    result = plan(inspect_source(path,offline=True))
    assert not result['issues'] and [unit['text'] for unit in result['utterances']]==[expected]


def test_duplicate_package_member_is_rejected(tmp_path):
    path = tmp_path/'Book.textpack'
    path.write_bytes(pack())
    with ZipFile(path,'a') as archive, pytest.warns(UserWarning,match='Duplicate name'):
        archive.writestr(ASSET_NAME,b'conflicting resource')
    with pytest.raises(Error,match='repeats a member path'):
        inspect_source(path)
