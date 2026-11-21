import json

import pytest

from audiobook.cli import main
from audiobook.core import Error
from audiobook.importers import markdown
from audiobook.pipeline import critical
from audiobook.planner import plan
from audiobook.speech_compare import comparison_tokens


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
    assert speech(result)=='Room two two two opens. There are 222 books. The price is two hundred twenty two dollars. Type $222 exactly.'
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
    assert not result['issues']
    assert [selected(event)['fields']['exact_identifier'] for event in events(result)]==['/tmp/v1.2.3.txt']
    assert 'Dr. $222 and 1/4' in speech(result)
    assert 'https://example.net/?fee=$222.' in speech(result)
    assert 'open parenthesis sigh close parenthesis' in speech(reading('This says `(sigh)`.'))


def test_quoted_numeric_symbols_are_voiced_as_characters_in_a_literal_instruction():
    source='The string "3/4" contains three characters. Type "$222" exactly, including the dollar sign. Pay $222.'
    result=reading(source)
    fraction,literal_amount,cost=[selected(event) for event in events(result)]
    assert fraction['fields']['exact_characters']=='3/4' and fraction['fields']['metalinguistic']
    assert literal_amount['fields']['exact_characters']=='$222' and literal_amount['fields']['dollar_sign_is_named']
    assert cost['kind']=='money_symbol'
    assert speech(result)==('The string "three slash four" contains three characters. '
                            'Type "dollar sign, followed by three twos" exactly, including the dollar sign. '
                            'Pay two hundred twenty two dollars.')
    assert result['decisions'][0]['source']==source and not result['issues']
    assert 'pound sign, followed by four sixes' in speech(reading('Type "£6666" exactly.'))
    assert 'euro sign one two three' in speech(reading('Type "€123" exactly.'))


def test_normal_currency_readings_use_familiar_names_with_codes_kept_in_the_plan():
    source='It cost $100. The fee is $12.50. Expect $90–$110. It includes a $100 fee. The deposit is USD 100.'
    natural=reading(source,conventions={'dollar_currency':'USD'})
    assert speech(natural)==('It cost one hundred dollars. The fee is twelve dollars and fifty cents. '
                             'Expect ninety to one hundred ten dollars. It includes a one hundred dollar fee. '
                             'The deposit is one hundred dollars.')
    assert [selected(event)['fields']['currency'] for event in events(natural)]==['USD']*5
    technical=reading(source,conventions={'dollar_currency':'USD','narration_style':'technical'})
    assert 'It cost one hundred U S dollars.' in speech(technical)
    assert 'ninety to one hundred ten U S dollars' in speech(technical)
    assert 'Canadian dollars' in speech(reading('Pay CAD 100.'))
    assert 'U S dollars' in speech(reading('Pay one hundred U S dollars.'))
    source='Lunch cost £12.50. The train fare was JPY 700.'
    result=reading(source)
    assert speech(result)=='Lunch cost twelve pounds and fifty pence. The train fare was seven hundred yen.'
    assert [selected(event)['fields']['currency'] for event in events(result)]==['GBP','JPY']
    assert 'twelve point five zero British pounds' in speech(reading(source,conventions={'narration_style':'technical'}))
    assert 'one point two five yen' in speech(reading('The accounting balance was JPY 1.25.'))


def test_authored_vocal_markers_are_read_as_words_and_keep_original_punctuation():
    source='The word [laughs] appears in the transcript. The guide prints (clears throat).'
    result=reading(source)
    assert speech(result)==('The word laughs appears in the transcript. '
                            'The guide prints open parenthesis clears throat close parenthesis.')
    fields=[selected(event)['fields'] for event in events(result)]
    assert [record['exact_characters'] for record in fields]==['[laughs]','(clears throat)']
    assert all(record['performance_control'] is False for record in fields)
    assert result['decisions'][0]['source']==source and not result['issues']
    code=reading('The guide prints `[laughs]`. Ordinary words (with care) stay in parentheses.')
    assert speech(code)=='The guide prints laughs. Ordinary words (with care) stay in parentheses.'


def test_authored_redaction_is_spoken_without_reconstructing_hidden_text():
    source='The password is [REDACTED]. The account owner is [redacted]. Type [redacted] including the brackets.'
    result=reading(source)
    assert speech(result)==('The password is redacted. The account owner is redacted. '
                            'Type open bracket redacted close bracket including the brackets.')
    assert [selected(event)['kind'] for event in events(result)]==['redaction_marker','redaction_marker','literal_marker_text']
    assert result['decisions'][0]['source']==source and not result['issues']


