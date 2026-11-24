from pathlib import Path
from zipfile import ZipFile

import pytest

from audiobook.core import Error, loadjson
from audiobook.intake import extract_location, inspect_saved, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe


def smil(*anchors):
    return ('<smil><body><seq>\n'+''.join(
        f'<par id="s-{anchor}"><text src="content.htm#{anchor}"/><audio src="track.mp3"/></par>\n'
        for anchor in anchors)+'</seq></body></smil>')


@pytest.fixture
def files():
    return {
        'NCC.HTML': '<html><head><title>Harbor log</title></head><body>\n'
                    '<h1><a href="west.smil#s-west">West shore</a></h1>\n'
                    '<h1><a href="east.smil#s-east">East shore</a></h1>\n'
                    '<span><a href="west.smil#s-folio">91</a></span></body></html>',
        'content.htm': '<html><head><link rel="stylesheet" href="style.css"/></head><body>\n'
                       '<h1 id="east">East shore</h1><p id="dawn">The bell rang.</p>\n'
                       '<h1 id="west">West shore</h1><span class="page-normal" id="folio">91</span>\n'
                       '<p id="dusk">A ferry arrived.</p></body></html>',
        'east.smil': smil('east','dawn'), 'west.smil': smil('west','folio','dusk'),
        'track.mp3': b'original legacy audio', 'style.css': 'body { color: black }',
    }


def archive(path,files):
    with ZipFile(path,'w') as book:
        for name,data in sorted(files.items(),reverse=True):
            book.writestr('disc/'+name,data)


def sources(doc):
    return [row.source for row in doc.blocks if row.role!='hidden']


def test_ncc_folder_archive_and_component_preserve_the_declared_order(tmp_path,files):
    folder = tmp_path/'disc'
    folder.mkdir()
    for name,content in files.items():
        (folder/name).write_bytes(content.encode() if isinstance(content,str) else content)
    path = tmp_path/'book.zip'
    archive(path,files)
    reference = ['West shore','A ferry arrived.','East shore','The bell rang.']
    for item in (folder,folder/'NCC.HTML',path):
        doc = inspect_source(item)
        assert doc.metadata['intake']['route']=='daisy202' and sources(doc)==reference
        assert not plan(doc)['issues']
        assert all('91' not in unit['text'] for unit in plan(doc)['utterances'])
        assert len(doc.metadata['source_resources'])==len(files)
    assert sources(inspect_source(folder/'east.smil'))==reference[2:]
    spool = tmp_path/'spool'
    spool.mkdir()
    saved,selection = capture(folder,spool)
    (folder/'content.htm').write_text('The working copy changed.')
    assert sources(inspect_saved(saved,selection))==reference


def test_master_smil_keeps_explicit_repeats_and_mixed_archive_choices(tmp_path,files):
    files['MASTER.SMIL'] = ('<smil><body><ref src="east.smil"/><ref src="west.smil"/>'
                            '<ref src="east.smil"/></body></smil>')
    path = tmp_path/'library.zip'
    archive(path,{**files,'separate.txt':'A different book.'})
    with pytest.raises(Error) as exc:
        describe(path)
    choices = {item['name']:item['id'] for item in exc.value.candidates}
    assert choices.keys()=={'disc/separate.txt','Harbor log'}
    doc = inspect_source(path,entry=choices['Harbor log'])
    assert sources(doc)==['East shore','The bell rang.','West shore','A ferry arrived.','East shore','The bell rang.']
    assert len({block.locator['location_id'] for block in doc.blocks})==len(doc.blocks)


def test_master_lookup_keeps_one_physical_spelling_and_an_incomplete_master_uses_ncc(tmp_path,files,monkeypatch):
    from audiobook import ncc_intake

    for name,text in files.items():
        (tmp_path/name).write_bytes(text.encode() if isinstance(text,str) else text)
    master = tmp_path/'MASTER.SMIL'
    master.write_text('<smil><body><ref src="east.smil"/><ref src="west.smil"/></body></smil>')
    lookup = ncc_intake.relative_file

    def case_insensitive(root,name):
        return lookup(root,'MASTER.SMIL' if name=='master.smil' else name)

    monkeypatch.setattr(ncc_intake,'relative_file',case_insensitive)
    doc = inspect_source(tmp_path)
    assert sources(doc)==['East shore','The bell rang.','West shore','A ferry arrived.']
    assert not plan(doc)['issues']
    master.write_text('<smil><body><ref src="east.smil"/></body></smil>')
    doc = inspect_source(tmp_path)
    assert sources(doc)==['West shore','A ferry arrived.','East shore','The bell rang.']
    assert any('Using the complete NCC order' in warning for warning in doc.warnings)
    assert not plan(doc)['issues']


