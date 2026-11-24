from pathlib import Path
from struct import pack, pack_into
from zipfile import ZipFile
import zlib

import pytest

from audiobook.core import Error
from audiobook.intake import inspect_source
from audiobook.planner import plan


def compressed(data):
    chunks = [zlib.compress(data[start:start+4096]) for start in range(0,len(data),4096)]
    return pack('<II',len(chunks),len(data))+b''.join(pack('<I',len(chunk)) for chunk in chunks)+b''.join(chunks)


def publication(records):
    header = bytearray(296)
    header[:6] = b'\xb0\x0c\xb0\x0c\x02\x00'
    start = len(header)
    directory = []
    contents = []
    for name,data,flags in records:
        directory.append(pack('<32sIII',name.encode(),len(data),start,flags))
        contents.append(data)
        start += len(data)
    pack_into('<II',header,24,start,start+4+44*len(records))
    return bytes(header)+b''.join(contents)+pack('<I',len(records))+b''.join(directory)


def narrated(doc):
    return [item['text'] for item in plan(doc)['utterances']]


def test_rocket_keeps_link_order_named_anchor_and_decoded_source(tmp_path):
    start = b'<html><head><meta charset="utf-8"></head><body><h1>First chapter</h1><p>The keeper said '
    text = start+b' '*(4095-len(start))+'“good night.”'.encode()+b'</p><p><a href="morning.html#first-light">Continue to morning</a></p></body></html>'
    root = ('night.html',compressed(text),8)
    body = b'<html><body><a name="first-light"><h1>Morning</h1></a><p>The boats returned.</p></body></html>'
    info = ('reader.info',b'TITLE=Harbor Watch\nBODY=night.html\n',2)
    records = [('morning.html',body,0),info,root]
    original = publication(records)
    source = tmp_path/'reading.rb'
    source.write_bytes(original)
    archive = tmp_path/'library.zip'
    with ZipFile(archive,'w') as z:
        z.writestr('Library/Reading.rb',original)
    for path in (source,archive):
        result = inspect_source(path)
        assert result.metadata['rocket_contents']['order']==['night.html','morning.html']
        assert [word for word in narrated(result) if 'good night' in word]
        assert any('“good night.”' in block.source for block in result.blocks)
        link = next(block for block in result.blocks if block.source=='Continue to morning')
        heading = next(block for block in result.blocks if block.source=='Morning')
        assert link.role=='navigation'
        assert link.links[0]['destinations']==['source.html#'+heading.id]
        assert result.metadata['source_resources'][2]['binary_asset']['codec']=='rocket-zlib'


def test_rocket_preserves_sound_siblings_when_one_record_has_invalid_compression(tmp_path):
    info = ('book.info',b'BODY=first.html\n',2)
    first = ('first.html',b'<h1>First</h1><p>The shoreline was quiet.</p>',0)
    second = ('next.html',compressed(b'<p>The tide was coming in.</p>')[:-1],8)
    source = tmp_path/'broken.rb'
    source.write_bytes(publication([info,first,second]))
    result = inspect_source(source)
    assert 'The shoreline was quiet.' in narrated(result)
    issue, = [block for block in result.blocks if block.issue]
    assert issue.locator['path']=='next.html'
    assert 'compressed Rocket eBook record' in issue.issue
    source.write_bytes(publication([info,first,('next.html',b'unknown',1)]))
    with pytest.raises(Error) as error:
        inspect_source(source)
    assert error.value.code=='protected_source'


def test_rocket_binds_only_unique_case_insensitive_artwork_match(tmp_path):
    info = ('book.info',b'BODY=page.html\n',2)
    page = ('page.html',b'<p><img src="Spacer.png" alt="Divider"></p>',0)
    asset = ('spacer.png',b'original image bytes',0)
    source = tmp_path/'picture.rb'
    source.write_bytes(publication([info,page,asset]))
    doc = inspect_source(source)
    record = next(item for item in doc.metadata['source_resources'] if item['name']=='spacer.png')
    link, = doc.blocks[0].links
    assert record['status']=='referenced'
    assert link['href']=='Spacer.png'
    assert link['destinations']==['source.html#resource-'+record['location_id']]
    source.write_bytes(publication([info,page,asset,('SPACER.PNG',b'another image',0)]))
    assert not inspect_source(source).blocks[0].links


def test_shared_html_prefers_body_and_retains_only_verified_contents_as_navigation(tmp_path):
    path = Path(tmp_path)/'contents.html'
    path.write_text('''<html><body><head><title>Test publication</title></head>
    <h1>Table of Contents</h1><h2><a href="contents.html#one">Start here</a></h2>
    <blockquote><p><a href="#two">Go here</a></p></blockquote>
    <h1 id="one">First chapter</h1><p>The water was calm.</p>
    <h1><a name="two"></a>Second chapter</h1><p>The ship came home.</p>
    <h1>Contents</h1><p>The cupboard contents are listed on the door.</p></body></html>''',encoding='utf-8')
    result = inspect_source(path)
    assert result.title=='Test publication'
    assert [block.source for block in result.blocks if block.role=='navigation']==[
        'Table of Contents','Start here','Go here']
    words = narrated(result)
    assert 'Test publication' not in words and 'Start here' not in words
    assert 'The cupboard contents are listed on the door.' in words


def test_shared_html_recovers_missing_paragraph_closes_without_losing_words(tmp_path):
    path = tmp_path/'legacy.html'
    text = [f'The keeper recorded observation {number}.' for number in range(170)]
    path.write_text('<html><body>'+''.join('<p><font face="Times">'+line+'</font>' for line in text)+'</body></html>')
    result = inspect_source(path)
    assert [block.source for block in result.blocks]==text
    assert any(row['kind']=='html_structure_recovered' for row in result.inventory)


def test_known_illustration_titles_stay_outside_the_surrounding_sentence(tmp_path):
    path = tmp_path/'illustrated.html'
    path.write_text('''<h1>At the harbor</h1>
    <p>The keeper <a name="inline"></a><img src="keeper.png"> watched the waves.</p>
    <p><a name="standalone"></a><img src="lamp.png"></p>
    <p><img src="unknown.png"></p>
    <h1>List of Illustrations</h1>
    <p><a href="#inline">The Keeper</a><br><a href="#standalone">A Lantern on the Pier</a></p>''')
    result = inspect_source(path)
    assert 'The keeper watched the waves.' in narrated(result)
    assert 'A Lantern on the Pier.' in narrated(result)
    assert all(block.role=='navigation' for block in result.blocks if 'The Keeper' in block.source)
    issue, = plan(result)['issues']
    assert 'no authored accessible description' in issue['reason']
