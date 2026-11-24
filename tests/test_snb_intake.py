import bz2
from io import BytesIO
from struct import pack
from zipfile import ZipFile
import zlib

import pytest

from audiobook.core import Error
from audiobook.intake import inspect_source
from audiobook.planner import plan
from audiobook.snb_container import Package, recover


def snb(files, *, raw_first=False, bad_pointer=False):
    size = 32768
    data = {True:bytearray(), False:bytearray()}
    pointers, entries, names = [], [], bytearray()
    for name,content in files:
        text = name.endswith(('.snbf','.snbc'))
        pointers.append((text,len(data[text])))
        entries.append(pack('>III',0x41000000 if text else 0x1000000,len(names),len(content)))
        names.extend(name.encode()+b'\0')
        data[text].extend(content)
    directory = b''.join(entries)+names
    compressed = zlib.compress(directory)
    binary, text = data[False],data[True]
    header = pack('>8s9I',b'SNBP000B',0x8000,0xa3a3a3,0,len(files),len(directory),
                  len(compressed),len(binary),len(text),0)
    start = len(header)+len(compressed)
    stored = bytearray(header+compressed+binary)
    blocks = [start+i for i in range(0,len(binary),size)]
    binary_blocks = len(blocks)
    for i in range(0,len(text),size):
        blocks.append(len(stored))
        block = text[i:i+size]
        stored.extend(block if raw_first and i==0 else bz2.compress(block))
    tail = b''.join(pack('>I',start) for start in blocks)
    for index,(text,offset) in enumerate(pointers):
        block,within = divmod(offset,size)
        if bad_pointer and index==0:
            within += 1
        tail += pack('>II',block+(binary_blocks if text else 0),within)
    compressed_tail = zlib.compress(tail)
    return bytes(stored)+compressed_tail+pack('>II8s',len(compressed_tail),len(stored),b'SNBP000B')


def test_snb_uses_authored_order_and_keeps_missing_occurrence_in_scope(tmp_path):
    earlier = b'<snbc><head><title>Arrival</title><hidetitle>true</hidetitle></head><body><text>The boat reached shore.</text></body></snbc>'
    later = b'<snbc><head><title>Home</title></head><body><text>The lamp was burning.</text></body></snbc>'
    contents = b'<toc-snbf><body><chapter src="z.snbc">Arrival</chapter><chapter src="missing.snbc">Gap</chapter><chapter src="a.snbc">Home</chapter></body></toc-snbf>'
    raw = snb([('snbc/a.snbc',later),('snbc/z.snbc',earlier),('snbf/toc.snbf',contents)])
    source = tmp_path/'book.snb'
    source.write_bytes(raw)
    archive = tmp_path/'shelf.zip'
    with ZipFile(archive,'w') as z:
        z.writestr('Independent shelf/Book.snb',raw)
    for path in (source,archive):
        doc = inspect_source(path)
        result = plan(doc)
        assert [item['text'] for item in result['utterances']]==[
            'The boat reached shore.','Home','The lamp was burning.']
        assert [row['label'] for row in doc.metadata['snb_contents']]==['Arrival','Gap','Home']
        issue, = result['issues']
        assert issue['location']['path']=='snbf/toc.snbf'
        assert 'chapter Gap has no readable member' in issue['reason']


@pytest.mark.parametrize('raw_first',[False,True])
def test_snb_recovers_member_and_accent_across_storage_blocks(tmp_path,raw_first):
    text = b' ' * 32767 + 'é'.encode()
    files = [('snbc/chapter.snbc',text),('snbc/images/cover.png',b'original image')]
    raw = snb(files,raw_first=raw_first)
    package = Package(BytesIO(raw))
    member = package.by_name['snbc/chapter.snbc']
    assert package.read(member)==text
    source = tmp_path/'package.snb'
    source.write_bytes(raw)
    output = tmp_path/'chapter.snbc'
    recover(source,output,member.identity())
    assert output.read_bytes()==text
    with pytest.raises(Error,match='invalid byte range'):
        Package(BytesIO(snb(files,bad_pointer=True)))
