from audiobook.core import Block,Document,digest
from audiobook.planner import plan
from audiobook.qa import missing_final_words
from audiobook.pipeline import critical
from audiobook.pdf_flow import insertions
import json
from pathlib import Path
import numpy as np
import pytest
from test_media import prepared
from audiobook import jobs,pipeline
from audiobook.backend import validate_pcm


PREFIX='During the summer the young animals wait at the den for their parents to return with food. Then the adult frequently takes one or'
ENDING='several of the young on exploratory trips away from the site.'


def caption_document(folio='59)',rect=None):
    return Document('Book','pdf','source',[
        Block('a','The adults lead the young away. At',{'format':'pdf','page':1,'rect':[20,90,540,190]},chapter='One'),
        Block('folio',folio,{'format':'pdf','page':1,'rect':rect or [532,40,548,53]},chapter='One'),
        Block('caption','Two adults meet beside the den.',{'format':'pdf','page':2,'rect':[62,506,554,542]},chapter='One'),
        Block('b','first the young return with them. Later they return alone.',{'format':'pdf','page':2,'rect':[62,359,568,436]},chapter='One'),
        Block('clear','The next paragraph is complete.',{'format':'pdf','page':2,'rect':[62,220,568,355]},chapter='One')],
        inventory=[{'page':1,'size':[616,1022]},{'page':2,'size':[616,1022]}])


def test_damaged_margin_folio_cannot_hide_a_caption_or_be_silently_deleted():
    doc=caption_document();old=plan(doc,rule_revision='faithful-10');current=plan(doc)
    assert not old['issues']
    assert {i['block'] for i in current['issues']}=={'a','folio','caption','b'}
    assert [u['text'] for u in current['utterances']]==['The next paragraph is complete.']
    assert [d['source'] for d in current['decisions']]==[b.source for b in doc.blocks]
    assert plan(doc,rule_revision='faithful-10')==old


def test_a_body_number_or_non_numeric_prose_is_not_treated_as_margin_noise():
    for text,rect in [('59)',[200,200,216,213]),('59) essential words',[532,40,548,53]),('59)',[20,40,540,53])]:
        doc=caption_document(text,rect);p=plan(doc)
        assert insertions(doc.blocks,doc.inventory,expanded=True,margin_numbers=True)==insertions(doc.blocks,doc.inventory,expanded=True)
        assert any(text in d['source'] for d in p['decisions'])


def test_source_verified_caption_and_folio_choices_restore_the_complete_sentence():
    doc=caption_document();caption=doc.blocks[2]
    policy={'source_sha256':'source','blocks':{
        'folio':{'action':'companion','verified':True,'reason':'Checked printed folio.'},
        'caption':{'action':'speak','verified':True,'reason':'Checked photograph caption.','expected_source_sha256':digest(caption.source.encode()),'placement':'caption_after_paragraph'}}}
    p=plan(doc,{'document':policy})
    assert not p['issues']
    assert p['utterances'][0]['text']=='The adults lead the young away. At first the young return with them. Later they return alone.'
    assert p['utterances'][1]['text']=='Illustration caption. '+caption.source


def test_mid_sentence_tail_loss_is_critical_even_below_whole_take_wer_threshold():
    expected=PREFIX+' '+ENDING
    assert missing_final_words(expected,PREFIX)
    assert 'tail' in critical(expected,PREFIX)
    long_prefix='The animals remain beside the trees throughout the entire day. '*8+PREFIX
    assert len(ENDING.split())/len((long_prefix+' '+ENDING).split())<.2
    assert missing_final_words(long_prefix+' '+ENDING,long_prefix)


def test_tail_review_abstains_for_recovered_endings_short_losses_and_unrelated_asr():
    expected=PREFIX+' '+ENDING
    for observed in [expected,expected.replace('summer','spring'), ' '.join(expected.split()[4:]), ' '.join(expected.split()[:-3]),'An unrelated recognizer sentence which has no matching source prefix or final words at all.']:
        assert not missing_final_words(expected,observed)
    assert not missing_final_words('The same few words repeat here. '*6,'The same few words repeat here. '*4)


