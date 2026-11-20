from audiobook.core import Block,Document
from audiobook.planner import normalize,plan,split
from audiobook.backend import Engine
from audiobook.core import Error
import pytest

def test_counts_are_spoken_as_asides_with_original_source_preserved():
    text='There are students (57) and faculty (four). Keep code (007).'
    assert normalize(text,rule_revision='faithful-2')==text
    assert normalize(text)=='There are students, fifty seven, and faculty, four. Keep code (007).'
    from audiobook.qa import missing_parenthetical_counts
    p=plan(Document('Book','control','sha',[Block('a',text,{})]))
    u=p['utterances'][0]
    assert u['protected_counts']==['57','four']
    assert missing_parenthetical_counts(u['text'],'There are students fifty seven and faculty.',u['protected_counts'])==['four']

def test_pause_markers_never_become_speech_requests():
    blocks=[Block('a','She waited.',{},chapter='Scene'),Block('b','. . .',{},chapter='Scene'),Block('c','Then she spoke.',{},chapter='Scene')]
    d=Document('Book','control','sha',blocks);p=plan(d)
    assert len(p['utterances'])==2
    assert p['decisions'][1]['action']=='pause' and p['decisions'][1]['source']=='. . .'
    assert 'b' in p['utterances'][0]['sources'] and p['utterances'][0]['pause_samples']>=12000
    old=plan(d,rule_revision='faithful-2');assert old['rule_revision']=='faithful-2'
    assert any('. . .' in u['text'] for u in old['utterances'])

def test_repeated_text_and_labels_get_bounded_without_losing_words():
    text='DELETE ME '*65
    pieces=split(text);assert all(len(p.split())<=18 for p in pieces)
    assert ' '.join(pieces).split()==text.split()
    assert len(split(text,rule_revision='faithful-2')[0].split())==65
    chat=normalize('####!Long_User_Name: hello ####!Long_User_Name: '+('word '*65))
    assert 'Long User Name:' in chat and '####' not in chat
    assert ' '.join(split(chat)).split()==chat.split()
    assert max(len(x.split()) for x in split(chat))<=34

def test_repeated_phrases_keep_unit_boundaries_and_source_count():
    text='Please come back. '+('DELETE ME '*33)+'Until the end.'
    pieces=split(text)
    assert pieces.count('DELETE ME')==33
    assert ' '.join(pieces).split()==text.split()
    assert not any(p.startswith('ME DELETE') for p in pieces)
    old=split(text,rule_revision='faithful-3');assert old!=pieces
    p=plan(Document('Book','control','sha',[Block('a',text,{})]))
    repeated=[u for u in p['utterances'] if u.get('literal_repeat')]
    assert len(repeated)==33 and all(u['text']=='DELETE ME' for u in repeated)
    assert all(u['sources']==['a'] and u['pause_samples']==1200 for u in repeated)

def test_short_structured_tail_keeps_context():
    text='A: '+('word '*31)+'hand'
    pieces=split(text)
    assert 'hand' not in pieces
    assert ' '.join(pieces).split()==text.split()

def test_email_delimiters_are_narratable_but_old_rules_stay_exact():
    text='From: x@example.org To: y@example.org'
    assert normalize(text)=='From: x at example dot org To: y at example dot org'
    assert normalize(text,rule_revision='faithful-2')==text
    assert normalize('##!literal')=='##!literal'

def test_backend_refuses_wordless_input_before_any_server_request():
    with pytest.raises(Error,match='only punctuation'):Engine(None,{'test':True}).speech('. . .')
