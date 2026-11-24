from audiobook.speech_compare import comparison_tokens as c,negations,quantities
from audiobook.speech_compare import mathematical_markers as math


def test_observed_year_pairs_need_the_same_value_and_surrounding_words():
    from audiobook.speech_compare import notation_observation

    expected = 'From the 1930s into the 1940s, and later in 1957, the figures changed.'
    observed = 'From the nineteen thirties into the nineteen forties, and later in nineteen fifty seven, the figures changed.'
    assert c(notation_observation(expected,observed))==c(expected)
    wrong_value = observed.replace('fifty seven','fifty eight')
    assert quantities(c(notation_observation(expected,wrong_value)))!=quantities(c(expected))
    assert notation_observation('in 1957 the total fell','in nineteen, fifty seven the total fell')=='in nineteen, fifty seven the total fell'
    assert notation_observation('in 1957 the total fell','she called nineteen fifty seven then')=='she called nineteen fifty seven then'


def test_book_identifier_formatting_preserves_each_digit_and_distinct_counts():
    assert c('ISBN: 9781122334455')==c('ISBN 978-1122-334455')
    assert quantities(c('ISBN: 9781122334455'))!=quantities(c('ISBN 978-1122-3344550'))
    assert quantities(c('ISBN: 9781122334455'))!=quantities(c('ISBN 978-1122-334456'))
    assert quantities(c('978 1122 334455'))==['978','1122','334455']


def test_known_orthographic_compounds_do_not_permit_arbitrary_merges():
    assert c('Dust Bowl and MathML')==c('dustbowl and Math ML')
    assert c('The textbox and two textboxes')==c('The text box and two text boxes')
    assert c('Second textbox')!=c('Second box')
    assert c('Second para')!=c('Second parrot')
    assert c('Minimal DTBook')==c('Minimal DT book')
    assert c('nowhere')!=c('now here')


def test_printed_roman_amendment_labels_match_the_spoken_number():
    ref = 'Article the fifth [Amendment III]'
    assert c(ref)==c('Article the fifth, Amendment three.')
    assert quantities(c(ref))!=quantities(c('Article the fifth, Amendment four.'))
    assert c('Article the second [Amendment XXVII - Ratified 1992]')==c(
        'Article the second, Amendment twenty seven, ratified 1992.')
    assert c('The amendment I wrote')!=c('The amendment one wrote')
    assert c('[Amendment IIII]')!=c('[Amendment four]')


def test_domain_formatting_recovers_exact_digits_without_swallowing_quantities():
    from audiobook.speech_compare import notation_observation

    expected = 'The credit is www.example007.com. The count is six.'
    observed = 'The credit is WWW example 007.com. The count is six.'
    assert c(notation_observation(expected,observed))==c(expected)
    observed = 'The credit is WWW dot example 007 dot com. The count is six.'
    assert c(notation_observation(expected,observed))==c(expected)
    assert notation_observation(expected,observed.replace('007','07'))==observed.replace('007','07')
    assert notation_observation(expected,observed.replace('007','008'))==observed.replace('007','008')
    assert notation_observation(expected,observed.replace('example 007','example and 007'))==observed.replace('example 007','example and 007')

def test_cardinal_spellings_match_values_without_changing_critical_values():
    assert c('TWO')==c('2.')
    assert c('fifty-seven and four')==c('57 and 4')
    assert c('one hundred and twenty three')==c('123')
    assert c('0.08 mg')!=c('0.80 mg')
    assert c('-0.08 mg')!=c('0.08 mg')
    assert c('\u22120.08 mg')==c('-0.08 mg')
    assert c('\u22120.08 mg')!=c('0.08 mg')
    assert c('08')!=c('8')
    assert c('2.4.10')!=c('2.4.1')
    assert c('one or two')==['#1','or','#2']
    assert c('two three')==['#2','#3']

def test_negative_contractions_and_elongated_no_preserve_polarity():
    assert c("don't restart")==c('do not restart')
    assert c("can't restart")==c('cannot restart')
    assert c("won't restart")==c('will not restart')
    assert negations(c("He doesn't know"))==1
    assert negations(c('He does know'))==0
    assert negations(c('Nnnnno, not yet'))==2
    assert c('know')!=c('no')
    assert c('for')!=c('four')
    assert quantities(c('faculty, four'))==['4']


def test_uncertainty_and_fraction_drops_are_not_hidden_by_matching_numbers():
    assert quantities(c('3.69 plus or minus 0.29'))==quantities(c('3.69 minus 0.29'))
    assert math('3.69 plus or minus 0.29')!=math('3.69 minus 0.29')
    assert c('3.69 ± 0.29')==c('3.69 plus or minus 0.29')
    assert quantities(c('one and one half pounds'))==quantities(c('one and one pounds'))
    assert math('one and one half pounds')!=math('one and one pounds')
    assert math('one quarter pounds')!=math('one half pounds')
    expected='one point two three four meters, with an uncertainty of five in the final digits'
    assert math(expected)==math('1.234 meters with uncertainty 5 in the last digits')
    assert math(expected)!=math('1.234 meters, five')
    assert math(expected)!=math('1.234 centimeters with uncertainty five in the final digits')


