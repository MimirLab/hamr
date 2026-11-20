"""Independent paired meaning/boundary annotations from local failure probes."""
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
from audiobook.boundaries import sentence_parts
from audiobook.core import Block,Document,Store
from audiobook.importers import epub,markdown
from audiobook.planner import normalize,plan,split
from audiobook.speech_compare import comparison_tokens as c, mathematical_markers as markers


@pytest.mark.parametrize('written,sentences',[
    ('Meet Dr. Chen at St. Anne’s. Use 12 King St. Bring ID.',[
        'Meet Doctor Chen at Saint Anne’s.','Use 12 King Street.','Bring ID.']),
    ('Meet at 12 Cedar Dr. before noon.',['Meet at 12 Cedar Drive before noon.']),
    ('At 5 p.m. Dr. Chen arrived. She left at 6 p.m. Then she called.',[
        'At 5 P M Doctor Chen arrived.','She left at 6 P M.','Then she called.']),
    ('We met at 5 p.m. Monday in Room 5.',['We met at 5 P M Monday in Room 5.']),
    ('The U.S. team arrived. She left the U.S. Then she called.',[
        'The U.S. team arrived.','She left the U.S.','Then she called.']),
    ('See No. 5 on p. 6.',['See number 5 on page 6.']),
    ('No. Bring five.',['No.','Bring five.']),
    ('J. R. R. Tolkien.',['J. R. R. Tolkien.']),
    ('“Are you leaving?” Dr. Chen asked. “Yes.”',['“Are you leaving?” Doctor Chen asked.','“Yes.”']),
    ('“Yes.” Dr. Chen left.',['“Yes.”','Doctor Chen left.']),
])
def test_reading_and_sentence_stop_are_distinct_from_synthesis_joins(written,sentences):
    spoken=normalize(written)
    assert list(sentence_parts(spoken))==sentences
    for budget in (18,32,65):
        chunks=split(spoken,words=budget)
        assert ' '.join(chunks).split()==spoken.split()
        assert all(len(chunk.split())<=budget+2 for chunk in chunks)
        assert not any(chunk.endswith(('Doctor','Saint','J.','R.')) for chunk in chunks[:-1])


@pytest.mark.parametrize('right,wrong',[
    ('10⁴','104'),('10⁻³','10³'),('2½','21/2'),('m²','m'),('CO₂','CO₃'),
    ('5 MB/s','5 Mb/s'),('≥ −0.08 mV','> −0.08 mV'),('≥ −0.08 mV','≥ 0.08 mV'),
    ('0.08–0.80','0.08 and 0.80'),('0.080','0.08'),('Room 007 runs v2.4.10.','Room 7 runs v2.4.1.'),
])
def test_wrong_meaning_cannot_disappear_in_display_normalization(right,wrong):
    assert c(right)!=c(wrong)
    assert markers(right)!=markers(wrong) or any(t.startswith('#') for t in c(right))


def test_correct_notation_readings_have_the_same_values_and_operators():
    assert c('10⁻³')==c('ten to the power of minus three')
    assert c('2½')==c('two and one half')
    assert c('5 MB/s')==c('five megabytes per second')
    assert c('≥ −0.08 mV')==c('greater than or equal to minus 0.08 millivolts')
    assert c('ﬁrst')==c('first')
    assert markers('6 cm² per gram')==markers('6 square centimeters per gram')


def test_word_budget_keeps_a_clock_and_critical_operator_with_its_value():
    for budget in (18,32,65):
        clock=' '.join(['context']*(budget-2))+' 5 P M. Then she called.'
        chunks=split(clock,words=budget)
        assert any('5 P M.' in chunk for chunk in chunks)
        critical=' '.join(['context']*(budget-1))+' only if the offset is greater than or equal to minus 0.08 millivolts.'
        chunks=split(critical,words=budget)
        assert ' '.join(chunks)==critical
        assert any('greater than or equal to minus 0.08 millivolts' in chunk for chunk in chunks)


