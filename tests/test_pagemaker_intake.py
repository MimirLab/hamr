from struct import pack_into
from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import inspect_source
from audiobook.planner import plan
from audiobook.source_bundle import describe


def publication(first=b'The keeper walked down the quiet path.', *, split=27, repeat=False,
                font=b'Times New Roman', figure=False, version=0x0632):
    data = bytearray(0x400)
    data[6:8] = b'\xff\x99'
    data[0x6c:0x6e] = b'MP'
    pack_into('<H',data,0x6e,version)
    arrays = []

    def store(kind,stride,rows):
        assert len(rows)%stride==0
        sequence = len(arrays)
        arrays.append((kind,len(rows)//stride,len(data)))
        data.extend(rows)
        return sequence

    def shapes(*items):
        stride = 258 if version==0x0632 else 136
        rows = bytearray(len(items)*stride)
        for i,(story,frame) in enumerate(items):
            offset = i*stride
            rows[offset] = 1 if story else 6
            pack_into('<hhhh',rows,offset+6,24,i*100+50,600,i*100+120)
            pack_into('<II',rows,offset+32,story,frame)
        return store(25,stride,rows)

    left = shapes((7,31))
    extras = ([(8,41)] if repeat else []) + ([(0,0)] if figure else [])
    right = shapes((7,32),*extras)
    pages = bytearray(2*472)
    pack_into('<H',pages,2,left)
    pack_into('<H',pages,474,right)
    store(5,472,pages)
    store(19,94,font.ljust(94,b'\0'))

    def story(ident,frames,text):
        frame_table = bytearray(len(frames)*24)
        line_table = bytearray(len(frames)*16)
        for i,(frame,size) in enumerate(frames):
            pack_into('<IH',frame_table,i*24,size,1)
            pack_into('<I',frame_table,i*24+20,frame)
            pack_into('<H',line_table,i*16,size)
        frame_sequence = store(27,24,frame_table)
        lines = store(9,16,line_table)
        words = store(13,1,text)
        style = bytearray(30)
        pack_into('<H',style,0,len(text))
        run = store(28,30,style)
        info = bytearray(36)
        pack_into('<HHHH',info,0,frame_sequence,lines,words,run)
        pack_into('<I',info,32,ident)
        return info

    stories = story(7,[(31,split),(32,len(first)-split)],first)
    if repeat:
        stories += story(8,[(41,len(first))],first)
    store(26,36,stories)
    pack_into('<HI',data,0x2e,len(arrays),0x100)
    for i,(kind,count,offset) in enumerate(arrays):
        pack_into('<BBHI',data,0x100+16*i,0,kind,count,offset)
    return bytes(data)


def compound(payload, stream_name='PageMaker'):
    size = max(4096,(len(payload)+511)//512*512)
    data = bytearray(size+1536)
    data[:8] = bytes.fromhex('d0cf11e0a1b11ae1')
    pack_into('<HHHHH',data,24,0x3e,3,0xfffe,9,6)
    pack_into('<9I',data,40,0,1,0,0,4096,0xfffffffe,0,0xfffffffe,0)
    pack_into('<109I',data,76,1,*([0xffffffff]*108))
    for offset,name,kind,child,start,length in (
            (512,'Root Entry',5,1,0xfffffffe,0),(640,stream_name,2,0xffffffff,2,size)):
        label = name.encode('utf-16-le')+b'\0\0'
        data[offset:offset+len(label)] = label
        pack_into('<HBBIII',data,offset+64,len(label),kind,1,0xffffffff,0xffffffff,child)
        pack_into('<IQ',data,offset+116,start,length)
    sectors = size//512
    chain = [0xfffffffe,0xfffffffd,*range(3,2+sectors),0xfffffffe]
    pack_into('<128I',data,1024,*chain,*([0xffffffff]*(128-len(chain))))
    data[1536:1536+len(payload)] = payload
    return bytes(data)


def test_pagemaker_saved_ranges_join_a_story_and_retain_an_authored_repeat(tmp_path):
    path = tmp_path/'Manuscript.pmd'
    path.write_bytes(publication(repeat=True))
    doc = inspect_source(path,offline=True)
    assert [(item.role,item.source) for item in doc.blocks]==[
        ('body','The keeper walked down the quiet path.'),
        ('body','The keeper walked down the quiet path.'),
    ]
    first,second = doc.blocks
    assert first.locator['pagemaker_pages']==[1,2]
    assert second.locator['pagemaker_pages']==[2]
    assert first.locator['pagemaker_story']==7 and second.locator['pagemaker_story']==8
    assert first.locator['pagemaker_byte_ranges']!=second.locator['pagemaker_byte_ranges']
    assert not plan(doc)['issues']


def test_pagemaker_compound_file_folder_and_zip_follow_same_reader(tmp_path):
    folder = tmp_path/'Publication'
    folder.mkdir()
    path = folder/'chapter.pm6'
    path.write_bytes(compound(publication(version=0x0600)))
    archive = tmp_path/'Publication.zip'
    with ZipFile(archive,'w') as stored:
        stored.write(path,'Book/chapter.pm6')
        stored.writestr('unrelated.pmd',compound(publication(),stream_name='Not PageMaker'))
    for candidate in (path,folder,archive):
        doc = inspect_source(candidate,offline=True)
        assert doc.format=='pagemaker'
        assert doc.metadata['pagemaker_record_version']=='0x0600'
        assert [item['text'] for item in plan(doc)['utterances']]==['The keeper walked down the quiet path.']
    assert doc.blocks[0].locator['member']=='Book/chapter.pm6'
    assert doc.blocks[0].locator['source_file']=='Book/chapter.pm6'


def test_pagemaker_surfaces_glyph_mapping_and_refuses_an_unrelated_signature(tmp_path):
    path = tmp_path/'text.pmd'
    path.write_bytes(publication(font=b'ML-TTAathira'))
    result = plan(inspect_source(path))
    assert not result['utterances']
    assert 'legacy font mapping' in result['issues'][0]['reason']
    path.write_bytes(b'Pmd'+b'\x00'*128)
    with pytest.raises(Error) as invalid:
        describe(path)
    assert invalid.value.code=='source_pagemaker_signature'
    path.write_bytes(publication()[:0x430])
    with pytest.raises(Error) as truncated:
        inspect_source(path)
    assert truncated.value.code=='source_pagemaker_records'
