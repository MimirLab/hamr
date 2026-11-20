import pytest
from audiobook.speech_compare import comparison_tokens as c,quantities


@pytest.mark.parametrize('written,spoken',[
    ('100','a hundred'),('123','one hundred and twenty three'),
    ('1005','one thousand and five'),('30000','thirty thousand'),
    ('1200000','one million two hundred thousand'),
    ('1234567','one million two hundred and thirty four thousand five hundred and sixty seven'),
    ('0.080','zero point zero eight zero'),('-0.08','minus zero point zero eight'),
])
def test_bounded_number_spellings_compare_by_exact_value_and_decimal_precision(written,spoken):
    assert c(written)==c(spoken)


def test_distinct_identifiers_precision_and_number_sequences_do_not_collapse():
    assert c('zero zero seven')==['#0','#0','#7']
    assert c('two three')==['#2','#3']
    assert c('one and one half')==['#1','and','#1','half']
    for a,b in [('0.080','0.08'),('007','7'),('1200000','1020000'),('one thousand and five','one thousand and fifty')]:assert quantities(c(a))!=quantities(c(b))