def test_nearby_words_support_a_date_without_guessing_date_order():
    source='The hearing is scheduled for 03/04. Add 3/4 cup.'
    unresolved=reading(source)
    date,fraction=events(unresolved)
    assert date['route']=='literal_fallback' and not unresolved['issues']
    assert date['field_status']['month']=='unspecified'
    assert {c['fields']['date_order'] for c in date['candidates'] if 'date_order' in c['fields']}=={'MD','DM'}
    assert any(item['kind']=='nearby' for item in date['evidence'])
    assert selected(fraction)['fields']=={'numerator':'3','denominator':'4','bound_unit':'cup'}
    result=reading(source,conventions={'date_order':'DM'})
    assert 'April third' in speech(result) and 'three quarters of a cup' in speech(result)
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


def test_source_measurements_disambiguate_separators_only_in_their_scope():
    source=('# Flight record\n\nThe probe climbed 118.65 miles. A pass took 23.67 seconds.\n\n'
            'The distance was 26,000 feet above “high gate”. The inclination was 32.521 degrees.\n\n'
            'The coordinates were .71 degrees north.\n\n“Another report gives 26,000 feet.”\n\n'
            '# Another record\n\nThe distance was 26,000 feet.')
    result=reading(source)
    target=[event for event in events(result) if event['source']=='26,000']
    assert [selected(event)['kind'] for event in target]==['grouped_integer','literal','literal']
    assert selected(target[0])['fields']['integer_digits']=='26000'
    assert selected(target[0])['fields']['group_separator']==','
    assert any(f.get('decimal_separator')=='.' and len(f['anchors'])>=2 for f in target[0]['evidence'])
    assert 'twenty six thousand feet' in speech(result)
    assert 'thirty two point five two one degrees' in speech(result)
    assert 'zero point seven one degrees north' in speech(result)
    disputed=source.replace('23.67 seconds','23,67 seconds')
    assert all(selected(e)['kind']=='literal' for e in events(reading(disputed)) if e['source']=='26,000')


def test_linked_digit_groups_and_measurements_keep_exact_digits_and_owner():
    source='The lot contained 76\u00a0483\u00a0522 items. The coefficient was 0.453\u2009592\u202f37. The sample weighed 1\u202f234.50 kg.'
    result=reading(source)
    group,decimal,measurement=events(result)
    assert selected(group)['fields']['value']=='76483522'
    assert selected(decimal)['fields']['fraction_digits']=='45359237'
    assert selected(measurement)['fields']['value']=='1234.50'
    part=selected(measurement)['components'][0]
    assert part['source']=='1\u202f234.50' and part['fields']['scale']==2
    assert 'zero point four five three five nine two three seven' in speech(result)
    assert 'one thousand two hundred thirty four point five zero kilograms' in speech(result)
    assert result['decisions'][0]['source']==source
    assert speech(reading('The identifier is 123\u00a0456. The two labels are 123 456.'))=='The identifier is 123 456. The two labels are 123 456.'
    table=reading('| A | B |\n| --- | --- |\n| 123 | 456 |')
    assert '123456' not in speech(table) and 'thousand' not in speech(table)


def test_qa_joins_recognizer_digits_only_for_a_complete_coefficient():
    expected='The measured coefficient was zero point four five three five nine two three seven.'
    assert not critical(expected,'The measured coefficient was 0.4535 9237.')
    assert 'quantity' in critical(expected,'The measured coefficient was 0.4535 9273.')
    assert 'quantity' in critical(expected,'The measured coefficient was 0.4535 923.')
    assert 'quantity' in critical(expected,'The measured coefficient was 0.4535; 9237.')
    assert 'quantity' in critical('The reference was 0.45359237.','The reference was 0.4535 9237.')


def test_speech_qa_ignores_the_inaudible_its_apostrophe():
    expected='Vulpes vulpes, its scientific name.'
    assert comparison_tokens(expected)==comparison_tokens('Vulpes vulpes, it’s scientific name.')
    assert comparison_tokens(expected)!=comparison_tokens('Vulpes vulpes, scientific name.')
    assert comparison_tokens(expected)!=comparison_tokens('Vulpies, Vulpies, it’s scientific name.')


