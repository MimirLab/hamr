import json
from audiobook.core import Block,Document,digest
from audiobook.continuous import passages
from audiobook.planner import plan


def words(parts):return ' '.join(p['text'] for p in parts).split()


def test_long_connected_paragraph_stays_together_within_byte_and_word_budgets():
    text=' '.join(['The fox family returned to the same familiar den each spring.']*8)
    assert len(text.split())>65 and len(passages(text))==1
    assert words(passages(text))==text.split()


def test_unicode_byte_limits_long_token_and_protected_units_never_truncate():
    for text in ['This is élan. '*180,'Word '+'界'*500+' last.', 'The threshold remains greater than or equal to 0.08 milligrams. '*50]:
        parts=passages(text)
        assert words(parts)==text.split()
        assert all(len(p['text'].encode())<=1000 or p['over_budget'] for p in parts)
    assert any(p['over_budget'] for p in passages('Word '+'界'*500+' last.'))


def test_connected_pdf_pages_and_deferred_caption_preserve_all_source_and_words():
    blocks=[Block('a','Because adult foxes in the wild usually',{'format':'pdf','page':1}),
            Block('caption','A parent rests by the den.',{'format':'pdf','page':1}),
            Block('b','live only three to seven years. The den outlives them.',{'format':'pdf','page':2})]
    doc=Document('Book','pdf','source',blocks)
    policy={'source_sha256':'source','delivery':{'flow':'continuous'},'blocks':{'caption':{'action':'speak','verified':True,'reason':'Visible caption beside a photograph.','expected_source_sha256':digest(blocks[1].source.encode()),'placement':'caption_after_paragraph'}}}
    p=plan(doc,{'document':policy},rule_revision='faithful-5')
    assert p['utterances'][0]['text']=='Because adult foxes in the wild usually live only three to seven years. The den outlives them.'
    assert p['utterances'][1]['text']=='Illustration caption. A parent rests by the den.'
    assert [d['source'] for d in p['decisions']]==[b.source for b in blocks]
    assert p['utterances'][0]['sources']==['a','b'] and not p['issues']


def test_unresolved_essential_region_separates_clear_work_instead_of_joining_over_a_hole():
    doc=Document('Book','markdown','source',[Block('a','First clear sentence.',{}),Block('b','Unreadable important clause.',{},issue='Verify this region.'),Block('c','Last clear sentence.',{})])
    p=plan(doc)
    assert [u['text'] for u in p['utterances']]==['First clear sentence.','Last clear sentence.']
    assert [i['block'] for i in p['issues']]==['b']


def test_caption_never_leaks_to_the_following_chapter_and_legacy_keeps_its_exact_plan():
    blocks=[Block('a','An unfinished thought',{},chapter='First'),Block('c','A caption.',{},chapter='First'),Block('b','Second chapter.',{},chapter='Second')]
    doc=Document('Book','markdown','source',blocks);old=plan(doc,rule_revision='faithful-6')
    policy={'source_sha256':'source','delivery':{'flow':'continuous'},'blocks':{'c':{'action':'speak','verified':True,'reason':'Known caption.','expected_source_sha256':digest(blocks[1].source.encode()),'placement':'caption_after_paragraph'}}}
    p=plan(doc,{'document':policy},rule_revision='faithful-6')
    assert next(u for u in p['utterances'] if 'caption' in u['text'])['chapter']=='First'
    assert plan(doc,rule_revision='faithful-6')==old


def test_unsplittable_source_token_reaches_review_before_backend():
    doc=Document('Book','markdown','source',[Block('a','Prefix '+'界'*500+' suffix.',{})])
    p=plan(doc)
    assert p['issues'] and all(len(u['text'].encode())<=1000 for u in p['utterances'])
    assert p['decisions'][0]['source']==doc.blocks[0].source


def test_unannotated_caption_interruption_reaches_review_even_if_its_words_are_readable():
    blocks=[Block('a','The pups have one impor-',{'format':'pdf','page':1,'rect':[20,30,400,90]}),
            Block('caption','A parent rests by the den.',{'format':'pdf','page':2,'rect':[20,400,400,420]}),
            Block('b','tant exception. Their tails are white.',{'format':'pdf','page':2,'rect':[20,100,400,180]})]
    doc=Document('Book','pdf','source',blocks,inventory=[{'page':1,'size':[600,800]},{'page':2,'size':[600,800]}]);p=plan(doc)
    assert len(p['issues'])==3 and not p['utterances']
    assert all('reading order' in i['reason'] for i in p['issues'])


