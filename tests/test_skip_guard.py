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
        return rows,{'model_files':{'control':'Substitute ASR lifecycle control only'}},{}
    monkeypatch.setattr('audiobook.qa_cache.observe',observe)
    monkeypatch.setattr('audiobook.qa_cache.observer_identity',lambda *a:{'control':'Substitute ASR only'})
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
