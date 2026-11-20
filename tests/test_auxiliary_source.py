import json
from pathlib import Path
from bs4 import BeautifulSoup
from test_upstream_epub import book,speech
from test_media import prepared
from audiobook.importers import epub
from audiobook.planner import plan
from audiobook.jobs import render


def test_note_content_is_separate_source_and_reading_policy_is_explicit():
    body='<p>BEFORE<a role="doc-noteref" href="notes.xhtml#n">1</a> AFTER.</p>'
    data=book({'c':body,'notes':'<aside id="n">NOTE_CANARY.</aside>'},['c'])
    doc=epub(data);claim=doc.blocks[0]
    assert claim.source=='BEFORE1 AFTER.' and 'NOTE_CANARY' not in claim.source
    assert speech(plan(doc))=='BEFORE AFTER. Note. NOTE_CANARY. End note.'
    assert speech(plan(doc,{'notes':'inline'}))=='BEFORE Note. NOTE_CANARY. End note. AFTER.'
    assert speech(plan(doc,{'notes':'companion'}))=='BEFORE AFTER. A note is available in the companion.'
    assert any(b.locator.get('auxiliary') and b.source=='NOTE_CANARY.' for b in doc.blocks)


def test_hidden_note_does_not_leak_and_image_only_reference_still_resolves():
    body='<p>KEPT<span hidden="hidden"><a role="doc-noteref" href="n.xhtml#secret">1</a></span>.</p><p>BODY<a role="doc-noteref" href="n.xhtml#public"><img alt="note marker"/></a> END.</p>'
    d=epub(book({'c':body,'n':'<aside id="secret">SECRET_NOTE.</aside><aside id="public">PUBLIC_NOTE.</aside>'},['c']))
    text=speech(plan(d))
    assert 'SECRET_NOTE' not in text and 'PUBLIC_NOTE.' in text and 'note marker' not in text


def test_repeated_references_and_nonlinear_description_have_offline_targets_and_backlinks(tmp_path):
    body='<p id="caller">Follow <a href="descriptions.xhtml#long">the description</a>.</p>'
    source=tmp_path/'source.epub';source.write_bytes(book({'c':body,'descriptions':'<section id="long"><p>DESCRIPTION_CANARY.</p></section>'},['c','c']))
    s,j=prepared(tmp_path,source);targets=[b for b in j['document']['blocks'] if b['locator'].get('auxiliary')]
    assert len(targets)==1 and targets[0]['source']=='DESCRIPTION_CANARY.'
    assert 'DESCRIPTION_CANARY' not in speech(j['plan'])
    result=render(s,j['id'],allow_synthesis=False);out=Path(result['output']);html=BeautifulSoup((out/'source.html').read_text(),'html.parser')
    target=html.find(id=targets[0]['id']);backlinks=target.find_all('a',string='Back to referring passage')
    assert len(backlinks)==2 and len({a['href'] for a in backlinks})==2
    assert all(html.find(id=a['href'][1:]) for a in backlinks)
    companion=json.loads((out/'companion.json').read_text());assert len(companion['source_links'])==2


def test_same_page_description_reuses_its_original_source_target():
    body='<p>Before <img alt="Chart" aria-details="detail"/> after.</p><section id="detail"><p>DETAIL_CANARY.</p></section>'
    d=epub(book({'c':body},['c']));assert not any(b.locator.get('auxiliary') for b in d.blocks)
    assert speech(plan(d)).count('DETAIL_CANARY.')==1


def test_note_structure_preserves_math_and_language_instead_of_flattening_it():
    body='<p>CLAIM<a role="doc-noteref" href="n.xhtml#math">1</a>.</p><p>FOREIGN<a role="doc-noteref" href="n.xhtml#foreign">2</a>.</p>'
    notes='<aside id="math">Value <math><msup><mn>10</mn><mn>3</mn></msup></math>.</aside><aside id="foreign" xml:lang="fr">le renard</aside>'
    d=epub(book({'c':body,'n':notes},['c']));p=plan(d)
    assert '10 to the power of 3' in speech(p) and 'le renard' not in speech(p)
    assert any('non-English' in i['reason'] or 'language' in i['reason'] for i in p['issues'])
    companion=plan(d,{'notes':'companion'})
    assert not companion['issues'] and 'FOREIGN' in speech(companion) and 'le renard' not in speech(companion)
    link=d.blocks[0].links[0]
    assert link['text']=='Value 103.' and any(s['kind']=='mathml' for s in link['inline_semantics'])
