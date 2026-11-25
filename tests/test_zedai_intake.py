from pathlib import Path
from zipfile import ZipFile

from test_media import prepared

from audiobook.intake import inspect_saved, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.source_bundle import capture


NS = 'http://www.daisy.org/ns/z3998/authoring/'


def book(body,head=''):
    return (f'<document xmlns="{NS}" xmlns:sel="{NS}features/select/" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" xml:lang="en">'
            '<head><meta property="dc:title" content="The Harbor Log"/>'
            '<meta property="dc:creator" content="Alice Morgan"/>'
            '<meta property="rd:prefix" content="output" '
            'about="http://www.daisy.org/z3998/2012/auth/features/select/functionSets/#target"/>'
            f'{head}</head><body>{body}</body></document>')


def speech(doc,**options):
    return [unit['text'] for unit in plan(doc,**options)['utterances']]


def test_selects_the_first_talking_book_branch_and_preserves_variant_evidence(tmp_path):
    path = tmp_path/'log.zai'
    path.write_text(book(
        '<section><h>Arrival</h><p>She heard <sel:select>'
        '<sel:when expr="output:format(\'PRINT\')">a printing press</sel:when>'
        '<sel:when expr="not(output:format(\'BRAILLE\')) and output:format(\'TALKING_BOOK\')">'
        '<emph>two bells</emph></sel:when>'
        '<sel:otherwise>an otherwise signal</sel:otherwise></sel:select>.'
        '<pagebreak value="42"/></p>'
        '<p>The <sel:select><sel:when expr="other:choose(\'A\')">unresolved option</sel:when>'
        '<sel:otherwise>red lamp</sel:otherwise></sel:select> was lit.</p>'
        '<index><h>Index</h><entry>Beacon, fifty-three.</entry></index></section>'))
    doc = inspect_source(path,offline=True)
    assert doc.title=='The Harbor Log'
    assert doc.language=='en' and doc.metadata['authors']==['Alice Morgan']
    assert speech(doc)==['Arrival','She heard two bells.','The red lamp was lit.']
    assert any('Beacon' in unit for unit in speech(doc,policy={'mode':'study'}))
    selections = [entry for entry in doc.inventory if entry['kind']=='text_variant']
    assert [item['selected'] for item in selections[0]['readings']]==[False,True,False]
    assert selections[0]['readings'][1]['xml_path']
    assert selections[1]['unsupported_conditions']==["other:choose('A')"]
    assert doc.warnings and not plan(doc)['issues']
    store,job = prepared(tmp_path,path)
    finished = Path(render(store,job['id'],allow_synthesis=False)['output'])
    companion = (finished/'source.html').read_text()
    assert 'Text variants' in companion and 'a printing press' in companion and 'TALKING_BOOK' in companion


def test_preserves_note_wording_and_binds_backlinks_without_speaking_annotation_twice(tmp_path):
    path = tmp_path/'log.xml'
    path.write_text(book(
        '<p>The lantern was lit<noteref ref="n1" value="1"/>.</p>'
        '<p>The keeper read <annoref ref="a1">the editor’s note</annoref>.</p>'
        '<p xml:id="watch">The watch began at sunset.</p>'
        '<note xml:id="n1"><p>She lit it at dawn.</p></note>'
        '<note ref="watch"><p>The moon was low.</p></note>'
        '<annotation xml:id="a1"><p>Two pages survive.</p></annotation>'
        '<annotation ref="watch"><p>Editorial annotation without an inline callout.</p></annotation>'))
    doc = inspect_source(path,offline=True)
    text = ' '.join(speech(doc))
    for utterance in ('The lantern was lit.','She lit it at dawn.','the editor’s note','Two pages survive.',
                      'The watch began at sunset.','The moon was low.'):
        assert text.count(utterance)==1
    assert 'without an inline callout' not in text
    assert len([item for item in doc.inventory if item['kind']=='zedai_annotation'])==2
    assert not plan(doc)['issues']
    links = [link for block in doc.blocks for link in block.links if link['kind']=='note']
    assert len(links)==3 and all(link['destinations'] for link in links)


def test_capture_uses_the_exact_description_and_inherited_media_base(tmp_path):
    root = tmp_path/'Edition'
    media = root/'media'
    media.mkdir(parents=True)
    text = root/'text'
    text.mkdir()
    art = media/'harbor signal.svg'
    art.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    manuscript = text/'log.zai'
    source = book('<section xml:base="../media/"><h>The signal</h>'
                  '<object src="harbor%20signal.svg" srctype="image/svg+xml" desc="image-note"/>'
                  '<p>The gate opened at dawn.</p></section>',
                  '<description xml:id="image-note"><p>A beacon flashes twice.</p></description>')
    manuscript.write_text(source)
    archive = tmp_path/'Publication.zip'
    with ZipFile(archive,'w') as container:
        for file in (manuscript,art):
            container.write(file,'Book/'+file.relative_to(root).as_posix())
    expected = 'The signal A beacon flashes twice. The gate opened at dawn.'
    for path in (root,archive):
        doc = inspect_source(path,offline=True)
        assert ' '.join(speech(doc))==expected
        assert any(link['kind']=='source_resource' for block in doc.blocks for link in block.links)
        assert not plan(doc)['issues']
    assert not inspect_source(manuscript,offline=True).metadata.get('source_resources')
    job = tmp_path/'job'
    job.mkdir()
    snapshot,route = capture(root,job,offline=True)
    with ZipFile(snapshot) as saved:
        assert saved.read('text/log.zai')==source.encode()
        assert saved.read('media/harbor signal.svg')==art.read_bytes()
    art.unlink()
    manuscript.unlink()
    assert ' '.join(speech(inspect_saved(snapshot,route)))==expected


def test_uses_one_local_diagram_description_and_keeps_its_original(tmp_path):
    diagram = tmp_path/'descriptions.xml'
    diagram_namespace = NS+'features/description/'
    diagram.write_text(book(
        f'<d:description xmlns:d="{diagram_namespace}" xml:id="other">'
        '<d:body><d:longdesc><p>Different subject.</p></d:longdesc></d:body></d:description>'
        f'<d:description xmlns:d="{diagram_namespace}" xml:id="beacon"><d:body>'
        '<d:summary><p>A light.</p></d:summary>'
        '<d:longdesc><p>A beacon flashes twice.</p></d:longdesc>'
        '</d:body></d:description>'))
    main = tmp_path/'book.zai'
    main.write_text(book('<p>The light came on.</p><object src="beacon.svg" srctype="image/svg+xml" '
                         'desc="image-note"/>',
                         '<description xml:id="image-note" xlink:href="descriptions.xml#beacon"/>'))
    doc = inspect_source(tmp_path,offline=True)
    assert ' '.join(speech(doc))=='The light came on. A beacon flashes twice.'
    assert not plan(doc)['issues']
    assert any(item['member']=='descriptions.xml' for item in doc.metadata['source_resources'])
    figure, = [item for item in doc.blocks if item.role=='figure']
    assert figure.locator['authored_description']['source_file']=='descriptions.xml'
    diagram.unlink()
    failure = plan(inspect_source(main,offline=True))['issues']
    assert len(failure)==1 and 'authored accessible description' in failure[0]['reason']
