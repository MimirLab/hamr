from gzip import compress
from io import BytesIO
import plistlib
import subprocess
from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import inspect_source
from audiobook.source_bundle import capture, describe


PAGES_XML = b'''<sl:document xmlns:sl="http://developer.apple.com/namespaces/sl"
    xmlns:sf="http://developer.apple.com/namespaces/sf" sl:version="92008102400">
  <sf:text-storage><sf:text-body><sf:section><sf:layout>
    <sf:p>The evening train passed the river.</sf:p>
  </sf:layout></sf:section></sf:text-body></sf:text-storage>
</sl:document>'''


def write_package(path, files):
    with ZipFile(path,'w') as archive:
        for member,payload in files.items():
            archive.writestr(member,payload)


def test_pages_package_keeps_identity_and_excludes_preview_from_candidates(tmp_path):
    book = tmp_path/'book.zip'
    write_package(book,{'River.pages/QuickLook/Thumbnail.jpg':b'thumbnail',
                        'River.pages/index.xml.gz':compress(PAGES_XML),
                        'River.pages/QuickLook/Preview.pdf':b'preview',
                        'Another.txt':b'Another source.'})
    with pytest.raises(Error) as selected:
        describe(book)
    assert selected.value.code=='source_selection'
    assert selected.value.candidates==[{'id':'4','name':'Another.txt'},{'id':'bundle:2','name':'River'}]
    source = describe(book,entry='bundle:2')
    assert (source.route,source.kind,source.entry)==('pages','zip',2)


def test_flat_iwa_and_native_index_directory_are_single_publications(tmp_path):
    properties = plistlib.dumps({'isMultiPage':True})
    records = {'Index/Document.iwa':b'example', 'Metadata/Properties.plist':properties, 'preview.jpg':b'preview'}
    book = tmp_path/'book.zip'
    write_package(book,records)
    assert describe(book).route=='pages'
    folder = tmp_path/'Unpacked.pages'
    folder.mkdir()
    index = BytesIO()
    write_package(index,{'Index/Document.iwa':b'iwa data'})
    (folder/'Index.zip').write_bytes(index.getvalue())
    (folder/'preview.jpg').write_bytes(b'preview')
    snapshot_folder = tmp_path/'snapshot'
    snapshot_folder.mkdir()
    snapshot,source = capture(folder,snapshot_folder)
    with ZipFile(snapshot) as archive:
        assert archive.read('Index.zip')==index.getvalue()
        assert archive.read('preview.jpg')==b'preview'
    assert (source.route,source.kind)==('pages','zip')


def test_pages_preview_or_native_encryption_cannot_become_a_scan(tmp_path):
    book = tmp_path/'Preview.pages'
    write_package(book,{'preview.jpg':b'preview'})
    with pytest.raises(Error) as missing:
        inspect_source(book)
    assert missing.value.code=='source_pages_package'
    write_package(book,{'index.xml':PAGES_XML})
    source = bytearray(book.read_bytes())
    directory = source.index(b'PK\x01\x02')
    method = (0x636c).to_bytes(2,'little')
    source[8:10] = source[directory+10:directory+12] = method
    book.write_bytes(source)
    with pytest.raises(Error) as protected:
        inspect_source(book)
    assert protected.value.code=='protected_source'


def test_duplicate_pages_documents_and_nested_index_members_are_rejected(tmp_path):
    book = tmp_path/'Document.pages'
    write_package(book,{'index.xml':PAGES_XML,'index.xml.gz':compress(PAGES_XML)})
    with pytest.raises(Error) as competing:
        inspect_source(book)
    assert competing.value.code=='source_pages_package'
    index = BytesIO()
    write_package(index,{'../Index/Document.iwa':b'unexpected relative path'})
    write_package(book,{'Index.zip':index.getvalue()})
    with pytest.raises(Error) as invalid:
        inspect_source(book)
    assert invalid.value.code=='unsafe_source_member'


def test_resident_pages_worker_reads_an_xml_document(tmp_path):
    from audiobook.legacy_office_intake import resident

    binary = resident()
    if binary is None:
        pytest.skip('The resident Pages worker is available on Linux x86-64.')
    source = tmp_path/'River.pages'
    write_package(source,{'index.xml':PAGES_XML})
    derivative = tmp_path/'River.fodt'
    subprocess.run([binary,'--pages',source,derivative],check=True,timeout=30)
    document = inspect_source(derivative,offline=True)
    assert [block.source for block in document.blocks]==['The evening train passed the river.']