def test_measurement_symbols_keep_case_and_use_comparison_evidence():
    result=reading('The readings were 7 m, 31.4 kg, and 250 mm. The width was 1.0\u00a0m. The code was 4 m.')
    assert [selected(e)['fields']['unit'] for e in events(result) if selected(e)['kind']=='measurement']==[
        'meter','kilogram','millimeter','meter']
    assert 'seven meters, thirty one point four kilograms, and two hundred fifty millimeters' in speech(result)
    assert 'one point zero meter' in speech(result) and 'code was 4 m' in speech(result)
    assert 'seven meters' not in speech(reading('Elapsed time: 7 m, 4 kg.'))
    french=reading('The distance is 1,234 km.',conventions={'number_locale':'fr-FR'})
    assert 'one point two three four kilometers' in speech(french)
    assert selected(events(french)[0])['components'][0]['fields']['decimal_separator']==','
    temperature=reading('The outside temperature was 37\u00a0°C.')
    assert selected(events(temperature)[0])['fields']=={'value':'37','unit':'celsius'}


def test_same_colon_shape_has_clock_reference_and_duration_owners():
    result=reading('Departure 03:20. Reference 03:20. Elapsed time: 03:20.',
                   conventions={'duration_fields':'minutes_seconds'})
    assert [selected(e)['kind'] for e in events(result)]==['clock_time','reference','duration']
    assert speech(result)=='Departure time, three twenty. Reference zero three colon two zero. Elapsed time: three minutes twenty seconds.'


def test_duration_separates_the_class_from_its_units():
    source='Elapsed time: 03:20. Duration (hh:mm): 01:01. The second run took 1h01m01s.'
    result=reading(source)
    plain,declared,suffixed=events(result)
    assert not result['issues'] and plain['route']=='automatic_best_guess'
    assert 'Elapsed time: three twenty.' in speech(result)
    assert 'Elapsed time: zero three colon two zero.' in speech(reading(source,conventions={'narration_style':'technical'}))
    assert plain['field_status']['hours']==plain['field_status']['seconds']=='unspecified'
    assert {c['fields']['field_order'] for c in plain['candidates']}=={None,'minutes_seconds','hours_minutes'}
    assert selected(declared)['fields']['hours']==selected(declared)['fields']['minutes']=='01'
    assert declared['duration_format']['source']==' (hh:mm)'
    assert 'Duration: one hour one minute.' in speech(result)
    assert selected(suffixed)['fields']=={'hours':'1','minutes':'01','seconds':'01'}
    assert 'one hour one minute one second' in speech(result)
    assert reading('Elapsed time: 03:20.',mode='strict')['issues'][0]['reason_code']=='ambiguous_context'
    source='| Duration (mm:ss) | Duration (hh:mm) |\n|---|---|\n|01:01|01:01|'
    table=reading(source,conventions={'duration_fields':'hours_minutes'})
    assert [selected(event)['fields']['field_order'] for event in events(table)]==['minutes_seconds','hours_minutes']
    assert 'one minute one second' in speech(table) and 'one hour one minute' in speech(table)
    assert '(mm:ss)' not in speech(table) and '(hh:mm)' not in speech(table)
    technical=reading(source,conventions={'narration_style':'technical'})
    assert '(mm:ss)' in speech(technical) and '(hh:mm)' in speech(technical)


def test_colon_clock_uses_actual_travel_or_time_of_day_context_in_wrapped_plain_text():
    from audiobook.importers import plain

    source='We board the train at about\r\n05:15. Be there at 06:35 this evening. The train reference at 04:25 was printed.'
    result=plan(plain(source.encode()))
    clock_one,clock_two,reference=[selected(event) for event in events(result)]
    assert clock_one['fields']=={'hour':'05','minute':'15','zone':None}
    assert clock_two['fields']=={'hour':'06','minute':'35','zone':None}
    assert reference['kind']=='literal' and reference['fields']['components']==['04','25']
    assert 'at about five fifteen' in speech(result) and 'at six thirty five this evening' in speech(result)
    assert result['decisions'][0]['source']==source


