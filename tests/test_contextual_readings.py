import json

import pytest

from audiobook.cli import main
from audiobook.core import Error
from audiobook.importers import markdown
from audiobook.pipeline import critical
from audiobook.planner import plan


def events(result):
    return [event for row in result['decisions'] for event in row.get('context_events',[])]


def selected(event):
    return next(candidate for candidate in event['candidates'] if candidate['id']==event['selected'])


def speech(result):
    return ' '.join(utterance['text'] for utterance in result['utterances'])


def reading(text, **config):
    return plan(markdown(text.encode()),{'context':config})


def test_owned_occurrences_keep_distinct_numeric_roles_and_literal_scope():
    source='Room 222 opens. There are 222 books. The price is $222. Type `$222` exactly.'
    result=reading(source)
    report=events(result)
    assert [selected(e)['kind'] for e in report]==['identifier','cardinal_quantity','money_symbol']
    assert len({e['occurrence_id'] for e in report})==3
    assert speech(result)=='Room two two two opens. There are 222 books. The price is dollar sign two hundred twenty two. Type $222 exactly.'
    for event in report:
        a,b=event['expression_span']
        assert source[a:b]==event['source']
        assert event['score_kind']=='heuristic'
    assert report[-1]['field_status']['currency']=='unspecified'
    a,b=report[-1]['seed_span']
    assert source[a:b]=='$'
    assert not result['issues']


def test_literal_scopes_do_not_expand_machine_syntax_or_abbreviation():
    result=reading('Type `Dr. $222 and 1/4`. Read /tmp/v1.2.3.txt. Open https://example.net/?fee=$222.')
    assert not result['issues'] and not events(result)
    assert 'Dr. $222 and 1/4' in speech(result)
    assert 'https://example.net/?fee=$222.' in speech(result)
    assert 'open parenthesis sigh close parenthesis' in speech(reading('This says `(sigh)`.'))


def test_nearby_words_support_a_date_without_guessing_date_order():
    source='The hearing is scheduled for 03/04. Add 3/4 cup.'
    unresolved=reading(source)
    date,fraction=events(unresolved)
    assert date['route']=='literal_fallback' and not unresolved['issues']
    assert date['field_status']['month']=='unspecified'
    assert {c['fields']['date_order'] for c in date['candidates'] if 'date_order' in c['fields']}=={'MD','DM'}
    assert any(item['kind']=='nearby' for item in date['evidence'])
    assert selected(fraction)['fields']=={'numerator':'3','denominator':'4'}
    result=reading(source,conventions={'date_order':'DM'})
    assert 'April third' in speech(result) and 'three quarters cup' in speech(result)
    assert selected(events(result)[0])['fields']['year'] is None
    assert not events(reading('The hearing ended. We waited for 03/04.'))[0].get('review')


def test_tuning_phrases_and_scores_moves_a_bounded_decision():
    word='Send it to address St. John.'
    before=reading(word)
    after=reading(word,weights={'governor':14})
    assert selected(events(before)[0])['fields']['reading']=='Saint'
    assert selected(events(after)[0])['fields']['reading']=='Street'
    assert speech(before)!=speech(after)
    custom=reading('The clinic has its slot booked for 04/05.',cues={'date_topic':['clinic']},conventions={'date_order':'DM'})
    assert 'May fourth' in speech(custom)
    with pytest.raises(Error,match='short literal phrases'):
        reading(word,cues={'address':['.*']})


def test_lexicon_and_explicit_source_definition_have_different_scopes():
    configured=reading('Follow the railway line Sta. five.',cues={'address':['railway line']},lexicon={'sta.':{'address':'Station'}})
    assert 'Station five' in speech(configured)
    source='# First\n\nIn this chapter St. means State. St. records follow.\n\n# Second\n\nSt. John arrived.'
    flow=reading(source)
    output=speech(flow)
    assert 'S T means State.' in output and 'State records follow.' in output and 'Saint John arrived.' in output
    assert [selected(e)['kind'] for e in events(flow)]==['literal','authored_abbreviation','abbreviation']


def test_table_uses_distinct_unambiguous_anchors_and_does_not_cross_columns():
    table='| Date | Code |\n| --- | --- |\n| 14/04 | 99/04 |\n| 15/04 | 98/04 |\n| 03/04 | 03/04 |'
    result=reading(table)
    date,code=events(result)[-2:]
    assert selected(date)['fields']['date_order']=='DM'
    assert date['view']['row']==3 and date['view']['column']==0
    assert code['route']=='literal_fallback' and code['view']['column']==1
    assert 'Date: April third; Code: zero three slash zero four.' in speech(result)
    repeated=reading(table.replace('| 15/04 |','| 14/04 |'))
    assert events(repeated)[-2]['route']=='literal_fallback'
    conflicted=reading(table,conventions={'date_order':'MD'})
    assert 'conflict' in events(conflicted)[-2] and not conflicted['issues']


