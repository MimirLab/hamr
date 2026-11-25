from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import inspect_source
from audiobook.planner import plan


def record(kind,payload):
    size = (len(payload)+4).to_bytes(2,'little')
    return b'\x1d'+size+bytes([kind])+payload+size+b'\x1d'


def manuscript(body,styles=()):
    end = 128+len(body)+1
    catalogue = (end+127)//128*128 if styles else 0
    header = record(0,b'\x70'+b'\0'*11+catalogue.to_bytes(4,'little')+b'\0'*105)
    assert len(header)==128
    if not styles:
        return header+body+b'\x1a'
    names = b''.join(name.encode('cp437').ljust(24,b' ')+b'\0'*9 for name in styles)
    index = (b'\x1a\x55\x01\0\x01'+len(styles).to_bytes(2,'little')+b'\x66\0\x0d\0\0\0'
             +bytes([len(styles)])+b'\0'*4+names)
    return header+body+b'\x1a'*(catalogue-end+1)+index


def selected(number):
    return record(0x11,bytes([number,2])+b'\0'*6)


def spoken(document):
    return plan(document)['utterances']


def test_wordstar_identifies_source_order_styles_and_soft_returns(tmp_path):
    source = tmp_path/'Manuscript'
    source.write_bytes(manuscript(
        selected(0)+b'Before dawn\r\x8a'+selected(1)+b'Watch the light over the obser\x1f\x8d\x8avatory.'
        +selected(2)+b' Its shutters rattled.\r\n'+b'\x1b\xd5\x1c inside the door.\r\n'
        +b'.pa'+record(0x0b,b'\r\n')+b'\r\x8a',('H1','MS Body Copy','Double-Indented Quote')))
    book = inspect_source(source,offline=True)
    assert [(part.role,part.source) for part in book.blocks]==[
        ('heading','Before dawn'),
        ('body','Watch the light over the observatory. Its shutters rattled.'),
        ('body','╒ inside the door.'),
        ('furniture','.pa'),
    ]
    assert book.blocks[1].locator['wordstar_style']=='MS Body Copy'
    assert book.blocks[2].locator['block_quote']
    assert 'observatory' in ' '.join(unit['text'] for unit in spoken(book))
    archive = tmp_path/'Evening.zip'
    with ZipFile(archive,'w') as container:
        container.write(source,'Draft/Evening.WS')
    wrapped = inspect_source(archive,offline=True)
    assert [unit['text'] for unit in spoken(book)]==[unit['text'] for unit in spoken(wrapped)]
    assert wrapped.blocks[1].locator['member']=='Draft/Evening.WS'
    assert wrapped.blocks[1].locator['binary_asset']==book.blocks[1].locator['binary_asset']


def test_wordstar_notes_use_shared_reading_and_exact_bytes(tmp_path):
    source = tmp_path/'Draft.ws'
    note = record(3,b'\x01\0\x01\0\0The keeper heard a bell.')
    comment = record(6,b'\x01\0\0\0\0The writer plans another ending.')
    source.write_bytes(manuscript(b'At noon it rained'+note+b'.'+comment+b'\r\nThe bell was quiet.'))
    document = inspect_source(source,offline=True)
    text = ' '.join(unit['text'] for unit in spoken(document))
    assert 'At noon it rained. Note. The keeper heard a bell. End note.' in text
    assert 'The bell was quiet.' in text
    assert 'another ending' not in text
    stored = next(b for b in document.blocks if b.role=='note')
    begin,end = stored.locator['bytes']
    assert source.read_bytes()[begin:end]==note
    assert stored.locator['binary_asset']['offset']==begin


def test_wordstar_reports_missing_graphic_and_unverified_record(tmp_path):
    path = tmp_path/'Damaged.ws'
    path.write_bytes(manuscript(b'The gate was open.\r\n'+record(16,b'Picture.PIX')+
                                b'\r\n'+record(0x45,b'\x1a')+b'\r\nKeep the next sentence.'))
    doc = inspect_source(path,offline=True)
    p = plan(doc)
    assert len(p['issues'])==2 and 'external WordStar illustration' in p['issues'][0]['reason']
    assert '0x45' in p['issues'][1]['reason']
    assert 'Keep the next sentence.' in ' '.join(unit['text'] for unit in p['utterances'])


def test_wordstar_reports_older_or_incomplete_headers_without_text_fallback(tmp_path):
    path = tmp_path/'Old.ws'
    raw = manuscript(b'An intact first sentence.\r\n')
    path.write_bytes(raw[:4]+b'\x60'+raw[5:])
    with pytest.raises(Error,match='WordStar 7'):
        inspect_source(path)
    path.write_bytes(raw[:-1]+b'\x1d\xff\xff\0')
    result = plan(inspect_source(path))
    assert 'invalid length' in result['issues'][0]['reason']
    assert 'An intact first sentence.' in ' '.join(unit['text'] for unit in result['utterances'])
