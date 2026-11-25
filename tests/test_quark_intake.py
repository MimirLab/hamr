from struct import pack, pack_into
from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import inspect_source
from audiobook.planner import plan


def publication(text=b'The keeper walked down the quiet path.', *, version=0x3c, split=23,
                orphan=False, figure=False):
    header = bytearray(512)
    header[2:8] = b'MMXPR3'
    pack_into('>HH',header,8,version,version)
    pack_into('>H',header,64,2)
    seed = 0x12
    pack_into('>HH',header,272,seed,3)
    data = header+bytearray(256*22)

    def field(value):
        return pack('>I',len(value))+value

    def box(kind,ref,start=0,successor=0,link=44):
        nonlocal seed
        top = 220 if kind==12 else 40
        shape = (pack('>BBII',kind^(seed&255),1,65536,ref^seed)+b'\0'*10
                 +pack('>I',link)+b'\0'*10+pack('>iiii',top*65536,60*65536,500*65536,300*65536))
        seed = (seed+3)&65535
        if kind==12:
            return shape+b'\0'*96
        body = b'\0'*18+pack('>I',start)+b'\0'*42+pack('>I',successor)+b'\0'*8
        return shape+body+(b'\0'*12 if not start else b'')

    def page(*objects):
        dimensions = pack('>iiii',0,0,792*65536,612*65536)
        return (b'\0'*6+pack('>H',1)+b'\0'*2+dimensions+b'\0'*44
                +field(b'')*5+pack('>I',len(objects))+b''.join(objects))

    def put_chain(block,value):
        chunks = [value[i:i+252] for i in range(0,len(value),252)]
        for i,chunk in enumerate(chunks):
            offset = (block+i-1)*256
            data[offset:offset+len(chunk)] = chunk
            pack_into('>i',data,offset+252,0 if i+1==len(chunks) else block+i+1)

    formats = bytearray(46)
    pack_into('>hH',formats,2,0,0)
    first = page(box(3,16,successor=99 if orphan else 1))
    continuation = box(3,1,start=split)
    pictures = [box(12,22)] if figure else []
    second = page(continuation,*pictures)
    put_chain(3,field(b'')*10+field(formats)+field(b'')*2+first+second)
    context = pack('>IH',20,split)+pack('>IH',21,len(text)-split)
    spans = field(pack('>HI',0,len(text)))
    put_chain(16,pack('>I',len(text))+field(context)+spans+spans)
    data[19*256:19*256+split] = text[:split]
    data[20*256:20*256+len(text)-split] = text[split:]
    return bytes(data)


def test_quark_cross_page_story_has_one_reading_and_exact_locations(tmp_path):
    folder = tmp_path/'book'
    folder.mkdir()
    path = folder/'Chapter'
    path.write_bytes(publication())
    archive = tmp_path/'book.zip'
    with ZipFile(archive,'w') as stored:
        stored.write(path,'Book/Chapter.qxd')
        stored.writestr('unrelated.qxd','Output package name and list of fonts')
    for candidate in path,folder,archive:
        doc = inspect_source(candidate,offline=True)
        assert doc.format=='quark'
        assert doc.metadata['quark_pages']==2
        result = plan(doc)
        assert not result['issues']
        assert [item['text'] for item in result['utterances']]==['The keeper walked down the quiet path.']
        block, = doc.blocks
        assert block.locator['quark_pages']==[1,2]
        size = len(block.source.encode('mac_roman'))
        assert block.locator['quark_byte_ranges']==[[4864,4887],[5120,5120+size-23]]
        assert block.locator['quark_story_block']==16
    assert block.locator['member']=='Book/Chapter.qxd'


def test_quark_has_typed_version_bounds_and_story_review(tmp_path):
    path = tmp_path/'book.qxd'
    path.write_bytes(publication(orphan=True))
    result = plan(inspect_source(path,offline=True))
    assert not result['utterances']
    assert any('unresolved linked frame' in row['reason'] for row in result['issues'])
    assert any('no verified story placement' in row['reason'] for row in result['issues'])
    original = bytearray(publication(figure=True))
    path.write_bytes(original)
    result = plan(inspect_source(path))
    assert len(result['utterances'])==1
    assert [row['reason'] for row in result['issues']]==[
        'This QuarkXPress illustration has no saved accessible description.',
    ]
    pack_into('>i',original,764,3)
    path.write_bytes(original)
    with pytest.raises(Error) as cyclic:
        inspect_source(path)
    assert cyclic.value.code=='source_quark_records'
    path.write_bytes(publication(version=0x41))
    with pytest.raises(Error) as too_new:
        inspect_source(path)
    assert too_new.value.code=='source_quark_version'