def test_missing_text_and_partially_transcribed_audio_have_exact_locators(tmp_path,files):
    files['west.smil'] = ('<smil><body><seq>\n<par><text src="content.htm#west"/></par>\n'
                          '<par><audio src="track.mp3"/></par>\n'
                          '<par><text src="content.htm#missing"/></par>\n</seq></body></smil>')
    path = tmp_path/'clip.zip'
    archive(path,files)
    issues = plan(inspect_source(path))['issues']
    assert [i['location']['lines'] for i in issues]==[[3,3],[4,4]]
    assert all(i['location']['member']=='disc/west.smil' for i in issues)
    assert 'no synchronized text' in issues[0]['reason'] and '#missing' in issues[1]['reason']


def test_declared_audio_only_and_partial_editions_have_an_explicit_outcome(tmp_path,files):
    path = tmp_path/'audio.zip'
    for mode,code in [('audioNcc','daisy_audio_only'),('audioPartText','daisy_partial_text')]:
        archive(path,{**files,'NCC.HTML':files['NCC.HTML'].replace('</head>',
                f'<meta name="NCC:MultimediaType" content="{mode}"/></head>')})
        with pytest.raises(Error) as exc:
            inspect_source(path)
        assert exc.value.code==code


def test_synchronized_note_rejoins_the_body_and_uses_the_shared_note_policy(tmp_path,files):
    files['content.htm'] = ('<html><body><h1 id="west">West shore</h1>'
                            '<div class="linegroup"><div class="line"><span id="before">The wind</span> '
                            '<a id="mark" class="noteref" href="west.smil#ref">9</a>'
                            '<span id="after"> had changed.</span></div></div>'
                            '<h1 id="east">East shore</h1><p id="dawn">The bell rang.</p>'
                            '<div id="footnote" class="notebody"><p id="definition">It came from the west.</p></div>'
                            '</body></html>')
    files['west.smil'] = ('<smil><body><seq><par><text src="content.htm#west"/></par>'
                          '<par><text src="content.htm#before"/></par><seq>'
                          '<par id="ref"><text src="content.htm#mark"/></par>'
                          '<par system-required="footnote-on"><text src="content.htm#footnote"/></par></seq>'
                          '<par><text src="content.htm#after"/></par></seq></body></smil>')
    files['east.smil'] = smil('east','dawn','definition')
    path = tmp_path/'annotated.zip'
    archive(path,files)
    doc = inspect_source(path)
    body = next(b for b in doc.blocks if b.source.startswith('The wind'))
    note, = [b for b in doc.blocks if b.role=='note']
    assert [cue['href'].split('#')[1] for cue in note.locator['playback_cues']]==['footnote','definition']
    link, = body.links
    assert link['destination']=='source.html#'+note.id and link['destinations']==[link['destination']]
    default = plan(doc)
    assert not default['issues']
    spoken = ' '.join(unit['text'] for unit in default['utterances'])
    assert 'The wind had changed. Note. It came from the west. End note.' in spoken
    assert spoken.count('It came from the west.')==1
    assert '9' not in spoken
    companion = plan(doc,{'notes':'companion'})
    assert not companion['issues']
    assert all('It came from the west.' not in unit['text'] for unit in companion['utterances'])


def test_unrelated_html_named_ncc_retains_ordinary_html(tmp_path):
    path = tmp_path/'ncc.html'
    path.write_text('<html><body><p>This is an ordinary HTML file.</p></body></html>')
    assert describe(path).route=='html'


def test_recovers_exact_legacy_markup_and_audio_from_moved_publication(tmp_path,files):
    from test_media import prepared

    path = tmp_path/'book.zip'
    archive(path,files)
    store,job = prepared(tmp_path,path)
    moved = Path(render(store,job['id'],allow_synthesis=False)['output']).rename(tmp_path/'moved')
    companion = loadjson(moved/'companion.json')
    assert (moved/companion['original_source']).read_bytes()==path.read_bytes()
    for name in ('NCC.HTML','content.htm','west.smil','track.mp3','style.css'):
        location = next(k for k,v in companion['source_locations'].items() if v['member']=='disc/'+name)
        output = tmp_path/name
        extract_location(moved,location,output)
        raw = files[name]
        assert output.read_bytes()==(raw.encode() if isinstance(raw,str) else raw)
