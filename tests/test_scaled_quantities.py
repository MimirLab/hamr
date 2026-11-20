"""Independent English reading pairs and consequential counterexamples."""
import pytest
from audiobook.speech_compare import comparison_tokens as c, mathematical_markers as math, quantities
from audiobook.pipeline import critical


@pytest.mark.parametrize('written,spoken',[
    ('10,000 years','ten thousand years'),
    ('12,000 people','twelve thousand people'),
    ('1,234 samples','one thousand two hundred and thirty-four samples'),
    ('100 million cells','one hundred million cells'),
    ('50 million years','fifty million years'),
    ('100 million and 20 thousand cells','one hundred million and twenty thousand cells'),
    ('2 billion three hundred million four thousand six','2300004006'),
    ('eleven hundred','1100'),
    ('nineteen hundred and ninety-nine','1999'),
    ('twelve hundred thirty-four','1234'),
    ('12 hundred and 34','1234'),
    ('hundred and sixty','160'),
    ('-10,000','minus ten thousand'),
    ('-1 million two hundred','negative one million two hundred'),
    ('1,234.50 milligrams','one thousand two hundred thirty-four point five zero milligrams'),
    ('17,500 people','seventeen and a half thousand people'),
    ('17500 people','seventeen and one half thousand people'),
    ('500000 cells','half a million cells'),
    ('1500000 cells','one and a half million cells'),
    ('750000 cells','half a million two hundred and fifty thousand cells'),
    ('-500000 cells','minus half a million cells'),
    ('twentyfold increase','20fold increase'),
    ('twenty-fivefold increase','25-fold increase'),
    ('two hundredfold increase','200fold increase'),
])
def test_equivalent_unambiguous_english_quantities(written,spoken):
    assert c(written)==c(spoken)
    assert math(written)==math(spoken)
    assert not critical(written,spoken)


@pytest.mark.parametrize('expected,wrong',[
    ('100 million','ten million'),('10,000','ten hundred'),
    ('half a million','one million'),('seventeen and a half thousand','seventeen thousand'),
    ('1500000','one and a half thousand'),('twentyfold','twofold'),
    ('-10,000','ten thousand'),('-17500','seventeen and a half thousand'),
    ('0.80 milligrams','zero point eight milligrams'),
    ('1,234.50','1234.5'),('1.50 million','1.5 million'),
    ('001000','1000'),('01,000','1000'),('0,125','0.125'),
    ('12,34','1234'),('1.234,50','1234.50'),
    ('2.4.10','2.4.1'),('version 2.4.10','version 2.4.1'),
    ('Room 008','Room 8'),
    ('one million, two','one million two'),
    ('one. Hundred cells','one hundred cells'),
    ('one hundred, twenty cells','one hundred twenty cells'),
    ('one million and two million','one million and two'),
])
def test_changed_values_or_ambiguous_notation_stay_distinct(expected,wrong):
    assert c(expected)!=c(wrong)
    assert 'quantity' in critical(expected,wrong)


def test_remaining_fraction_units_polarity_and_homophones_stay_independent():
    assert math('one and one half pounds')!=math('one and one pounds')
    assert math('half a million square centimeters')!=math('half a million centimeters')
    assert critical('half a million cells must not move','500000 cells must move')
    assert c('for')!=c('four') and c('know')!=c('no')
    assert c('unfold scaffold')==['unfold','scaffold']


def test_numeric_lists_and_repeated_scales_are_not_added_together():
    assert quantities(c('one million and two million'))==['1000000','2000000']
    assert quantities(c('one million, two hundred thousand'))==['1000000','200000']
    assert quantities(c('one hundred one hundred'))==['100','100']
    assert quantities(c('one million 3rd place'))==['1000000','ordinal:3']


def test_long_identifiers_are_opaque_and_hundred_chains_do_not_recurse():
    identifier='9'*5000
    assert c(identifier+' samples')==['#'+identifier,'samples']
    assert quantities(c('one hundred '*1200))==['100']*1200


def test_unparsed_scale_loss_remains_critical_below_overall_wer_threshold():
    prefix='The historical estimate describes a population with a very large total of '
    expected=prefix+'ten thousand billion billion billion descendants.'
    observed=prefix+'ten thousand billion billion descendants.'
    assert quantities(c(expected))==quantities(c(observed))
    assert 'math_or_unit' in critical(expected,observed)
    # Exact arithmetic for nested/repeated scales is still unsupported. Keep
    # that uncertainty visible rather than claiming the digits are equivalent.
    assert math('ten thousand billion')!=math('10000000000000')