@pytest.mark.parametrize('expected,wrong',[
    ('Use 10⁴ readings.','Use 104 readings.'),
    ('Transfer 5 MB/s.','Transfer 5 Mb/s.'),
    ('Stop at ≥ −0.08 mV.','Stop at > −0.08 mV.'),
    ('Do not restart.','Do restart.'),
    ('Checked. Snapshot made.','Unchecked. Snapshot made.'),
    ('Unchecked. Rollback tested.','Checked. Rollback tested.'),
    ('Keep the closing exception in place.',''),
])
def test_real_qa_gate_flags_plausible_bad_content_even_below_wer_threshold(tmp_path,monkeypatch,expected,wrong):
    import numpy as np
    from audiobook.backend import validate_pcm
    from audiobook.core import RATE,atomic,key,writejson
    from audiobook.jobs import create,clip_key,valid_clip
    from audiobook.qa import check
    import audiobook.qa_cache as cache
    prefix='The field observation records the ordinary conditions in the surrounding area. The observer then follows the same established procedure to record the remaining information before leaving this place. '
    source=tmp_path/'source.md';source.write_text(prefix+expected)
    s=Store(tmp_path/'state');j=s.get(create(s,source)['job']);j['voice']={'test':True};j['backend']={'test':True};s.save(j)
    data=(np.sin(np.arange(RATE*3)*2*np.pi*200/RATE)*10000).astype('<i2').tobytes()
    for u in j['plan']['utterances']:
        k=clip_key(u,j);_,receipt=validate_pcm(data);receipt['key']=k
        atomic(s.root/'clips'/k/'audio.pcm',data);writejson(s.root/'clips'/k/'receipt.json',receipt)
    def observations(store,job,utterances,python,model):
        rows=[]
        for u in utterances:
            r=valid_clip(s,clip_key(u,j));text=prefix+wrong
            rows.append({'utterance':u['id'],'text':text,'segments':[{'start':0,'end':2.9,'text':text}],
                         'audio_seconds':3,'audio_samples':RATE*3,'pcm_sha256':r['sha256']})
        return rows,{'model_files':{'control':'Unprompted recognizer result substitute; no real speech claim.'}},{}
    monkeypatch.setattr(cache,'observe',observations)
    result=check(s,j['id'],Path('/unused'),Path('/unused'),all_clips=True,require_review=True)
    assert result['status']=='needs_review' and result['flagged_passages']==1
    report=json.loads((s.path(j)/'qa-r1.json').read_text());row=report['rows'][0]
    assert row['wer']<.2 and row['flags']
    assert s.get(j['id']).get('audio_gate_approved_plan') is None


def test_exact_logical_payload_trace_roundtrips_unicode_and_leading_parenthesis(tmp_path):
    import httpx
    import numpy as np
    from types import SimpleNamespace
    from urllib.parse import parse_qs
    from audiobook.backend import Engine
    from audiobook.core import RATE,key
    runtime=tmp_path/'runtime';runtime.mkdir();(runtime/'backend.log').write_text('backend: Vulkan0\n')
    seen=[];audio=(np.sin(np.arange(RATE*4)*2*np.pi*200/RATE)*10000).astype('<i2').tobytes()
    def handler(request):
        seen.append(parse_qs(request.read().decode()))
        return httpx.Response(200,headers={'X-Sample-Rate':'24000','X-Sample-Format':'s16le'},content=audio)
    engine=Engine.__new__(Engine);engine.store=SimpleNamespace(root=tmp_path);engine.identity={'settings':{}}
    engine.client=httpx.Client(base_url='http://127.0.0.1:1',transport=httpx.MockTransport(handler))
    try:
        raw='(sigh) “Chen” said: <threshold> ≥ −0.08 mV.'
        _,receipt=engine.speech(raw)
        assert seen[0]['text']==[raw] and seen[0]['split_chars']==['0']
        assert receipt['request']['fields']['text']==raw
        assert receipt['request_sha256']==key(receipt['request'])
        planned=normalize('The printed label said “(sigh)”. Do not restart.')
        engine.speech(planned)
        assert seen[1]['text']==[planned] and 'open parenthesis sigh close parenthesis' in planned
    finally:engine.client.close()


def book(xhtml):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        z.writestr('book.opf','<package><metadata><title>Semantic control</title><language>en</language></metadata><manifest><item id="c" href="c.xhtml"/></manifest><spine><itemref idref="c"/></spine></package>')
        z.writestr('c.xhtml',xhtml)
    return data.getvalue()


def test_styling_note_marker_and_explicit_power_keep_distinct_source_roles():
    source='<html xmlns:epub="http://www.idpf.org/2007/ops"><body><h1>Control</h1><p>D<em>r.</em> Chen.</p><p>Use 10<a role="doc-noteref" href="#n3">3</a> volts.</p><p>Compute <math><msup><mn>10</mn><mn>3</mn></msup></math>.</p><aside epub:type="footnote" id="n3">Do not exceed the threshold.</aside></body></html>'
    doc=epub(book(source));p=plan(doc);speech=' '.join(u['text'] for u in p['utterances'])
    assert 'Doctor Chen.' in speech and '10 volts.' in speech
    assert 'Do not exceed the threshold.' in speech
    assert '10 to the power of 3' in speech
    assert sum('10 to the power of 3' in u['text'] for u in p['utterances'])==1
    claim=next(b for b in doc.blocks if 'volts' in b.source)
    assert claim.locator['inline_semantics'][0]['kind']=='note_reference'
    assert 'role="doc-noteref"' in claim.locator['exact']
    assert '103 volts.' in claim.source  # Literal display text retained separately.
    assert not p['issues']