def test_uncertainty_claims_its_unit_without_losing_precision_or_identifier_scope():
    source='The measured length is −1.2340(05) m. The product code is 1.234(5).'
    result=reading(source)
    measurement,label=events(result)
    fields=selected(measurement)['fields']
    assert fields['coefficient']=='−1.2340' and fields['coefficient_digits']=='-1.2340'
    assert (fields['decimal_places'],fields['uncertainty_last_digits'],fields['unit'])==(4,'05','m')
    a,b=measurement['seed_span']
    assert source[a:b]=='−1.2340(05)' and measurement['source']=='−1.2340(05) m'
    assert 'minus one point two three four zero meters with an uncertainty of zero five in the final digits' in speech(result)
    assert selected(label)['kind']=='label' and selected(label)['fields']['exact']=='1.234(5)'
    comma='The measured length is 1,234(5)\u00a0m.'
    assert selected(events(reading(comma))[0])['kind']=='literal'
    france=reading(comma,conventions={'number_locale':'fr-FR'})
    assert 'one point two three four meters with an uncertainty of five' in speech(france)


def test_dotted_meridiem_owns_the_clock_without_breaking_before_the_zone():
    source='Launch was 9:32 a.m. EDT. Landing was at 12:50 p.m. Then we went home.'
    result=reading(source)
    clocks=events(result)
    assert [row['source'] for row in clocks]==['9:32 a.m.','12:50 p.m.']
    assert [selected(row)['fields']['meridiem'] for row in clocks]==['AM','PM']
    assert speech(result)=='Launch was nine thirty two A M EDT. Landing was at twelve fifty P M. Then we went home.'
    assert not critical('Launch was nine thirty two A M EDT.','Launch was 9:32 a.m. EDT.')
    assert critical('Launch was nine thirty two A M EDT.','Launch was 9:32 p.m. EDT.')


def test_qa_compares_recognized_calendar_notation_and_exact_clock_fields():
    expected='Launch was July sixteenth, nineteen sixty nine; nine thirty two A M EDT.'
    assert not critical(expected,'Launch was July 16, 1969, 9.32 AM EDT.')
    assert 'quantity' in critical(expected,'Launch was July 17, 1969, 9.32 AM EDT.')
    assert 'quantity' in critical(expected,'Launch was July 16, 1968, 9.32 AM EDT.')
    assert 'quantity' in critical(expected,'Launch was July 16, 1969, 9.33 AM EDT.')
    assert 'math_or_unit' in critical(expected,'Launch was July 16, 1969, 9.32 PM EDT.')
    compact='Landing was July twenty fourth, nineteen sixty nine; twelve fifty P M EDT.'
    assert not critical(compact,'Landing was July 24, 1969, 1250 PM EDT.')
    assert not critical('Flight date was June first sixty nine.','Flight date was June 1st, 69.')
    assert 'quantity' in critical('Flight date was June first sixty nine.','Flight date was June 1st, 1969.')
    assert not critical('Flight date was June first oh nine.','Flight date was June 1st, 09.')
    assert 'quantity' in critical('A reading of 9.32 in the AM block.','A reading of 9:32 A M.')


def test_dates_use_a_whole_named_date_or_a_source_bound_chronology():
    source=('May 25, 1961, was windy. The launch was July 16, 1969.\n\n'
            '## Milestones\n\n11/21/68 – Integrated test\n\n1/18/69 – Docking\n\n6/1/69 – Readiness test\n\n'
            '## Storage\n\n6/1/69 – Rack label')
    result=reading(source)
    dates=[selected(event)['fields'] for event in events(result)]
    assert [d['year'] for d in dates]==['1961','1969','68','69','69']
    assert dates[-1]['month']=='6' and dates[-1]['day']=='1'
    assert 'May twenty fifth, nineteen sixty one, was windy.' in speech(result)
    assert 'June first sixty nine' in speech(result) and '6/1/69 – Rack label' in speech(result)
    assert any(f.get('heading',{}).get('title')=='Milestones' for f in events(result)[-1]['evidence'])
    assert not reading('## Timeline\n\n6/1/69 – Readiness test')['issues']
    assert selected(events(reading('## Timeline\n\n6/1/69 – Readiness test'))[0])['fields']['month'] is None


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
    assert 'two Canadian dollars and twenty cents to three Canadian dollars and forty cents per hour' in text
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

    contrast=reading('The note was read daily. She asked me to read yesterday.')
    assert [selected(e)['fields']['sense'] for e in events(contrast)]==['past_read','present_read']
    assert speech(contrast)=='The note was read daily. She asked me to read yesterday.'


