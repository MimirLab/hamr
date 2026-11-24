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


def ztxt(records,length,*,version=0x012c,record_size=8192,flags=0,crc=None,marks=(),notes=()):
    first = len(records)+1
    bookmark_record = b''.join(pack('>I20s',*mark) for mark in marks)
    note_index = b''.join(pack('>I20s',offset,title) for offset,title,_ in notes)
    annotations = [note_index,*(body for _,_,body in notes)] if notes else []
    checksum = zlib.crc32(b''.join(records)) if crc is None else crc
    header = pack('>HHI5HBBI8x',version,len(records),length,record_size,
                  len(marks),first if marks else 0,len(notes),first+bool(marks) if notes else 0,flags,0,checksum)
    extras = ([bookmark_record] if marks else [])+annotations
    return database(b'zTXTGPlm',[header,*records,*extras])


def test_ztxt_preserves_shared_text_and_annotation_assets(tmp_path):
    raw = 'The harbor was quiet. It’s late.\r\n\r\nThe captain came home.'.encode()
    first = raw.index('’'.encode())+2
    encoder = zlib.compressobj()
    records = [encoder.compress(chunk)+encoder.flush(zlib.Z_FULL_FLUSH)
               for chunk in (raw[:first],raw[first:])]
    original = ztxt(records,len(raw),flags=3,marks=[(0,b'Beginning')],
                   notes=[(first+1,b'Private reader note',b'Keep this with the source.\0')])
    file = tmp_path/'vigil.prc'
    file.write_bytes(original)
    plain = tmp_path/'vigil.txt'
    plain.write_bytes(raw)
    archive_file = tmp_path/'shelf.zip'
    with ZipFile(archive_file,'w') as archive:
        archive.writestr('Books/Vigil.pdb',original)
    expected = [row['text'] for row in plan(inspect_source(plain))['utterances']]
    for path in (file,archive_file):
        doc = inspect_source(path,encoding='utf-8')
        assert doc.format=='ztxt'
        assert [row['text'] for row in plan(doc)['utterances']]==expected
        assert doc.blocks[0].locator['palm_records']==[1,2]
        assert doc.blocks[1].locator['palm_records']==[2]
        marks = doc.metadata['ztxt_reader_marks']
        assert marks['bookmarks']==[{'title':'Beginning','text_byte_offset':0}]
        note, = marks['annotations']
        assert note['text_byte_offset']==first+1
        asset = next(row for row in doc.metadata['source_resources'] if row['location_id']==note['location_id'])
        folder = tmp_path/path.suffix[1:]
        folder.mkdir()
        (folder/path.name).write_bytes(path.read_bytes())
        companion = {'original_source':path.name,'source_sha256':doc.source_sha256,
                     'source_kind':'zip-member' if path==archive_file else 'file',
                     'source_locations':{note['location_id']:asset}}
        (folder/'companion.json').write_text(json.dumps(companion))
        extracted = folder/'note.txt'
        extract_location(folder,note['location_id'],extracted)
        assert extracted.read_bytes()==b'Keep this with the source.\0'


def test_ztxt_validates_both_stream_endings_and_integrity(tmp_path):
    raw = b'The lighthouse stood watch. The crew was returning safely.'
    data = zlib.compress(raw)
    source = tmp_path/'test.pdb'
    for version in (0x010a,0x012c):
        source.write_bytes(ztxt([data[:12],data[12:]],len(raw),version=version))
        assert inspect_source(source).blocks[0].locator['palm_records']==[1,2]
    source.write_bytes(ztxt([data[:-1]],len(raw)))
    with pytest.raises(Error,match='stream ending'):
        inspect_source(source)
    source.write_bytes(ztxt([data],len(raw),crc=1))
    with pytest.raises(Error,match='its CRC'):
        inspect_source(source)
    source.write_bytes(ztxt([data],len(raw),flags=1))
    with pytest.raises(Error,match='full-flush boundary'):
        inspect_source(source)


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
