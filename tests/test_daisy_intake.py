from pathlib import Path
from zipfile import ZipFile

import pytest

from audiobook.core import Error, loadjson
from audiobook.intake import extract_location, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan


OPF = '''<package xmlns="http://openebook.org/namespaces/oeb-package/1.0/">
<metadata><dc-metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:Format>ANSI/NISO Z39.86-2005</dc:Format>
<dc:Title>Harbor Guide</dc:Title></dc-metadata></metadata>
<manifest><item id="sync" href="chapter.smil" media-type="application/smil"/>
<item id="book" href="text.xml" media-type="application/x-dtbook+xml"/>
<item id="self" href="harbor.opf" media-type="text/xml"/>
</manifest><spine><itemref idref="sync"/></spine></package>'''

TEXT = '''<dtbook xmlns="http://www.daisy.org/z3986/2005/dtbook/" version="2005-3">
<head/><book><bodymatter><level1>
<h1 id="title">Arrival</h1><pagenum id="folio">72</pagenum>
<p id="words"><sent><w id="a">A</w> <w id="ferry">ferry</w> <w id="came">came</w> <w id="in">in</w>.</sent></p>
<p id="bell">A bell rang.<noteref idref="#why">41</noteref></p>
<note id="why"><p>The watch had begun.</p></note>
</level1></bodymatter></book></dtbook>'''


def edition(path,cues,*,opf=OPF,include_text=True):
    smil = ('<smil xmlns="http://www.w3.org/2001/SMIL20/"><body><seq>\n'+
            '\n'.join(f'<par id="s{index}"><text src="text.xml#{anchor}"/></par>'
                      for index,anchor in enumerate(cues,1))+'</seq></body></smil>')
    with ZipFile(path,'w') as archive:
        archive.writestr('edition/harbor.opf',opf)
        if include_text:
            archive.writestr('edition/text.xml',TEXT)
        archive.writestr('edition/chapter.smil',smil)


def test_daisy_uses_smil_order_whole_sentences_and_repeated_occurrences(tmp_path):
    path = tmp_path/'guide.zip'
    edition(path,['title','bell','folio','a','ferry','came','in','bell'])
    doc = inspect_source(path)
    assert doc.metadata['intake']['route']=='daisy3'
    assert [r['role'] for r in doc.metadata['source_resources'] if r['member'].endswith('.opf')]==['descriptor']
    assert all(b.locator['member']=='edition/text.xml' for b in doc.blocks)
    assert len({b.locator['location_id'] for b in doc.blocks})==len(doc.blocks)
    assert [b.source for b in doc.blocks]==['Arrival','A bell rang.41','72','A ferry came in.',
                                           'A bell rang.41','The watch had begun.']
    assert len(doc.blocks[3].locator['playback_cues'])==4
    for repeated in [doc.blocks[1],doc.blocks[4]]:
        assert repeated.links[0]['destinations']==['source.html#'+doc.blocks[5].id]
    reading = plan(doc)
    assert not reading['issues']
    spoken = ' '.join(unit['text'] for unit in reading['utterances'])
    assert spoken.index('A bell rang.')<spoken.index('A ferry came in.')<spoken.rindex('A bell rang.')
    assert '72' not in spoken and '41' not in spoken


def test_daisy_reports_unknown_anchor_at_its_smil_line(tmp_path):
    path = tmp_path/'incomplete.zip'
    edition(path,['title','missing','words'])
    doc = inspect_source(path)
    cue, = [block for block in doc.blocks if block.issue]
    assert cue.locator['member']=='edition/chapter.smil' and cue.locator['lines']==[3,3]
    assert 'text.xml#missing' in cue.issue
    assert [block.source for block in doc.blocks if block.role!='note' and not block.issue]==['Arrival','A ferry came in.']


def test_audio_only_daisy_has_a_specific_outcome(tmp_path):
    path = tmp_path/'audio.zip'
    declaration = OPF.replace('<item id="book" href="text.xml" media-type="application/x-dtbook+xml"/>','')
    edition(path,[],opf=declaration,include_text=False)
    with pytest.raises(Error) as exc:
        inspect_source(path)
    assert exc.value.code=='daisy_audio_only'


def test_daisy_descriptor_and_zip_recover_exact_synchronization_source(tmp_path):
    from test_media import prepared

    path = tmp_path/'guide.zip'
    edition(path,['title','bell','words','bell'])
    with ZipFile(path) as book:
        for item in book.namelist():
            target = tmp_path/item
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(book.read(item))
        smil = book.read('edition/chapter.smil')
    direct = inspect_source(tmp_path/'edition/harbor.opf')
    assert [b.source for b in direct.blocks]==[b.source for b in inspect_source(path).blocks]
    store,job = prepared(tmp_path,path)
    output = Path(render(store,job['id'],allow_synthesis=False)['output'])
    moved = output.rename(tmp_path/'moved')
    companion = loadjson(moved/'companion.json')
    where = next(key for key,value in companion['source_locations'].items()
                 if value.get('member')=='edition/chapter.smil')
    recovered = tmp_path/'recovered.smil'
    extract_location(moved,where,recovered)
    assert recovered.read_bytes()==smil