def test_optional_citation_expansion_preserves_sentence_joins_and_literal_mentions():
    source='Smith et al. report the result. We first read Chen et al. Their findings agree. Print et al. exactly as written.'
    unexpanded=reading(source)
    assert speech(unexpanded)==source
    assert [span['text'] for span in unexpanded['decisions'][0]['sentence_spans']]==[
        'Smith et al. report the result.','We first read Chen et al.','Their findings agree.','Print et al. exactly as written.']
    expanded=reading(source,conventions={'expand_citations':True})
    assert speech(expanded)==('Smith and others report the result. We first read Chen and others. '
                              'Their findings agree. Print et al. exactly as written.')
    assert expanded['decisions'][0]['source']==source


def test_shell_language_requires_context_and_code_tokens_keep_source_shape():
    source='Run echo "$HOME". Use the option --no-cache. The label reads $HOME.'
    result=reading(source)
    shell,option,unknown=[selected(event) for event in events(result)]
    assert shell['fields']['exact_characters']=='$HOME' and shell['fields']['source_language']=='shell'
    assert option['fields']['exact_characters']=='--no-cache' and option['fields']['leading_hyphens']==2
    assert 'source_language' not in unknown['fields']
    assert speech(result)=='Run echo "dollar sign HOME". Use the option double dash no dash cache. The label reads $HOME.'
    assert result['decisions'][0]['source']==source


def test_coordinated_read_keeps_the_verb_form_of_its_own_phrase():
    source=('He opened the envelope and read the note. She will open the envelope and read the note. '
            'Then arose a quiet, thoughtful guest, whose face showed no surprise at the story, and read a poem.')
    result=reading(source)
    found=[row for row in events(result) if row['source']=='read']
    assert [selected(row)['fields']['sense'] for row in found]==['past_read','present_read','past_read']
    assert all(row['realization']=='existing_renderer' for row in found)
    for row,verb in zip(found,('opened','open','arose')):
        evidence=row['evidence'][0]['cues'][0]
        assert evidence[2]==verb and source[evidence[3]:evidence[4]]==verb
    assert speech(result)==source
    adjective=reading('I keep my folded copy and read at night.')
    assert not any(selected(e)['fields'].get('sense')=='past_read' for e in events(adjective))
    infinitive=reading('I opened the file to check the title and read several pages.')
    assert not any(selected(e)['fields'].get('sense')=='past_read' for e in events(infinitive))


def test_reading_sense_uses_auxiliaries_and_leaves_plain_ambiguity_open():
    source=('She had evidently read the note. It does not read like him. “May I read it?” She then read it. '
            'When she read it, she smiled. “Read it aloud.” I’ve already read it. They insist that she read it.')
    result=reading(source)
    found=[row for row in events(result) if row['source'].casefold()=='read']
    assert [selected(row)['fields']['sense'] for row in found]==[
        'past_read','present_read','present_read','past_read','past_read','present_read','past_read',None]
    assert len(result['contextual_resolution']['review_requests'])==1 and not result['issues']
    final=found[-1]
    assert {c['fields']['sense'] for c in final['candidates']}=={None,'past_read','present_read'}
    assert speech(result)==source
    assert reading('I read it.',mode='strict')['issues']


def test_reading_sense_uses_perfect_and_imperative_context_in_plain_text():
    from audiobook.importers import plain

    source=("I've so often read it before. The witness had twice\r\nread the note. "
            "'Herald, read the announcement!' she said. What do you read? I read it.")
    result=plan(plain(source.encode()))
    senses=[selected(event)['fields']['sense'] for event in events(result) if event['source']=='read']
    assert senses==['past_read','past_read','present_read','present_read',None]
    assert result['decisions'][0]['source']==source
    assert 'I read it.' in speech(result) and not result['issues']
    assert not [row for row in events(reading('Ms. Read inspected the order.')) if row['source']=='Read']


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
    assert {row['kind'] for row in observed}=={'ruby','literal','sentence_stop'}


def test_component_ids_keep_the_written_owner_and_do_not_materialize_twice():
    result=reading('Water is H₂O. Evaluate 8−5. The train departs at 3:20PM.')
    chosen=[(event,selected(event)) for event in events(result)]
    parts={part['event_id']:(event,part) for event,candidate in chosen for part in candidate.get('components',[])}
    chemical,subscript=parts['CR-N036']
    assert chemical['source']=='H₂O' and subscript['source']=='₂'
    assert subscript['fields']=={'element':'H','atom_count':'2'}
    assert parts['CR-N006'][1]['source']=='−5' and parts['CR-N006'][1]['fields']['operator']=='subtract'
    assert parts['CR-N016'][1]['source']=='3:20' and parts['CR-N016'][1]['fields']['meridiem']=='PM'
    assert speech(result)=='Water is H two O. Evaluate eight minus five. The train departs at three twenty P M.'


