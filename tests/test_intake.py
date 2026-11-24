import io
import json
from pathlib import Path
import tarfile
from zipfile import ZipFile

from PIL import Image

from audiobook import jobs
from audiobook.core import Store, filehash
from audiobook.intake import extract_location, inspect_source
from audiobook.source_bundle import capture, describe


def make_page(path,text):
    Image.new('RGB',(320,200),'white').save(path)
    path.with_suffix('.hocr').write_text(
        f'<div class="ocr_page" title=\'image "{path.name}"; bbox 0 0 320 200\'>'
        '<p class="ocr_par"><span class="ocr_line">'
        f'<span class="ocrx_word" title="bbox 10 15 150 50">{text}</span></span></p></div>'
    )


def test_open_loose_pages_orders_naturally_and_freezes_originals(tmp_path):
    scans = tmp_path/'scans'
    scans.mkdir()
    for i, text in [(10,'Third page.'),(1,'First page.'),(2,'Second page.')]:
        make_page(scans/f'page{i}.jpg',text)
    store = Store(tmp_path/'app')
    result = jobs.create(store,scans)
    job = store.get(result['job'])
    pages = job['document']['inventory']
    assert [row['member'] for row in pages]==['page1.jpg','page2.jpg','page10.jpg']
    assert all(row['route']=='supplied_text' for row in pages)
    assert [block['source'] for block in job['document']['blocks']]==['First page.','Second page.','Third page.']
    snapshot = Path(job['snapshot'])
    original_hash = filehash(snapshot)
    (scans/'page1.jpg').unlink()
    (scans/'page10.hocr').write_text('changed')
    from audiobook.intake import inspect_saved
    assert inspect_saved(snapshot,job['source_selection']).source_sha256==original_hash


def test_manifest_keeps_frame_crop_and_intentional_repeats(tmp_path):
    images = [Image.new('RGB',(250,150),'white') for _ in range(2)]
    source = tmp_path/'spread.tif'
    images[0].save(source,save_all=True,append_images=images[1:])
    manifest = tmp_path/'pages.hamr.json'
    manifest.write_text(json.dumps({'schema':'hamr.page-sequence.v1','title':'Test copy','pages':[
        {'path':source.name,'occurrence':'first','frame':1,'crop':[0,0,.5,1],'role':'cover'},
        {'path':source.name,'occurrence':'second','frame':0,'role':'cover'},
        {'path':source.name,'occurrence':'repeat','frame':1,'crop':[0,0,.5,1],'role':'cover'}]}))
    work = tmp_path/'capture'
    work.mkdir()
    snapshot,route = capture(manifest,work)
    with ZipFile(snapshot) as archive:
        members = json.loads(archive.read('manifest.json'))['members']
        assert len([name for name in archive.namelist() if name.startswith('blobs/')])==2
        assert members[source.name]['sha256']==filehash(source)
    result = inspect_source(manifest)
    assert [row['frame'] for row in result.inventory]==[1,0,1]
    assert result.inventory[0]['image_transform']['crop_pixels']==[0,0,125,150]
    assert result.inventory[0]['occurrence']=='first' and result.inventory[2]['occurrence']=='repeat'


def test_zip_dispatch_requires_supported_payload_and_chooses_distinct_books(tmp_path):
    from audiobook.core import Error
    import pytest

    filename = tmp_path/'any.zip'
    with ZipFile(filename,'w') as archive:
        archive.writestr('folder/first.txt','Words in reading order.')
        archive.writestr('folder/second.html','<p>Other publication.</p>')
        archive.writestr('readme.bin',b'\x01')
    with pytest.raises(Error) as exc:
        describe(filename)
    assert exc.value.code=='source_selection'
    assert [item['id'] for item in exc.value.candidates]==['1','2']
    doc = inspect_source(filename,entry='1')
    assert doc.metadata['archive_entry']['name']=='folder/first.txt'
    assert doc.source_sha256==filehash(filename) and doc.blocks[0].source=='Words in reading order.'
    with ZipFile(filename,'w') as archive:
        archive.writestr('unsupported.bin',b'\x00\x01')
    with pytest.raises(Error) as exc:
        describe(filename)
    assert exc.value.code=='unsupported_format'


def test_zip_tar_routes_preserve_original_member_order_and_exact_bytes(tmp_path):
    source = tmp_path/'collected.tar.gz'
    data = b'<html><body><h1>One chapter</h1><p>Exact original words.</p></body></html>'
    with tarfile.open(source,'w:gz') as archive:
        info = tarfile.TarInfo('book.html')
        info.size = len(data)
        archive.addfile(info,io.BytesIO(data))
    doc = inspect_source(source)
    destination = tmp_path/'moved'
    destination.mkdir()
    original = destination/'original.gz'
    original.write_bytes(source.read_bytes())
    location = doc.blocks[1].locator
    (destination/'companion.json').write_text(json.dumps({'original_source':original.name,
        'source_sha256':filehash(original),'source_kind':'tar-member',
        'source_locations':{location['location_id']:location}}))
    copied = tmp_path/'original.html'
    extract_location(destination,location['location_id'],copied)
    assert copied.read_bytes()==data


def test_modern_native_readers_preserve_notes_headers_and_revisions(tmp_path):
    from audiobook.planner import plan

    fn = tmp_path/'an edition.docx'
    word_ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    with ZipFile(fn,'w') as book:
        book.writestr('word/document.xml',f'<w:document xmlns:w="{word_ns}"><w:body>'
          '<w:p><w:r><w:t>Visible prose.</w:t></w:r><w:del><w:r><w:delText>Deleted.</w:delText></w:r></w:del>'
          '<w:ins><w:r><w:t xml:space="preserve"> Added.</w:t></w:r></w:ins>'
          '<w:r><w:rPr><w:vanish/></w:rPr><w:t> Hidden.</w:t></w:r>'
          '<w:r><w:footnoteReference w:id="2"/></w:r></w:p>'
          '<w:tbl><w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc><w:p><w:r><w:t>Kind</w:t></w:r></w:p></w:tc></w:tr>'
          '<w:tr><w:tc><w:p><w:r><w:t>Oak</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        book.writestr('word/footnotes.xml',f'<w:footnotes xmlns:w="{word_ns}"><w:footnote w:id="2">'
          '<w:p><w:r><w:t>Author footnote.</w:t></w:r></w:p></w:footnote></w:footnotes>')
    doc = inspect_source(fn)
    assert [item.source for item in doc.blocks]==['Visible prose. Added.','Kind\nOak','Author footnote.']
    assert doc.blocks[1].locator['table_header_cells']==[0]
    result = plan(doc)
    assert 'Author footnote.' in ' '.join(unit['text'] for unit in result['utterances'])
    rtf = tmp_path/'root.rtf'
    rtf.write_bytes(br'{\rtf1\ansi\ansicpg1252\uc1 Plain caf\u233? {\v secret text} visible.\par Next paragraph.}')
    control = inspect_source(rtf)
    assert [item.source for item in control.blocks]==['Plain café  visible.','Next paragraph.']