@pytest.mark.parametrize('corroborated',[False,True])
def test_low_wer_truncation_stays_held_and_automatic_retakes_need_corroboration_and_stop_at_budget(tmp_path,monkeypatch,corroborated):
    prefix='The observer followed the animals through the forest during a quiet summer afternoon and watched their progress from a sheltered station beside the familiar clearing. '+PREFIX
    expected=prefix+' '+ENDING
    assert len(ENDING.split())/len(expected.split())<.2
    source=tmp_path/'book.md';source.write_text(expected)
    s,j=prepared(tmp_path,source);assert len(j['plan']['utterances'])==1
    original=jobs.clip_key(j['plan']['utterances'][0],j)
    def observe(store,job,utterances,python,model):
        rows=[]
        for u in utterances:
            receipt=jobs.valid_clip(store,jobs.clip_key(u,job))
            text=u['text'] if model.name=='second' and not corroborated else prefix
            rows.append({'utterance':u['id'],'text':text,'segments':[{'start':0,'end':receipt['seconds'],'text':text}],'audio_seconds':receipt['seconds'],'audio_samples':receipt['samples'],'pcm_sha256':receipt['sha256']})
        return rows,{'model_files':{'model.bin':str(model)}},{}
    monkeypatch.setattr('audiobook.qa_cache.observe',observe)
    monkeypatch.setattr('audiobook.qa_cache.observer_identity',lambda *a:{'model_files':{'model.bin':str(a[1])}})
    class Engine:
        def __init__(self,*a):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def speech(self,text,voice,seed=42):
            data=(np.sin(np.arange(6000)*2*np.pi*(440+seed)/24000)*10000).astype('<i2').tobytes()
            return validate_pcm(data)
    monkeypatch.setattr(jobs,'Engine',Engine)
    result=pipeline.run(s,j['id'],Path('/unused'),Path('/first'),second_model=Path('/second'))
    state=json.loads((s.path(j)/'pipeline.json').read_text())
    assert result['status']=='needs_review' and state['phase']=='speech_review'
    assert len(state['retakes'])==(2 if corroborated else 0)
    assert not jobs.final_path(s.get(j['id'])).exists()
    assert jobs.valid_clip(s,original) and not s.get(j['id']).get('audio_acceptances')


@pytest.mark.parametrize('expected,observed,flag',[
    ('The first vessel contains five liters of water and seven grams of powder.',
     'The first vessel contains five grams of water and seven liters of powder.','paired with a different unit'),
    ('Do not issue the final permit to Alice. Do issue the final permit to Bob.',
     'Do issue the final permit to Alice. Do not issue the final permit to Bob.','Negation is associated'),
])
def test_required_gate_holds_misassociated_values_and_negation(tmp_path,monkeypatch,expected,observed,flag):
    from audiobook import qa

    source=tmp_path/'book.md'
    source.write_text(expected)
    store,job=prepared(tmp_path,source)
    assert len(job['plan']['utterances'])==1

    def observe(store,job,utterances,python,model):
        unit,=utterances
        pcm=jobs.valid_clip(store,jobs.clip_key(unit,job))
        return ([{'utterance':unit['id'],'text':observed,'audio_seconds':pcm['seconds'],'pcm_sha256':pcm['sha256'],'audio_samples':pcm['samples'],
                  'segments':[{'text':observed,'start':0,'end':pcm['seconds']}]}],{'model_files':{}},{})

    monkeypatch.setattr('audiobook.qa_cache.observe',observe)
    result=qa.check(store,job['id'],Path('/fixture-python'),Path('/fixture-model'),require_review=True)
    saved=store.get(job['id'])
    assert result['status']=='needs_review' and not saved.get('audio_gate_approved_takes')
    assert any(flag in message for message in saved['audio_review'][0]['flags'])