def test_source_bound_chapter_conventions_override_a_listening_default():
    doc=markdown(b'# Older section\n\nThe hearing is on 03/04.\n\n# Current section\n\nThe hearing is on 03/04.')
    policy={'context':{'conventions':{'date_order':'MD'}},'document':{'source_sha256':doc.source_sha256,
                'context':{'chapters':{'Older section':{'date_order':'DM'}}}}}
    result=plan(doc,policy)
    assert [selected(e)['fields']['date_order'] for e in events(result)]==['DM','MD']


def test_finance_and_case_sensitive_unit_contracts_reach_speech_and_qa():
    result=reading('Loss (222). There were (222) visitors. Pay €12.50, then £12.50. Link A is 5 MB. Link B is 5 Mb.',
                   conventions={'accounting_parentheses':True})
    options=[selected(event) for event in events(result)]
    assert [option['kind'] for option in options]==['accounting_negative','parenthetical_quantity','money','money','measurement','measurement']
    assert 'Loss minus two hundred twenty two.' in speech(result)
    assert 'There were, two hundred twenty two, visitors.' in speech(result)
    assert [option['fields'].get('currency') for option in options[2:4]]==['EUR','GBP']
    assert 'quantity' in critical('Loss minus two hundred twenty two.', 'Loss two hundred twenty two.')
    assert not critical('Pay twelve point five zero euros.','Pay €12.50.')
    assert 'math_or_unit' in critical('Pay twelve point five zero euros.','Pay £12.50.')
    assert 'math_or_unit' in critical('Link A is 5 MB.', 'Link A is 5 Mb.')


def test_same_colon_shape_has_clock_reference_and_duration_owners():
    result=reading('Departure 03:20. Reference 03:20. Elapsed time: 03:20.',
                   conventions={'duration_fields':'minutes_seconds'})
    assert [selected(e)['kind'] for e in events(result)]==['clock_time','reference','duration']
    assert speech(result)=='Departure time, three twenty. Reference zero three colon two zero. Elapsed time: three minutes twenty seconds.'


def test_qa_holds_a_lost_measurement_instruction_at_matching_date_and_fraction():
    exact='The hearing is scheduled for April third. Add one quarter cup of water.'
    assert 'measurement_instruction' in critical(exact,'The hearing is scheduled for April 3rd and one quarter cup of water.')
    assert 'measurement_instruction' not in critical(exact,'The hearing is scheduled for April 3rd. Please add one quarter cup of water.')
    assert 'measurement_instruction' not in critical(exact,'The hearing is scheduled for April 3rd. Add 1 quarter cup of water.')


def test_reviewer_chooses_an_existing_candidate_and_fails_closed_to_code_defaults():
    doc=markdown(b'The hearing is on 03/04.')
    result=plan(doc)
    req=result['contextual_resolution']['review_requests'][0]
    choice=next(c for c in req['candidates'] if c['fields'].get('date_order')=='DM')
    valid={'source_digest':req['source_digest'],'occurrence_id':req['occurrence_id'],'action':'select',
           'candidate_id':choice['id'],'evidence_ids':choice['evidence_ids'],'requested_context':[]}
    reviewed=plan(doc,{'context_review':[valid]})
    assert 'April third' in speech(reviewed) and events(reviewed)[0]['route']=='reviewer_selected'
    assert plan(doc,reviewed['policy'])==reviewed
    for patch in ({'candidate_id':'invented'}, {'source_digest':'stale'}, {'evidence_ids':['unknown']}, {'spoken':'December ninth'}):
        rejected=plan(doc,{'context_review':[{**valid,**patch}]})
        assert speech(rejected)==speech(result) and events(rejected)[0]['review']=='invalid_reply'
        assert rejected['policy']['context_review']==[] and not rejected['issues']
    unavailable=plan(doc,{'context_review':[]})
    assert events(unavailable)[0]['review']=='unavailable' and not unavailable['issues']
    strict=plan(doc,{'context':{'mode':'strict'}})
    assert strict['issues'][0]['kind']=='context' and len(strict['issues'])==1


def test_ordinary_legacy_abbreviation_ambiguity_is_nonblocking_only_in_automatic_mode():
    text='A letter arrived for Prof.'
    auto=reading(text)
    assert auto['decisions'][0]['abbreviations']['review'] and not auto['issues']
    assert reading(text,mode='strict')['issues']
    damaged=reading('The value is 0\ue00193.')
    assert damaged['issues'] and not damaged['utterances']


def test_context_configuration_is_pinned_to_job_until_explicit_repair(tmp_path,capsys):
    src=tmp_path/'book.md';src.write_text('The hearing is on 03/04.',encoding='utf-8')
    rules=tmp_path/'context.json';rules.write_text('{"conventions":{"date_order":"MD"}}')
    base=['--home',str(tmp_path/'state')]
    assert main([*base,'queue',str(src),'--context',str(rules),'--json'])==0
    job=json.loads(capsys.readouterr().out)['job']
    rules.write_text('{"conventions":{"date_order":"DM"}}')
    assert main([*base,'show',job,'--content','--json'])==0
    original=json.loads(capsys.readouterr().out)['plan']
    assert 'March fourth' in speech(original)
    assert main([*base,'repair',job,'--context',str(rules),'--expect-revision','1','--json'])==0
    capsys.readouterr()
    assert main([*base,'show',job,'--content','--json'])==0
    repaired=json.loads(capsys.readouterr().out)['plan']
    assert 'April third' in speech(repaired)
    assert original['contextual_resolution']['configuration_sha256']!=repaired['contextual_resolution']['configuration_sha256']


