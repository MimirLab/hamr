from audiobook.speech_compare import comparison_tokens as c,negations,quantities
from audiobook.speech_compare import mathematical_markers as math

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


def test_unit_power_and_dimension_changes_remain_inspectable():
    assert math('6 square centimetres per gram')==math('6 cm² per gram')
    assert math('6 square centimeters per gram')!=math('6 centimeters per gram')
    assert math('6 square centimeters per gram')!=math('6 grams per square centimeter')
    assert math('45 degrees')==math('45°')
    assert math('one foot')==math('two feet')  # Values are checked separately.
