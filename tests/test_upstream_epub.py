"""Source canaries specify order independently of the extractor/navigation."""
import io
import json
import subprocess
import sys
import zipfile
from audiobook.importers import epub
from audiobook.planner import plan


def book(resources,spine,nav='',manifest_order=None):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        keys=manifest_order or list(resources)
        manifest=''.join('<item id="'+name+'" href="'+name+'.xhtml"/>' for name in keys)
        manifest+='<item id="nav" href="nav.xhtml" properties="nav"/>'
        z.writestr('book.opf','<package><metadata><title>Coverage control</title><language>en</language></metadata><manifest>'+manifest+'</manifest><spine>'+''.join('<itemref idref="'+name+'"/>' for name in spine)+'</spine></package>')
        for name,body in resources.items():z.writestr(name+'.xhtml','<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'+body+'</body></html>')
        z.writestr('nav.xhtml','<html xmlns:epub="http://www.idpf.org/2007/ops"><body>LINEAR_NAV_PREFIX<nav epub:type="toc">'+nav+'</nav></body></html>')
    return data.getvalue()


def speech(p):return ' '.join(u['text'] for u in p['utterances'])


def test_inline_objects_and_hidden_text_have_source_supported_positions():
    doc=epub(book({'c':'<p>BEFORE <img alt="ALTERNATIVE"/> AFTER.</p><p>LEFT <svg role="img" aria-label="RELATIONSHIP"><circle/></svg> RIGHT.</p><p>KEPT <span hidden="hidden">HIDDEN_ONLY</span> TAIL.</p>'},['c']))
    p=plan(doc);text=speech(p)
    assert 'BEFORE ALTERNATIVE AFTER.' in text and 'LEFT RELATIONSHIP RIGHT.' in text
    assert 'KEPT TAIL.' in text and 'HIDDEN_ONLY' not in text
    assert any('HIDDEN_ONLY' in d['source'] for d in p['decisions'])
    assert not p['issues']


def test_svg_title_and_aria_reference_are_read_once_in_their_source_position():
    body='<p>FIRST <svg><title>DRAWING_TITLE</title><circle/></svg> SECOND.</p><p>THIRD <svg aria-labelledby="label"><circle/></svg> FOURTH.</p><p hidden="hidden" id="label">REFERENCED_LABEL</p>'
    p=plan(epub(book({'c':body},['c'])))
    assert 'FIRST DRAWING_TITLE SECOND.' in speech(p)
    assert 'THIRD REFERENCED_LABEL FOURTH.' in speech(p)
    assert speech(p).count('DRAWING_TITLE')==speech(p).count('REFERENCED_LABEL')==1


def test_nav_is_an_ordered_view_and_does_not_omit_preface_or_reorder_spine(tmp_path):
    resources={'preface':'<h1>Preface</h1><p>PREFACE_CANARY.</p>',
        'a':'<p id="first">ALPHA_CANARY. <span id="middle">BETA_CANARY.</span> TAIL_CANARY.</p>',
        'b':'<p>CONTINUATION_CANARY.</p><h1 id="later">Same label</h1><p>GAMMA_CANARY.</p>'}
    nav='<a href="a.xhtml#first">Same label</a><a href="a.xhtml#middle">Second interval</a><a href="b.xhtml#later">Same label</a><a href="b.xhtml#missing">Broken anchor</a>'
    source=book(resources,['preface','a','b','a'],nav,manifest_order=['b','a','preface'])
    path=tmp_path/'control.epub';path.write_bytes(source)
    result=subprocess.run([sys.executable,'-m','audiobook','--home',str(tmp_path/'state'),'plan',str(path),'--json'],check=True,capture_output=True,text=True)
    p=json.loads(result.stdout);text=speech(p)
    expected=['PREFACE_CANARY.','ALPHA_CANARY.','BETA_CANARY.','TAIL_CANARY.','CONTINUATION_CANARY.','GAMMA_CANARY.','ALPHA_CANARY.','BETA_CANARY.','TAIL_CANARY.']
    assert [word for word in text.split() if 'CANARY.' in word]==expected
    assert 'LINEAR_NAV_PREFIX' not in text
    assert next(u for u in p['utterances'] if 'CONTINUATION_CANARY.' in u['text'])['chapter']=='Second interval'
    assert next(u for u in p['utterances'] if 'BETA_CANARY.' in u['text'])['chapter']=='Second interval'
    assert any('missing' in warning for warning in p['warnings'])
    assert not p['issues']


def test_equal_adjacent_titles_remain_distinct_source_intervals():
    p=plan(epub(book({'c':'<p id="one">FIRST_CANARY.</p><p id="two">SECOND_CANARY.</p>'},['c'],
        '<a href="c.xhtml#one">Same</a><a href="c.xhtml#two">Same</a>')))
    assert p['chapters']==['Same','Same']
    assert len({u['chapter_id'] for u in p['utterances']})==2


def test_nested_wrapper_text_and_lists_are_not_visited_twice():
    p=plan(epub(book({'c':'<div>BEFORE <p>MIDDLE.</p> AFTER.</div><ul><li>OUTER <ul><li>INNER.</li></ul> TAIL.</li></ul>'},['c'])))
    assert speech(p)=='BEFORE MIDDLE. AFTER. OUTER INNER. TAIL.'


def test_a_linear_nav_resource_retains_its_non_navigation_prose():
    p=plan(epub(book({'c':'<p>BODY_CANARY.</p>'},['nav','c'],'<a href="c.xhtml">Contents label</a>')))
    assert 'LINEAR_NAV_PREFIX' in speech(p) and 'BODY_CANARY.' in speech(p)
    assert 'Contents label' not in speech(p)


def test_split_nav_interval_retains_note_at_its_calling_occurrence():
    body='<p id="first">FIRST<a role="doc-noteref" href="#n">1</a>. <span id="next">SECOND.</span></p><aside epub:type="footnote" id="n">NOTE_CANARY.</aside>'
    source=book({'c':body},['c'],'<a href="c.xhtml#first">One</a><a href="c.xhtml#next">Two</a>')
    p=plan(epub(source));one=' '.join(u['text'] for u in p['utterances'] if u['chapter']=='One')
    two=' '.join(u['text'] for u in p['utterances'] if u['chapter']=='Two')
    assert 'FIRST' in one and 'NOTE_CANARY.' in one
    assert 'SECOND.' in two and 'NOTE_CANARY.' not in two


def test_print_page_marker_is_lookup_data_without_changing_neighboring_words():
    doc=epub(book({'c':'<p>pressure<span role="doc-pagebreak" aria-label="Page 50">50</span>rose.</p>'},['c']));p=plan(doc)
    assert speech(p)=='pressure rose.'
    assert any(s['kind']=='pagebreak' and s['label']=='Page 50' for s in doc.blocks[0].locator['inline_semantics'])
    assert 'pressure50rose.' in p['decisions'][0]['source']


def test_standalone_image_uses_its_authored_aria_description():
    p=plan(epub(book({'c':'<img aria-label="SOURCE_DESCRIPTION"/>'},['c'])))
    assert speech(p)=='SOURCE_DESCRIPTION' and not p['issues']
