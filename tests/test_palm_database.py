import io
import json
from struct import pack, pack_into
from zipfile import ZipFile
import zlib

from PIL import Image
import pytest

from audiobook.core import Error
from audiobook.intake import extract_location, inspect_source
from audiobook.planner import plan


def database(signature,records):
    header = bytearray(78)
    header[:10] = b'Harbor log'
    header[60:68] = signature
    pack_into('>H',header,76,len(records))
    offset = len(header)+8*len(records)+2
    directory = []
    for number,record in enumerate(records,1):
        directory.append(pack('>II',offset,number))
        offset += len(record)
    return bytes(header)+b''.join(directory)+b'\0\0'+b''.join(records)


def palmdoc(records,*,compression=1,length=None,position=0):
    declared = sum(map(len,records)) if length is None else length
    header = pack('>HHIHHI',compression,0,declared,len(records),4096,position)
    return database(b'TEXtREAd',[header,*records])


def ereader_header(non_text,*,compression=10,images=(0,0),footnotes=(0,0),sidebars=(0,0)):
    header = bytearray(132)
    for offset,value in ((0,compression),(6,25152),(12,non_text),(20,images[1]),(28,footnotes[1]),
                         (30,sidebars[1]),(34,2560),(40,images[0]),(48,footnotes[0]),(50,sidebars[0])):
        pack_into('>H',header,offset,value)
    return bytes(header)


def test_bookmark_does_not_change_plain_text_or_archive_intake(tmp_path):
    raw = b'The harbour was quiet. It\x92s late.\r\n\r\nThe last lamp went out.\r\n'
    chunks = [raw[:12],raw[12:]]
    source = tmp_path/'log.pdb'
    source.write_bytes(palmdoc(chunks,position=65536))
    document = inspect_source(source)
    assert document.format=='palmdoc' and document.inventory[-1]['stored_position']==65536
    assert document.blocks[0].locator['palm_records']==[1,2]
    assert document.blocks[1].locator['palm_records']==[2]
    plain = tmp_path/'log.txt'
    plain.write_bytes(raw)
    expected = [item['text'] for item in plan(inspect_source(plain,encoding='cp1252'))['utterances']]
    pack_path = tmp_path/'collection.zip'
    with ZipFile(pack_path,'w') as archive:
        archive.writestr('Shelf/Log.prc',source.read_bytes())
        archive.writestr('Downloader.prc',b'Palm application')
    for edition in (document,inspect_source(pack_path)):
        assert [item['text'] for item in plan(edition)['utterances']]==expected
    assert inspect_source(pack_path).blocks[0].locator['source_file']=='Shelf/Log.prc'


def test_compressed_palmdoc_literals_overlap_and_record_split(tmp_path):
    # One high byte run, one space pair, a back-reference and a split UTF-8 character.
    head, tail = b'Waves. \xe2\x80',b'\x99 AAAAAA.\r\n'
    first = b'Waves. \x02\xe2\x80'
    second = b'\x01\x99\xc1\x80\x0a.\r\n'
    source = tmp_path/'control.prc'
    source.write_bytes(palmdoc([first,second],compression=2,length=len(head+tail)))
    result = inspect_source(source,encoding='utf-8')
    assert result.blocks[0].source=='Waves. ’ AAAAAA.'
    assert result.blocks[0].locator['palm_records']==[1,2]
    source.write_bytes(palmdoc([first,b'\x80\x0a'],compression=2,length=len(head+tail)))
    with pytest.raises(Error,match='record 2.*outside'):
        inspect_source(source,encoding='utf-8')


def test_ereader_uses_binary_notes_and_recovers_named_artwork(tmp_path):
    buf = io.BytesIO()
    Image.new('RGB',(8,8),'navy').save(buf,format='PNG')
    png = buf.getvalue()
    record = b'PNG '+b'cover.png'.ljust(32,b'\x00')+b'\x00'*22+pack('>HH',8,8)+png
    source = tmp_path/'Ebook.pdb'
    first = b'\\xThe evening watch\\x\r\n\\m="cover.png"\r\n'
    second = b'The lamp stayed lit.\\Fn="wind"1\\Fn\r\n'
    header = ereader_header(3,images=(3,1),footnotes=(4,2))
    source.write_bytes(database(b'PNRdPPrs',[header,zlib.compress(first),zlib.compress(second),record,
                                             b'\0\1\x04wind\0',zlib.compress(b'The wind had stopped.')]))
    book = tmp_path/'moved'
    book.mkdir()
    original = book/'original.zip'
    with ZipFile(original,'w') as archive:
        archive.write(source,'Edition/Ebook.pdb')
    document = inspect_source(original)
    note, = [item for item in document.blocks if item.role=='note']
    assert note.locator['palm_records']==[5] and note.locator['exact']=='The wind had stopped.'
    assert any('The wind had stopped.' in utterance['text'] for utterance in plan(document)['utterances'])
    cover, = [item for item in document.metadata['source_resources'] if item['role']=='asset']
    assert cover['resource_label']=='cover.png'
    assert all(item['resource_label'] for item in document.metadata['source_resources'])
    identity = cover['location_id']
    companion = {'original_source':original.name,'source_kind':'zip-member','source_sha256':document.source_sha256,
                 'source_locations':{identity:cover}}
    (book/'companion.json').write_text(json.dumps(companion))
    extracted = tmp_path/'cover.png'
    extract_location(book,identity,extracted)
    assert extracted.read_bytes()==png


def test_old_encrypted_and_unrelated_palm_records_get_distinct_errors(tmp_path):
    path = tmp_path/'Ebook.pdb'
    path.write_bytes(database(b'PNRdPPrs',[ereader_header(2,compression=260),b'unreadable']))
    with pytest.raises(Error) as encrypted:
        inspect_source(path)
    assert encrypted.value.code=='protected_source'
    path.write_bytes(database(b'applTest',[b'x',b'y']))
    with pytest.raises(Error) as unrelated:
        inspect_source(path)
    assert unrelated.value.code=='unsupported_format'
