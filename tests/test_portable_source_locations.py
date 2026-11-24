import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup
from reportlab.pdfgen.canvas import Canvas

from audiobook.core import Error
from audiobook.intake import extract_location
from audiobook.jobs import render
from test_media import prepared
from test_upstream_epub import book


@pytest.mark.parametrize('wrapped',[False,True])
def test_recover_the_source_from_a_moved_epub_publication(tmp_path,wrapped):
    source = tmp_path/'book.epub'
    epub = book({'c':'<p id="intro">The original passage stays with the book.</p>'},['c','c'])
    source.write_bytes(epub)
    if wrapped:
        source = tmp_path/'download.zip'
        with ZipFile(source,'w') as archive:
            archive.writestr('nested/reading.epub',epub)
    store,job = prepared(tmp_path,source)
    result = render(store,job['id'],allow_synthesis=False)
    out = Path(result['output']).rename(tmp_path/'moved')
    source.unlink()
    companion = json.loads((out/'companion.json').read_text())
    blocks = [block for block in job['document']['blocks'] if block['locator'].get('anchor')=='intro']
    assert len(blocks)==2 and blocks[0]['id']!=blocks[1]['id']
    occurrences = []
    for index,block in enumerate(blocks):
        ident = block['id']
        loc = companion.get('source_block_locations',{}).get(ident,ident)
        saved = companion['source_locations'][loc]
        occurrences.append(saved['occurrence'])
        assert saved['path'].endswith('/c.xhtml') or saved['path']=='c.xhtml'
        if wrapped:
            assert saved['member']=='nested/reading.epub'
        copied = tmp_path/f'recovered{index}.epub'
        assert extract_location(out,ident,copied)['location']==saved
        assert copied.read_bytes()==epub
    assert occurrences==[1,2]


def test_native_pdf_link_keeps_page_and_recovery_checks_bytes(tmp_path):
    source = tmp_path/'book.pdf'
    pdf = Canvas(str(source),invariant=True)
    pdf.drawString(72,720,'The original passage stays with the book.')
    pdf.save()
    exact = source.read_bytes()
    store,job = prepared(tmp_path,source)
    block, = job['document']['blocks']
    result = render(store,job['id'],allow_synthesis=False)
    out = Path(result['output']).rename(tmp_path/'moved')
    source.unlink()
    html = BeautifulSoup((out/'source.html').read_text(),'html.parser')
    section = html.find(id=block['id'])
    assert section.find('a',href='original.pdf#page=1')
    assert section.code.get_text()==block['id']
    dest = tmp_path/'recovered.pdf'
    receipt = extract_location(out,block['id'],dest)
    assert receipt['location']['page']==1 and receipt['location']['rect']==block['locator']['rect']
    assert dest.read_bytes()==exact
    with pytest.raises(Error,match='destination already exists'):
        extract_location(out,block['id'],dest)
    (out/'original.pdf').write_bytes(b'Changed byte snapshot.')
    new = tmp_path/'bad.pdf'
    with pytest.raises(Error,match='recorded identity'):
        extract_location(out,block['id'],new)
    assert not new.exists()
    assert not list(tmp_path.glob('.hamr-source-*'))
