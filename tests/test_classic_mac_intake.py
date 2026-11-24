import base64
from binascii import crc_hqx
import io
import struct
import tarfile
from zipfile import ZipFile

import pytest

from audiobook.classic_mac_intake import BASE64_ALPHABET, BINHEX, BINHEX_ALPHABET, fork_metadata, verified_binhex
from audiobook.core import Error
from audiobook.source_bundle import capture, describe


def apple_double(finder=b'TEXTNISI',resource=b'original Macintosh resource data'):
    finder += b'\0'*24
    header = struct.pack('>II16sH',0x51607,0x20000,b'',2)
    entries = struct.pack('>IIIIII',2,50,len(resource),9,50+len(resource),len(finder))
    return header+entries+resource+finder


def test_mac_pair_finder_selects_a_distinct_source_and_preserves_both_bytes(tmp_path):
    doc = tmp_path/'Nisus'
    sidecar = tmp_path/'._Nisus'
    data = b'Story data as its original Macintosh byte stream.'
    resource = apple_double()
    doc.write_bytes(data)
    sidecar.write_bytes(resource)
    assert describe(doc).route=='classic-mac'
    assert describe(tmp_path).route=='classic-mac'
    captured = tmp_path/'capture'
    captured.mkdir()
    snapshot,source = capture(doc,captured)
    assert (source.route,source.kind,source.entry)==('classic-mac','zip',2)
    doc.write_bytes(b'edited')
    sidecar.write_bytes(b'edited')
    with ZipFile(snapshot) as original:
        assert original.read('Nisus')==data and original.read('._Nisus')==resource


def test_mac_archive_preserves_traditional_sidecar_path_without_a_separate_choice(tmp_path):
    for format in ('zip','tar'):
        path = tmp_path/f'archive.{format}'
        files = {'books/Nisus':b'data','__MACOSX/books/._Nisus':apple_double()}
        if format=='zip':
            with ZipFile(path,'w') as archive:
                for name,data in files.items():
                    archive.writestr(name,data)
        else:
            with tarfile.open(path,'w') as archive:
                for name,data in files.items():
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    archive.addfile(entry,io.BytesIO(data))
        source = describe(path)
        assert (source.route,source.kind,source.entry)==('classic-mac',format,1)


def test_ordinary_text_with_unrelated_mac_metadata_stays_text(tmp_path):
    path = tmp_path/'Book.zip'
    with ZipFile(path,'w') as archive:
        archive.writestr('Book.txt','An ordinary text file.')
        archive.writestr('__MACOSX/._Book.txt',apple_double(b'TEXTttxt'))
    assert describe(path).route=='text'


def test_appledouble_entries_cannot_overlap_or_claim_the_data_fork():
    resource = bytearray(apple_double())
    struct.pack_into('>I',resource,42,51)
    with pytest.raises(Error,match='overlap'):
        fork_metadata(resource)
    resource = bytearray(apple_double())
    struct.pack_into('>I',resource,26,1)
    with pytest.raises(Error,match='invalid fork range'):
        fork_metadata(resource)


def test_binhex_checks_every_fork_and_normalizes_transport_line_endings():
    data,resource = b'prose',b'fork'
    header = b'\x01N\0TEXTNISI'+struct.pack('>HII',0,len(data),len(resource))
    payload = b''.join(chunk+struct.pack('>H',crc_hqx(chunk,0)) for chunk in (header,data,resource))

    def transport(content):
        encoded = base64.b64encode(content.replace(b'\x90',b'\x90\0')).rstrip(b'=')
        encoded = encoded.translate(bytes.maketrans(BASE64_ALPHABET,BINHEX_ALPHABET))
        return BINHEX+b'\r\n:'+encoded+b':\r\n'

    assert verified_binhex(transport(payload))==transport(payload).replace(b'\r\n',b'\n')
    for target in (b'TEXT',data,resource):
        with pytest.raises(Error,match='checksum'):
            verified_binhex(transport(payload.replace(target,b'X'+target[1:])))
