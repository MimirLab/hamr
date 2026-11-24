from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

from audiobook.core import Error, loadjson
from audiobook.intake import extract_location, inspect_saved, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe

ARTICLE = '''<article dtd-version="1.4" xml:lang="en" xmlns:xlink="http://www.w3.org/1999/xlink">
<front><article-meta>
<title-group><article-title>Harbor Watch</article-title></title-group>
<contrib-group><contrib contrib-type="author"><name><surname>Morgan</surname><given-names>Alice</given-names></name></contrib></contrib-group>
<abstract><p>The early ferry crossed the harbor in thick fog.</p></abstract>
<trans-abstract xml:lang="fr"><p>Traduction du résumé.</p></trans-abstract>
</article-meta></front>
<body><sec><title>Arrival</title>
<p>The schedule lists 1 boat<xref ref-type="fn" rid="n1"><sup>1</sup></xref> and 1 watch<xref rid="n2" ref-type="fn">1</xref>.</p>
<p>The first mate <xref alt="checked figure two" ref-type="fig" rid="signal">checked Fig. II</xref>.</p>
<fig id="signal"><label>Figure 2.</label><caption><p>The harbor signal.</p></caption>
<alt-text>A light flashes on the outer pier.</alt-text><graphic xlink:href="assets/signal.png"/></fig>
<table-wrap id="schedule"><label>Table 1.</label><caption><p>Morning tide heights.</p></caption>
<alternatives><graphic xlink:href="assets/table.png"/>
<table><tr><th>Time</th><th>Height</th></tr><tr><td>Dawn</td><td>Two feet.</td></tr></table></alternatives>
</table-wrap></sec></body>
<back><fn-group><fn id="n1"><label>1</label><p>The ferry was early.</p></fn>
<fn id="n2"><label>1</label><p>The watch lasted ten minutes.</p></fn></fn-group></back></article>'''


def speech(doc,policy=None):
    return ' '.join(u['text'] for u in plan(doc,policy)['utterances'])


def test_exact_duplicate_marker_positions_and_primary_abstract(tmp_path):
    source = tmp_path/'harbor.xml'
    source.write_text(ARTICLE)
    doc = inspect_source(source)
    assert doc.title=='Harbor Watch' and doc.language=='en' and doc.metadata['authors']==['Alice Morgan']
    assert not plan(doc)['issues']
    spoken = speech(doc)
    assert spoken.index('The early ferry') < spoken.index('Arrival')
    assert 'Traduction' not in spoken and any('Traduction' in block.source for block in doc.blocks)
    assert 'The first mate checked figure two.' in spoken
    assert 'A light flashes on the outer pier.' in spoken
    assert 'Morning tide heights.' in spoken
    assert spoken.count('The ferry was early.')==1 and spoken.count('The watch lasted ten minutes.')==1
    paragraph = next(block for block in doc.blocks if 'The schedule lists' in block.source)
    refs = [span for span in paragraph.locator['inline_semantics'] if span['kind']=='note_reference']
    assert [paragraph.source[span['source_start']-4:span['source_end']] for span in refs]==['boat1','atch1']
    assert [span['source_locator']['xml_path'].split('/')[-1] for span in refs]==['xref[1]','xref[2]']
    assert [span['target_href'] for span in refs]==['#n1','#n2']
    assert 'The ferry was early.' not in speech(doc,{'notes':'companion'})


def test_missing_note_and_real_visual_hold_the_exact_source(tmp_path):
    source = tmp_path/'harbor.jats'
    bad = ARTICLE.replace('rid="n1"','rid="unknown"').replace('<alt-text>A light flashes on the outer pier.</alt-text>','')
    source.write_text(bad)
    issues = plan(inspect_source(source))['issues']
    assert len(issues)==2
    assert 'note is unavailable' in issues[0]['reason']
    assert issues[0]['location']['xml_path'].endswith('/p[1]')
    assert 'authored accessible description' in issues[1]['reason']
    assert issues[1]['location']['xml_path'].endswith('/fig/graphic')


def test_verified_replacement_resolves_stale_note_through_reimport(tmp_path):
    from test_media import prepared
    from audiobook.jobs import repair

    source = tmp_path/'harbor.jats'
    source.write_text(ARTICLE.replace('rid="n1"','rid="unavailable"'))
    store,job = prepared(tmp_path,source)
    issue, = job['plan']['issues']
    verified = 'The schedule lists one boat and one watch.'
    repair(store,job['id'],block_id=issue['block'],text=verified)
    repair(store,job['id'],reimport=True)
    saved = store.get(job['id'])
    assert not saved['plan']['issues']
    assert any(verified in unit['text'] for unit in saved['plan']['utterances'])
    assert not any('The ferry was early.' in unit['text'] for unit in saved['plan']['utterances'])
    assert 'rid="unavailable"' in Path(saved['snapshot']).read_text()


def test_live_folder_claims_only_declared_assets_and_captures_immutable_original(tmp_path):
    book = tmp_path/'book'
    book.mkdir()
    library = book/'assets'
    library.mkdir()
    source = book/'harbor.jats'
    source.write_text(ARTICLE)
    for name in ('signal.png','table.png'):
        Image.new('L',(1,1),255).save(library/name)
    buffer = tmp_path/'snapshot'
    buffer.mkdir()
    assert describe(book).route=='xml-book'
    doc = inspect_source(book)
    assert doc.metadata['intake']['kind']=='zip'
    assert {row['member'] for row in doc.metadata['source_resources']}=={
        'harbor.jats','assets/signal.png','assets/table.png'}
    saved,selected = capture(book,buffer)
    original = (library/'signal.png').read_bytes()
    (library/'signal.png').write_bytes(b'working file replaced')
    source.write_text('<different/>')
    assert speech(inspect_saved(saved,selected))==speech(doc)
    with ZipFile(saved) as frozen:
        assert frozen.read('assets/signal.png')==original
        assert frozen.read('harbor.jats')==ARTICLE.encode()


def test_archive_selection_preserves_assets_and_recovers_after_move(tmp_path):
    from test_media import prepared

    path = tmp_path/'publication.zip'
    with ZipFile(path,'w') as book:
        book.writestr('vol/harbor.xml',ARTICLE)
        book.writestr('vol/assets/signal.png',b'original signal data')
        book.writestr('vol/assets/table.png',b'original table data')
        book.writestr('other.txt','A separate short document.')
    with pytest.raises(Error) as exc:
        describe(path)
    choices = {row['name']:row['id'] for row in exc.value.candidates}
    assert choices.keys()=={'Harbor Watch','other.txt'}
    assert len(inspect_source(path,entry=choices['Harbor Watch']).metadata['source_resources'])==3

    # Give the ordinary noninteractive rendering helper just the publication.
    only = tmp_path/'article.zip'
    with ZipFile(only,'w') as book:
        with ZipFile(path) as original:
            for name in original.namelist()[:-1]:
                book.writestr(name,original.read(name))
    store,job = prepared(tmp_path,only)
    published = Path(render(store,job['id'],allow_synthesis=False)['output']).rename(tmp_path/'moved')
    companion = loadjson(published/'companion.json')
    assert (published/companion['original_source']).read_bytes()==only.read_bytes()
    with ZipFile(only) as original:
        for member in original.namelist():
            location = next(k for k,v in companion['source_locations'].items() if v.get('member')==member)
            output = tmp_path/Path(member).name
            extract_location(published,location,output)
            assert output.read_bytes()==original.read(member)