def test_marked_scope_selects_layout_and_named_conventions_without_rewriting_raw_symbols():
    result=reading('The count is 三. The notation is 12¤abc. The estimate is two million. The song title is Two Million.')
    chosen=[selected(event) for event in events(result)]
    assert [entry['kind'] for entry in chosen]==['numeral','unknown_numeric_form','lexical_quantity','name']
    assert 'count is three' in speech(result) and '12¤abc' in speech(result)
    assert chosen[2]['fields']['exact_value']=='2000000'
    assert 'song title is Two Million' in speech(result)
    assert '🙂' in speech(reading('The glyph is 🙂.',conventions={'emoji_speech':'literal'}))
    assert any('🙂' in d['source'] for d in reading('🙂',conventions={'emoji_speech':'localized_name'})['decisions'])
    assert speech(reading('🙂',conventions={'emoji_speech':'localized_name'}))=='slightly smiling face'
    assert speech(reading('Nf3',conventions={'notation':'chess'}))=='knight f 3'
    assert speech(reading('Nf3',conventions={'notation':'inventory'}))=='Nf3'
    assert speech(reading('A→B',conventions={'notation':'propositional_implication'}))=='A implies B'


def test_email_and_path_keep_exact_identifier_boundaries():
    message='Contact help@example.org for details. Write help@example.org character by character. Open ./notes/chapter.md.'
    result=reading(message)
    readings=[selected(e) for e in events(result)]
    assert [r['kind'] for r in readings]==['email_reading','literal_identifier','filesystem_path']
    assert [r['fields']['exact_identifier'] for r in readings]==['help@example.org','help@example.org','./notes/chapter.md']
    assert 'Contact help at example dot org for details.' in speech(result)
    assert 'Write help@example.org character by character.' in speech(result)
    assert 'Open ./notes/chapter.md.' in speech(result)
    assert not critical('Contact help at example dot org for details.','Contact help at example.org for details.')
    assert 'math_or_unit' in critical('The train departs at three twenty P M.','The train departs at 3.20 a.m.')
    assert not critical('The train departs at three twenty P M.','The train departs at 3.20pm.')
    assert 'quantity' in critical('The distance is 3.20 pm.','The distance is three twenty P M.')


def test_epub_markup_records_author_roles_with_exact_source_spans():
    from audiobook.importers import epub
    from test_upstream_epub import book
    markup=('<p>They asked <span style="text-transform: uppercase">us</span> to keep <em>not</em> in the sentence. '
            '<cite>The Voyage</cite> is <a href="#appendix">here</a>.</p>'
            '<p>Claim<a epub:type="noteref" href="#n1">¹</a>. '
            'The formula is <math><msup><mi>x</mi><mn>2</mn></msup></math>. '
            'The vector is <math><mi intent=":vector">v</mi></math>.</p>'
            '<p id="appendix">Appendix text.</p><aside epub:type="footnote" id="n1">Remember the full claim.</aside>')
    result=plan(epub(book({'c':markup},['c'])))
    annotated=[(decision,entry) for decision in result['decisions'] for entry in decision.get('context_structure',[])
               if 'event_id' in entry]
    by_kind={entry['selected_reading']['kind']:entry for _,entry in annotated}
    assert {'underlying_word_reading','contrastive_emphasis','title_style','visible_link_label',
            'note_reference','mathematical_superscript','vector_symbol'}<=by_kind.keys()
    for decision, entry in annotated:
        first,last=entry['source_span']
        assert decision['source'][first:last]==entry['source']
    assert by_kind['note_reference']['selected_reading']['fields']['marker_value']=='1'
    assert 'They asked us to keep not in the sentence.' in speech(result)
    assert 'x to the power of 2' in speech(result)
    assert speech(result).count('Remember the full claim.')==1