def test_unit_power_and_dimension_changes_remain_inspectable():
    assert math('6 square centimetres per gram')==math('6 cm² per gram')
    assert math('6 square centimeters per gram')!=math('6 centimeters per gram')
    assert math('6 square centimeters per gram')!=math('6 grams per square centimeter')
    assert math('45 degrees')==math('45°')
    assert math('one foot')==math('two feet')  # Values are checked separately.


def test_scientific_denominator_seconds_and_script_letters_are_distinct():
    assert quantities(c('per kilogram per square second'))==[]
    assert quantities(c('she came second'))==['ordinal:2']
    assert math('per square second')==math('per square seconds')
    assert math('m subscript p')!=math('m subscript e')
    assert math('alpha superscript minus one')!=math('alpha superscript one')


def test_exact_decimal_repeats_and_formula_attachments():
    assert c('three point eight six one five nine two six seven double four')==c('3.8615926744')
    assert c('3.86159267 double 4')==c('3.8615926744')
    assert c('3.86159267 double four')!=c('3.861592674')
    assert c('double four')!=c('44')
    assert math('M subscript U')==math('M subscripts U')
    assert math('lambda bar subscript capital C')!=math('lambda subscript capital C')
    assert math('subscript capital C')!=math('subscript C')
    assert math('numerator one and denominator twelve')!=math('numerator twelve and denominator one')


def test_named_currency_symbol_keeps_exact_repeated_digits_in_literal_reading():
    expected='Type dollar sign, followed by three twos.'
    assert c(expected)==c('Type dollar sign followed by 3 2s.')
    assert quantities(c(expected))==['2','2','2']
    assert quantities(c(expected))!=quantities(c('Type dollar sign followed by three threes.'))
    assert quantities(c(expected))!=quantities(c('Type dollar sign followed by four twos.'))
    assert c(expected)!=c('Type $2.22.')
    assert c('I drew three twos.')!=c('I drew two two two.')


def test_dotted_currency_names_keep_the_same_currency_in_the_audio_check():
    assert c('Pay one hundred U S dollars.')==c('Pay one hundred U.S. dollars.')
    assert math('Pay one hundred U S dollars.')==math('Pay one hundred U.S. dollars.')
    assert math('Pay one hundred U.S. dollars.')!=math('Pay one hundred Canadian dollars.')
    assert math('Pay one hundred U.S. dollars.')!=math('Pay one hundred dollars.')


def test_audio_check_resolves_asr_currency_glyphs_without_changing_amount_or_currency():
    from audiobook.pipeline import critical

    expected='Lunch cost twelve pounds and fifty pence. The last ferry was seven hundred yen.'
    assert not critical(expected,'Lunch cost £12.50. The last ferry was ¥700.')
    assert not critical(expected,'Lunch cost £12.50. The last ferry was $700 yen.')
    assert 'quantity' in critical(expected,'Lunch cost £12.51. The last ferry was ¥700.')
    assert 'quantity' in critical(expected,'Lunch cost £12.50. The last ferry was $701 yen.')
    assert 'math_or_unit' in critical(expected,'Lunch cost €12.50. The last ferry was 700 yuan.')
    assert 'math_or_unit' in critical(expected,'Lunch cost £12.50. The last ferry was 700 dollars yen.')
    assert 'math_or_unit' in critical(expected,'Lunch cost twelve pounds and fifty cents. The last ferry was 700 yen.')


def test_recognizer_can_glue_a_printed_dollar_amount_to_the_previous_word():
    from audiobook.pipeline import critical
    from audiobook.speech_compare import notation_observation

    expected = 'Doctor Gray paid one hundred dollars for the repair. The key was on the shelf.'
    raw = 'Dr. Gray paid$100 for the repair. The key was on the shelf.'
    assert notation_observation(expected, raw) == raw.replace('paid$', 'paid $')
    assert not critical(expected, raw)
    assert 'quantity' in critical(expected, raw.replace('$100', '$101'))
    assert 'math_or_unit' in critical(expected, raw.replace('$100', '£100'))
    literal = 'Type echo dollar sign one zero zero.'
    assert notation_observation(literal, 'Type echo$100.') == 'Type echo$100.'


def test_unqualified_elapsed_pair_accepts_printed_fields_without_guessing_units():
    from audiobook.pipeline import critical

    expected='Elapsed time: three twenty.'
    assert not critical(expected,'Elapsed time, 3.20.')
    assert not critical(expected,'Elapse time, 03:20.')
    assert 'quantity' in critical(expected,'Elapsed time, 3.21.')
    assert 'quantity' in critical('Elapsed time: three minutes twenty seconds.','Elapsed time, 3.20.')
    assert 'quantity' in critical('Reference three twenty.','Reference 3.20.')
def test_regional_spelling_does_not_create_a_word_error():
    from audiobook.speech_compare import comparison_tokens

    assert comparison_tokens('The harbour guide.')==comparison_tokens('The Harbor Guide')
    assert comparison_tokens('The harbour guide.')!=comparison_tokens('The Harvard guide.')
    assert comparison_tokens('Four boats entered the harbour.')!=comparison_tokens('For boats entered the harbor.')