@pytest.mark.parametrize('expected,first,second,category',[
    ('Do not issue the final permit to Alice. Do not issue the final permit to Bob.',
     'Do issue the final permit to Alice. Do not issue the final permit to Bob.',
     'Do not issue the final permit to Alice. Do issue the final permit to Bob.','negation'),
    ('Add two liters of water to Alice’s vessel, then pour five liters into Bob’s container.',
     'Add two of water to Alice’s vessel, then pour five liters into Bob’s container.',
     'Add two liters of water to Alice’s vessel, then pour five into Bob’s container.','math_or_unit'),
])
def test_automatic_retake_requires_a_shared_source_site(expected,first,second,category):
    assert category in pipeline.critical(expected,first) and category in pipeline.critical(expected,second)
    assert category in pipeline.corroborated(expected,first,first)
    assert not pipeline.corroborated(expected,first,second)


def test_word_substitution_needs_the_same_anchored_source_word():
    expected = 'Anne smiled wanly.'
    assert pipeline.substitution_sites(expected,'Anne smiled wandly.') == {2}
    assert pipeline.substitution_sites(expected,'And smiled wildly.') == {0,2}
    assert pipeline.substitution_sites(expected,'Anne smiled wanly.') == set()
    assert pipeline.substitution_sites(expected,'Anne smiled.') == set()
    assert pipeline.substitution_sites('Go left then right','Go right then left') == set()
    assert pipeline.substitution_sites(expected,'Jane cried loudly.') == set()


@pytest.mark.parametrize('observed,operation,expected_site,observed_site',[
    ('unlocked','replace',['locked'],['unlocked']),
    ('','delete',['locked'],[]),
])
def test_low_wer_residual_cannot_approve_a_take(tmp_path,monkeypatch,observed,operation,expected_site,observed_site):
    from audiobook import qa

    expected = 'When the family returned to the house after the storm, the caretaker explained that the door was locked and the windows were secure.'
    source = tmp_path/'book.md'
    source.write_text(expected)
    store,job = prepared(tmp_path,source)
    pcm = jobs.valid_clip(store,jobs.clip_key(job['plan']['utterances'][0],job))

    def observe(store,job,utterances,python,model):
        unit, = utterances
        transcript = unit['text'].replace('locked',observed)
        row = {'utterance':unit['id'],'text':transcript,'audio_seconds':pcm['seconds'],
               'pcm_sha256':pcm['sha256'],'audio_samples':pcm['samples'],
               'segments':[{'text':transcript,'start':0,'end':pcm['seconds']}]}
        return [row],{'model_files':{}},{}

    monkeypatch.setattr(pipeline.qa_cache,'observe',observe)
    result = qa.check(store,job['id'],Path('/unused'),Path('/unused-model'),require_review=True)
    report = json.loads(Path(result['report']).read_text())
    row, = report['rows']
    site, = row['residual_word_edits']
    assert result['status']=='needs_review' and result['wer']<.05
    assert row['flags']==[qa.RESIDUAL_WORD_REVIEW] and row['pcm_sha256']==pcm['sha256']
    assert (site['operation'],site['expected'],site['observed'])==(operation,expected_site,observed_site)
    assert site['expected_token_range']==[17,18]
    assert not store.get(job['id']).get('audio_gate_approved_takes')