def test_epub_inline_fraction_binds_both_text_leaves_and_skips_comments():
    from bs4 import BeautifulSoup, Comment
    from audiobook.importers import epub
    from test_upstream_epub import book

    markup='<p>  Add 3/<!-- editorial comment --><em>4</em> cup. </p>'
    data=book({'recipe':markup},['recipe','recipe'])
    result=plan(epub(data))
    body=BeautifulSoup('<body>'+markup+'</body>','xml').body
    assert not result['issues'] and speech(result).count('Add three quarters of a cup.')==2
    assert 'editorial comment' not in speech(result)
    fractions=[event for event in events(result) if event['source']=='3/4']
    assert len(fractions)==2 and len({event['occurrence_id'] for event in fractions})==2
    for index,event in enumerate(fractions,1):
        locations=event['source_leaves']
        assert locations['path']=='recipe.xhtml' and locations['spine_occurrence']==index
        pieces=[]
        nodes=list(body.descendants)
        for leaf in locations['segments']:
            node=nodes[leaf['node_rank']]
            assert not isinstance(node,Comment)
            start,stop=leaf['node_span']
            pieces.append(str(node)[start:stop])
        assert pieces==['3/','4']
        assert [leaf['block_span'] for leaf in locations['segments']]==[[4,6],[6,7]]


def test_epub_uses_the_authored_column_reference_and_keeps_ditto_lineage():
    from audiobook.importers import epub
    from test_upstream_epub import book

    markup=('<table><tr><th id="place">Place</th><th id="revenue">Revenue ($ millions)</th></tr>'
            '<tr><td id="paris">Paris</td><td headers="revenue">222</td></tr>'
            '<tr><td id="previous">〃</td><td headers="place">333</td></tr>'
            '<tr><td>〃</td><td>444</td></tr></table>')
    doc=epub(book({'c':markup},['c']))
    table=next(b for b in doc.blocks if b.role=='table')
    policy={'context':{'conventions':{'dollar_currency':'USD'}},'document':{
        'source_sha256':doc.source_sha256,'context':{'tables':{table.id:{'ditto_marker':'ditto'}}}}}
    result=plan(doc,policy)
    assert not result['issues'] and 'Place: 333' in speech(result)
    assert 'three hundred thirty three million' not in speech(result)
    amount=next(event for event in events(result) if event['source']=='222')
    money=selected(amount)['components'][0]
    assert money['fields']['header_source']=={'block':table.id,'row':0,'column':1,'id':'revenue'}
    assert any(fact.get('origin')=='authored table header reference' for fact in amount['evidence'])
    dittos=[entry['fields'] for decision in result['decisions'] for entry in decision.get('context_structure',[])
            if entry['kind']=='inherited_cell']
    assert dittos[0]['antecedent']['id']=='paris'
    assert dittos[1]['antecedent']['id']=='previous' and dittos[1]['value_source']==dittos[0]['antecedent']


def test_epub_table_header_references_do_not_borrow_an_unowned_column():
    from audiobook.importers import epub
    from test_upstream_epub import book

    markup=('<table><tr><th id="a">Code</th><th id="b">Revenue ($ millions)</th></tr>'
            '<tr><td headers="b">222</td><td headers="a">333</td></tr></table>')
    options={'context':{'conventions':{'dollar_currency':'USD'}}}
    result=plan(epub(book({'c':markup},['c'])),options)
    assert not result['issues']
    assert 'Revenue: two hundred twenty two million dollars' in speech(result)
    assert 'Code: three three three' in speech(result)
    scaled=next(event for event in events(result) if event['source']=='222')
    assert scaled['scale_format']['source']==' ($ millions)'
    assert scaled['scale_format']['rendered_as']=='spoken_scale_and_currency'
    technical={'context':{'conventions':{'dollar_currency':'USD','narration_style':'technical'}}}
    assert 'Revenue ($ millions): two hundred twenty two million U S dollars' in speech(plan(epub(book({'c':markup},['c'])),technical))
    assert 'Revenue ($ millions)' in speech(plan(epub(book({'c':markup},['c']))))
    for broken in ('missing','a b'):
        release=plan(epub(book({'c':markup.replace('headers="b"',f'headers="{broken}"')},['c'])),options)
        assert release['issues'] and 'Authored table header references' in release['issues'][0]['reason']
    duplicate=plan(epub(book({'c':markup+'<p id="b">Duplicate identifier.</p>'},['c'])),options)
    assert 'Authored table header references' in duplicate['issues'][0]['reason']
    mismatch=markup.replace('</table>','<tr><td>〃</td><td>444</td></tr></table>')
    doc=epub(book({'c':mismatch},['c']))
    table=next(b.id for b in doc.blocks if b.role=='table')
    options['document']={'source_sha256':doc.source_sha256,'context':{'tables':{table:{'ditto_marker':'ditto'}}}}
    assert 'crosses different authored table columns' in plan(doc,options)['issues'][0]['reason']