def test_compound_expressions_own_their_operands_and_units():
    source='The fee was ($2.20–$3.40)/h. The panel measures 10×20 cm. The rate was 25 bp.'
    result=reading(source,conventions={'dollar_currency':'CAD'})
    report=events(result)
    assert [event['source'] for event in report]==['($2.20–$3.40)/h','10×20 cm','25 bp']
    charge,dimensions,rate=map(selected,report)
    assert charge['fields']['start']=='2.20' and charge['fields']['end']=='3.40'
    assert charge['fields']['currency']=='CAD' and charge['fields']['denominator_unit']=='hour'
    assert dimensions['fields']=={'axes':['10','20'],'shared_unit':'cm'}
    assert rate['fields']=={'coefficient':'25','scale':'basis_point'}
    text=speech(result)
    assert 'two point two zero to three point four zero Canadian dollars per hour' in text
    assert 'ten by twenty centimeters' in text and 'twenty five basis points' in text
    assert speech(reading('Type 12 m/s exactly.'))=='Type 12 m/s exactly.'
    assert 'twelve meters per second' in speech(reading('The speed is 12 m/s.'))
    assert not critical('The rate was five percent.','The rate was 5%.')
    assert 'math_or_unit' in critical('The rate was five percent.','The rate was five per mille.')
    assert 'math_or_unit' in critical('The panel measures ten by twenty centimeters.','The panel measures ten to twenty centimeters.')
    assert 'math_or_unit' not in critical('The panel measures ten by twenty centimeters.','The panel measures 10 x 20 cm.')
    assert 'math_or_unit' in critical('The panel measures ten by twenty centimeters.','The panel measures 10 x 20 mm.')


def test_quote_and_table_conventions_are_scoped_to_their_source():
    source=('She wrote, “The record uses DD/MM: 03/04.” The hearing is on 03/04.\n\n'
            '| Date | Code |\n| --- | --- |\n| 03/04 | 03/04 |')
    doc=markdown(source.encode())
    table=next(block.id for block in doc.blocks if block.role=='table')
    result=plan(doc,{'context':{'conventions':{'date_order':'MD'}},
                    'document':{'source_sha256':doc.source_sha256,'context':{'tables':{table:{'date_order':'DM'}}}}})
    quoted,book,cell,code=[event for event in events(result) if event['source']=='03/04']
    assert [selected(e)['fields'].get('date_order') for e in (quoted,book,cell,code)]==['DM','MD','DM',None]
    assert quoted['evidence'][0]['kind']=='definition'
    assert any(e['origins']['date_order']=='table policy' for e in cell['evidence'] if e['kind']=='convention')
    assert code['route']=='literal_fallback'
    assert 'April third' in speech(result) and 'March fourth' in speech(result)


def test_typed_date_and_equation_preserve_invalid_authored_values():
    result=reading('The date is 2026-02-31. The exercise says 2 + 2 = 5.')
    date,formula=map(selected,events(result))
    assert date['fields']['calendar_validity']=='invalid_authored_value'
    assert date['fields']['day']=='31'
    assert formula['fields']['equality_rhs']=='5' and not formula['fields']['correct_source']
    assert 'two plus two equals five' in speech(result)


def test_contextual_pronunciation_keeps_source_spelling_and_defers_realization():
    result=reading("I'd finished yesterday. I'd finish if asked. Yesterday I read the book. I read it daily. I read it.")
    chosen=[selected(event) for event in events(result)]
    assert [c['kind'] for c in chosen[:2]]==['had_contraction','would_contraction']
    assert [c['fields']['sense'] for c in chosen[2:]]==['past_read','present_read',None]
    assert all(event['realization']=='existing_renderer' for event in events(result))
    assert "I'd finished yesterday." in speech(result)
    assert 'I read it daily.' in speech(result)
    assert not result['issues']


def test_authored_epub_edit_does_not_disable_context_around_literal():
    from audiobook.importers import epub
    from test_upstream_epub import book
    markup='<p><ruby>Specimen<rt>reading</rt></ruby>: Save <code>025</code>. Room 007 is ready.</p>'
    result=plan(epub(book({'c':markup},['c'])))
    assert 'Specimen: Save 025.' in speech(result)
    assert 'Room zero zero seven is ready.' in speech(result)
    assert [event['source'] for event in events(result)]==['007']
    assert events(result)[0]['view']['kind']=='prepared_reading'
    observed=[row for entry in result['decisions'] for row in entry.get('context_structure',[])]
    assert {row['kind'] for row in observed}=={'ruby','literal'}