def test_presentation_superscript_abstains_instead_of_fusing_a_number():
    doc=epub(book('<html><body><p>Use 10<sup>3</sup> volts.</p><p>Keep the final warning.</p></body></html>'))
    p=plan(doc)
    assert p['issues'] and 'superscript' in p['issues'][0]['reason']
    assert 'Keep the final warning.' in ' '.join(u['text'] for u in p['utterances'])
    assert not any('103' in u['text'] for u in p['utterances'])


def test_inline_pronunciation_and_foreign_language_are_retained_without_fake_support():
    source='<html xmlns:ssml="http://www.w3.org/2001/10/synthesis" xml:lang="en"><body><p>Meet <span ssml:ph="tʃɛn" ssml:alphabet="ipa">Chen</span>. Read <span xml:lang="fr">le renard</span>.</p></body></html>'
    doc=epub(book(source));b=doc.blocks[0];spans=b.locator['inline_semantics']
    hint=next(s for s in spans if s['kind']=='pronunciation_hint')
    assert hint['source']=='Chen' and hint['phoneme']=='tʃɛn' and hint['origin']=='inline ssml:ph' and hint['applied'] is False
    assert next(s for s in spans if s['kind']=='language')['language']=='fr'
    assert b.locator['language']=='en'
    assert 'le renard' in ' '.join(u['text'] for u in plan(doc,rule_revision='faithful-11')['utterances'])
    assert plan(doc)['issues'] and not plan(doc)['utterances']


def test_explicit_source_bound_reading_has_priority_over_a_broader_alias():
    from audiobook.core import digest
    text='They lead the group.';doc=Document('Control','control','source',[Block('b',text,{})])
    policy={'document':{'source_sha256':'source','blocks':{'b':{'action':'speak','verified':True,
        'reason':'Verified this occurrence is the verb, using its selected spoken spelling.',
        'expected_source_sha256':digest(text.encode()),'spoken':text}}}}
    p=plan(doc,policy,aliases={'lead':'led'})
    assert p['decisions'][0]['spoken']==text and p['decisions'][0]['source']==text


def test_scene_separator_never_reaches_speech_and_legacy_repair_is_source_bound():
    from audiobook.core import digest,Error
    blocks=[Block('before','The first scene ends.',{}),Block('scene','* * *',{}),Block('after','The next scene begins.',{})]
    doc=Document('Control','control','source',blocks);p=plan(doc)
    assert p['decisions'][1]['source']=='* * *' and p['decisions'][1]['action']=='pause'
    assert len(p['utterances'])==2 and p['utterances'][0]['pause_samples']>=12000
    assert all(any(c.isalnum() for c in u['text']) for u in p['utterances'])
    bound={'document':{'source_sha256':'source','blocks':{'scene':{'action':'pause','verified':True,
        'expected_source_sha256':digest(b'* * *'),'reason':'Verified isolated source scene divider.'}}}}
    legacy=plan(doc,bound,rule_revision='faithful-5')
    assert not any('*' in u['text'] for u in legacy['utterances'])
    assert any('*' in u['text'] for u in plan(doc,rule_revision='faithful-5')['utterances'])
    rule=bound['document']['blocks'].pop('scene');rule['expected_source_sha256']=digest(blocks[0].source.encode());bound['document']['blocks']['before']=rule
    with pytest.raises(Error,match='words cannot'):plan(doc,bound)
    md=plan(markdown(b'First scene.\n\n* * *\n\nNext scene.\n'))
    assert any(d['action']=='pause' for d in md['decisions'])


@pytest.mark.parametrize('extension',['md','epub','pdf'])
def test_ordinary_cli_keeps_title_address_meaning_and_literal_companion(tmp_path,extension):
    text='Meet Dr. Chen. Use 12 King St. Bring ID.'
    source=tmp_path/('source.'+extension)
    if extension=='md':source.write_text('# Control\n\n'+text)
    elif extension=='epub':source.write_bytes(book('<html><body><h1>Control</h1><p>'+text+'</p></body></html>'))
    else:
        from reportlab.pdfgen.canvas import Canvas
        canvas=Canvas(str(source));canvas.drawString(40,700,text);canvas.save()
    result=subprocess.run([sys.executable,'-m','audiobook','--home',str(tmp_path/'state'),'plan',str(source),'--json'],capture_output=True,text=True,check=True)
    p=json.loads(result.stdout);spoken=' '.join(u['text'] for u in p['utterances'])
    assert 'Doctor Chen.' in spoken and '12 King Street.' in spoken and 'Bring ID.' in spoken
    assert any('Dr. Chen.' in d['source'] for d in p['decisions'])
    assert not p['issues']