def test_ditto_and_scale_keep_table_coordinates_and_header_lineage():
    source='| Site | Revenue ($ millions) |\n| --- | --- |\n| Paris | 222 |\n| " | 333 |'
    doc=markdown(source.encode())
    table=next(b.id for b in doc.blocks if b.role=='table')
    source_policy={'source_sha256':doc.source_sha256,'context':{'tables':{table:{'ditto_marker':'double_quote'}}}}
    policies={'context':{'conventions':{'dollar_currency':'USD'}},'document':source_policy}
    result=plan(doc,policies)
    assert 'Site: Paris; Revenue' in speech(result) and 'Site: "; Revenue' not in speech(result)
    note=next(entry for decision in result['decisions'] for entry in decision.get('context_structure',[]) if entry['kind']=='inherited_cell')
    assert note['fields']['value']=='Paris' and note['fields']['antecedent']=={'table':table,'row':1,'column':0}
    money=next(selected(event) for event in events(result) if event['source']=='222')['components'][0]
    assert money['fields']['header_source']=={'block':table,'row':0,'column':1}
    assert money['fields']['scale_multiplier']=='1000000' and money['fields']['currency']=='USD'
    assert 'Site: "; Revenue' in speech(plan(doc))


def test_mixed_script_occurrence_retains_codepoints():
    result=reading('The exact name is pаypal. The comparison token is paypal.')
    event,=events(result)
    candidate=selected(event)
    character,=candidate['components']
    assert character['fields']=={'retain_character':'а','script':'Cyrillic','unicode':'U+0430'}
    assert 'pаypal' in speech(result) and 'paypal' in speech(result)


def test_natural_narration_and_opt_in_technical_readouts_keep_the_same_fields():
    source='Add 3/4 cup of flour. This costs $12.50. A ticket costs €12.50.'
    natural=reading(source)
    technical=reading(source,conventions={'narration_style':'technical'})
    assert speech(natural)=='Add three quarters of a cup of flour. This costs twelve dollars and fifty cents. A ticket costs twelve euros and fifty cents.'
    assert speech(technical)=='Add three quarters cup of flour. This costs dollar sign twelve point five zero. A ticket costs twelve point five zero euros.'
    assert [selected(e)['fields'] for e in events(natural)]==[selected(e)['fields'] for e in events(technical)]
    assert not critical('This costs twelve dollars and fifty cents.','This costs $12.50.')
    assert not critical('This costs twelve euros and fifty cents.','This costs €12.50.')
    assert 'quantity' in critical('This costs twelve euros and fifty cents.','This costs €12.00.')
    assert 'math_or_unit' in critical('This costs twelve euros and fifty cents.','This costs $12.50.')
    assert 'quantity' in critical('The depth is 2.50 meters.','The depth is 2.5 meters.')


def test_fraction_like_fragments_in_two_cells_get_a_companion_disposition():
    table='| Left | Right |\n| --- | --- |\n| 3 | /4 |'
    result=reading(table)
    decision,=result['decisions']
    assert decision['action']=='companion'
    assert '3 | /4' in decision['source']
    observation,=decision['context_structure']
    assert observation['selected_reading']['kind']=='companion_object'
    assert observation['fields']['fragments']==[{'row':1,'columns':[0,1],'values':['3','/4']}]
    assert 'The full table is available in the companion.' in speech(result)
    assert not events(result) and not result['issues']
    assert 'three quarters of a cup' in speech(reading('Add 3/4 cup of flour.'))


def test_native_name_watch_list_preserves_the_word_and_allows_a_verified_alias():
    source='Zhalorin entered the hall. Maralin followed.'
    assert not events(reading(source))
    result=reading(source,native_names=['Zhalorin'])
    artifact,=events(result)
    assert selected(artifact)['kind']=='backend_native_fallback'
    assert artifact['realization']=='existing_renderer'
    assert speech(result)==source and not result['issues']
    verified=plan(markdown(source.encode()),{'context':{'native_names':['Zhalorin']}},aliases={'Zhalorin':'Zalorin'})
    assert not events(verified) and 'Zalorin entered' in speech(verified)