@pytest.mark.parametrize('second,retake',[
    ('unlocked', True), ('locked', False), ('blocked', False),
])
def test_residual_retake_corroboration_requires_separate_observers_and_the_same_edit(
        tmp_path,monkeypatch,second,retake):
    source = tmp_path/'book.md'
    source.write_text('After the family returned from the storm, the caretaker explained that the door was locked and the windows were secure.')
    store,job = prepared(tmp_path,source)
    old = jobs.clip_key(job['plan']['utterances'][0],job)
    primary = Path('/primary')
    secondary = Path('/secondary')
    seen = []

    def identity(python,model,worker):
        return {'model_files':{'model.bin':model.name}}

    def observe(store,job,utterances,python,model):
        unit, = utterances
        clip = jobs.clip_key(unit,job)
        pcm = jobs.valid_clip(store,clip)
        heard = unit['text']
        if clip==old:
            heard = heard.replace('locked','unlocked' if model==primary else second)
        seen.append((clip,model,heard))
        row = {'utterance':unit['id'],'text':heard,'audio_seconds':pcm['seconds'],
               'pcm_sha256':pcm['sha256'],'audio_samples':pcm['samples'],
               'segments':[{'text':heard,'start':0,'end':pcm['seconds']}]}
        return [row],identity(python,model,None),{}

    class Engine:
        def __init__(self,*a): pass
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def speech(self,text,voice,seed=42):
            pcm = (np.sin(np.arange(6000)*2*np.pi*(440+seed)/24000)*10000).astype('<i2').tobytes()
            return validate_pcm(pcm)

    monkeypatch.setattr(pipeline.qa_cache,'observer_identity',identity)
    monkeypatch.setattr(pipeline.qa_cache,'observe',observe)
    monkeypatch.setattr(jobs,'Engine',Engine)
    if retake:
        def interrupt(point):
            if point=='after_automatic_retake_commit':
                raise KeyboardInterrupt

        with monkeypatch.context() as patch:
            patch.setattr(pipeline,'fault',interrupt)
            with pytest.raises(KeyboardInterrupt):
                pipeline.run(store,job['id'],Path('/unused'),primary,second_model=secondary)
        assert store.get(job['id'])['status']=='paused'
        assert jobs.valid_clip(store,old)

    result = pipeline.run(store,job['id'],Path('/unused'),primary,second_model=secondary)
    state = store.get(job['id'])
    assert any(clip==old and model==secondary for clip,model,_ in seen)
    assert bool(state['automatic_retakes'])==retake
    assert result['status']==('ready' if retake else 'needs_review')
    if retake:
        record, = state['automatic_retakes']
        assert record['reasons']==['residual_word_disagreement'] and record['previous_clip']==old
        witness = record['word_disagreement']
        assert witness['pcm_sha256']==jobs.valid_clip(store,old)['sha256']
        assert len(set(witness['observer_sha256']))==2 and witness['sites'][0][2]==['unlocked']
        assert state['audio_gate_approved_takes']==jobs.take_signature(store,state)
    else:
        assert not state.get('audio_gate_approved_takes')


def test_copied_model_cannot_confirm_its_own_disagreement(tmp_path,monkeypatch):
    source=tmp_path/'book.md'
    source.write_text('Fill this vessel with exactly five liters of clean water before final inspection.')
    store,job=prepared(tmp_path,source)
    first,second=tmp_path/'first',tmp_path/'second'
    for path in (first,second):
        path.mkdir()
        (path/'model.bin').write_bytes(b'duplicate toy model')
    from audiobook.core import filehash
    def identity(python,model,worker):return {'model_files':{'model.bin':filehash(model/'model.bin')}}
    monkeypatch.setattr(pipeline.qa_cache,'observer_identity',identity)

    calls=[]
    def observe(store,job,utterances,python,model):
        calls.append(model)
        unit,=utterances
        pcm=jobs.valid_clip(store,jobs.clip_key(unit,job))
        text=unit['text'].replace('five','seven')
        return ([{'utterance':unit['id'],'text':text,'audio_seconds':pcm['seconds'],'pcm_sha256':pcm['sha256'],'audio_samples':pcm['samples'],
                  'segments':[{'text':text,'start':0,'end':pcm['seconds']}]}],identity(python,model,None),{})

    monkeypatch.setattr(pipeline.qa_cache,'observe',observe)
    result=pipeline.run(store,job['id'],Path('/fixture-python'),first,second_model=second)
    checkpoint=json.loads((store.path(job)/'pipeline.json').read_text())
    assert result['status']=='needs_review' and checkpoint['second_observer_status']=='duplicate_or_unverified_model'
    assert calls==[first,first] and not store.get(job['id'])['automatic_retakes']
