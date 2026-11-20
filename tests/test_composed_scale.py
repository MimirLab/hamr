"""Paired quantity readings plus retained magnitude/operator counterexamples."""
import pytest
from audiobook.speech_compare import comparison_tokens as c,quantities,mathematical_markers as math
from audiobook.pipeline import critical


@pytest.mark.parametrize('expected,observed,value',[
    ('three thousand million DNA bases','3,000 million DNA bases','3000000000'),
    ('three thousand million DNA bases','three billion DNA bases','3000000000'),
    ('a thousand million cells','1000000000 cells','1000000000'),
    ('twenty thousand million cells','20 billion cells','20000000000'),
    ('three thousand\nmillion cells','3000000000 cells','3000000000'),
    ('minus three thousand million cells','-3000000000 cells','-3000000000'),
    ('half a thousand million cells','500 million cells','500000000'),
    ('one and a half thousand million cells','1500000000 cells','1500000000'),
    ('two thousand million three hundred million cells','2300000000 cells','2300000000'),
    ('two thousand million three hundred thousand cells','2000300000 cells','2000300000'),
])
def test_conventional_composed_scale_preserves_exact_value(expected,observed,value):
    assert c(expected)==c(observed)
    assert quantities(c(expected))==[value]
    assert math(expected)==math(observed) and not critical(expected,observed)


@pytest.mark.parametrize('wrong',[
    'three thousand DNA bases','three million DNA bases','three hundred million DNA bases',
    'thirty thousand million DNA bases','three billion one DNA bases','-3000000000 DNA bases',
    '003000000000 DNA bases','3.0 billion DNA bases',
])
def test_changed_values_and_precision_still_flag(wrong):
    assert 'quantity' in critical('three thousand million DNA bases',wrong)


@pytest.mark.parametrize('separator',[',',';','/','-','—','+','×'])
def test_punctuation_and_operators_do_not_form_a_composed_scale(separator):
    expected='three thousand'+separator+'million cells'
    assert c(expected)!=c('three billion cells')
    assert math(expected).get('unparsed-scale:million')==1


def test_repeated_equal_or_ascending_effective_scales_remain_separate():
    assert quantities(c('two billion three thousand million'))==['2000000000','3000000000']
    assert quantities(c('one million and two thousand million'))==['1000000','2000000000']
    assert quantities(c('one thousand million and two thousand million'))==['1000000000','2000000000']
    assert math('ten thousand billion billion')!=math('ten thousand billion')


@pytest.mark.parametrize('scale',['trillion','quadrillion','quintillion','millions','billions'])
def test_unparsed_large_magnitude_cannot_disappear_under_low_wer(scale):
    prefix='The source includes a long surrounding explanation before naming '
    assert 'math_or_unit' in critical(prefix+'two '+scale+' cells.',prefix+'two cells.')


@pytest.mark.parametrize('fraction',['third','thirds','eighth','eighths','twentieth'])
def test_unparsed_fraction_denominator_cannot_disappear(fraction):
    assert 'math_or_unit' in critical('Keep one '+fraction+' of the sample.','Keep one of the sample.')


def test_numeric_ordinal_is_not_mistaken_for_an_unparsed_fraction():
    assert math('the third result')==math('the 3rd result')
    assert not critical('the third result','the 3rd result')