def test_cross_page_hyphen_is_reviewed_without_inventing_or_erasing_lexical_meaning():
    doc=Document('Book','pdf','source',[Block('a','He will re-',{'format':'pdf','page':1}),Block('b','sign the contract.',{'format':'pdf','page':2})])
    p=plan(doc)
    assert len(p['issues'])==2 and not p['utterances']


def test_repairing_one_paragraph_does_not_reframe_the_rest_of_a_chapter():
    blocks=[Block(str(i),' '.join([f'The fox found shelter in region {i}.']*5),{}) for i in range(10)]
    doc=Document('Book','markdown','source',blocks);before=plan(doc);blocks[1].source+=' A newly verified sentence.';after=plan(doc)
    assert [u['text'] for u in before['utterances'] if '1' not in u['sources']]==[u['text'] for u in after['utterances'] if '1' not in u['sources']]
    assert len(before['utterances'])==10 and len(after['utterances'])==10


def test_ornamental_ocr_cannot_hide_an_interleaved_caption_review():
    blocks=[Block('a','The animal began to inves-',{'format':'pdf','page':1,'rect':[20,30,400,90]}),
            Block('noise','- -, -',{'format':'pdf','page':2,'rect':[20,700,400,730]}),
            Block('caption','A parent rests by the den.',{'format':'pdf','page':2,'rect':[20,400,400,420]}),
            Block('b','tigate. The wind was strong.',{'format':'pdf','page':2,'rect':[20,100,400,180]})]
    doc=Document('Book','pdf','source',blocks,inventory=[{'page':1,'size':[600,800]},{'page':2,'size':[600,800]}]);p=plan(doc)
    assert any('reading order' in i['reason'] for i in p['issues'])


def test_complete_sentence_and_unfinished_tail_are_never_loaded_as_one_request():
    text='The first sentence is complete. Then an unfinished source fragment'
    parts=passages(text,separate_incomplete=True)
    assert [p['text'] for p in parts]==['The first sentence is complete.','Then an unfinished source fragment']
    assert parts[1]['boundary']=='incomplete'


def test_bounded_long_sentence_is_preserved_and_continuation_never_mixes_with_next_sentence():
    sentence=' '.join(['fox']*145)+'.'
    assert len(passages(sentence,separate_incomplete=True,max_sentence_words=160))==1
    split=passages(' '.join(['fox']*200)+'. A separate full sentence.',separate_incomplete=True)
    assert split[-1]['text']=='A separate full sentence.'
    assert words(split)==(' '.join(['fox']*200)+'. A separate full sentence.').split()


def test_known_source_hole_withholds_a_dangling_tail_instead_of_synthesizing_it():
    doc=Document('Book','pdf','source',[Block('a','A complete sentence. Then an unfinished phrase',{'format':'pdf','page':1}),Block('hole','',{'format':'pdf','page':2},issue='Source region is missing.'),Block('b','A new complete paragraph.',{'format':'pdf','page':3})])
    p=plan(doc)
    assert [u['text'] for u in p['utterances']]==['A complete sentence.','A new complete paragraph.']
    assert any('unfinished source sentence' in i['reason'] for i in p['issues'])


def test_long_unfinished_sentence_is_held_in_full_at_a_known_source_hole():
    tail=' '.join(['unfinished']*200)
    doc=Document('Book','pdf','source',[Block('a','A complete sentence. '+tail,{'format':'pdf','page':1}),Block('hole','',{'format':'pdf','page':2},issue='Source region is missing.')])
    p=plan(doc)
    assert [u['text'] for u in p['utterances']]==['A complete sentence.']
    assert any('unfinished source sentence' in i['reason'] for i in p['issues'])


def test_verified_standalone_synopsis_never_runs_into_the_following_body():
    blocks=[Block('synopsis','Selection — Sex, age and death — Extinction',{}),Block('body','I once worked in a soap factory.',{})]
    doc=Document('Book','pdf','source',blocks)
    policy={'source_sha256':'source','blocks':{'synopsis':{'action':'speak','verified':True,'reason':'Source layout separates a chapter synopsis from its opening paragraph.','expected_source_sha256':digest(blocks[0].source.encode()),'placement':'standalone'}}}
    p=plan(doc,{'document':policy})
    assert [u['text'] for u in p['utterances']]==[blocks[0].source,blocks[1].source]
    assert p['utterances'][0]['pause_samples']==8400
