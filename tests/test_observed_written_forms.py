import pytest
from audiobook.speech_compare import comparison_tokens as c,notation_observation,quantities
from audiobook.pipeline import critical,corroborated


@pytest.mark.parametrize('script,observation',[
    ('The clock reads eight oh five U T C.','The clock reads 805 UTC.'),
    ('Louis the fourteenth read Chapter fourteen.','Louis the 14th read Chapter 14.'),
    ('Room 007 runs v2.4.10.','Room 0 0 7 runs V 2.4.10.'),
    ('Room 007 runs v2.4.10.','Room zero zero seven runs version 2.4.10.'),
    ('The twenty first century.','The 21st century.'),
    ('The date is zero three slash zero four.','The date is 03 slash 04.'),
    ('The clock reads eight oh five U T C.','The clock reads 8-05, UTC.'),
    ('Use an eight to five ratio.','Use an 8-5 ratio.'),
])
def test_observer_formatting_preserves_exact_typed_quantity(script,observation):
    assert quantities(c(script))==quantities(c(observation))


@pytest.mark.parametrize('left,right',[
    ('The clock reads eight oh five U T C.','The clock reads 905 UTC.'),
    ('Louis the fourteenth','Louis the fifteenth'),
    ('Room 007','Room 0 7'),('Room 007','Room 7'),
    ('v2.4.10','V 2.4.11'),('0.080','0.08'),
    ('one quarter cup','1.25 cup'),
    ('zero three slash zero four','03 slash 05'),
    ('Use an eight to five ratio.','Use an 8-6 ratio.'),
])
def test_neighboring_wrong_values_still_trigger_critical_quantity_review(left,right):
    assert 'quantity' in critical(left,right)


def test_unlabelled_digit_sequences_and_invalid_clocks_do_not_collapse():
    assert c('zero zero seven')==['#0','#0','#7']
    assert quantities(c('805 UTC'))!=quantities(c('eight oh five U T C'))
    assert c('The clock reads 2561 UTC.')!=c('The clock reads two five six one UTC.')
    assert c('Wait one second.')==['wait','#1','second']


def test_corroboration_requires_the_same_quantity_or_marker_dispute():
    expected='Set five cups beside eight tablespoons and wait ten minutes.'
    first='Set six cups beside eight tablespoons and wait ten minutes.'
    second='Set five cups beside nine tablespoons and wait ten minutes.'
    assert 'quantity' in critical(expected,first) and 'quantity' in critical(expected,second)
    assert not corroborated(expected,first,second)
    assert 'quantity' in corroborated(expected,first,first)
    first='Set five ounces beside eight tablespoons and wait ten minutes.'
    second='Set five cups beside eight tablespoons and wait ten hours.'
    assert not corroborated(expected,first,second)
    assert 'math_or_unit' in corroborated(expected,first,first)


@pytest.mark.parametrize('expected,observed',[
    ('The President\u0313s phone rang.',"The President's phone rang."),
    ('It landed at eight thirteen and fifteen seconds A M EDT.',
     'It landed at eight thirteen and fifteen seconds AM EDT.'),
    ('It landed at eight thirteen and fifteen seconds A M EDT.',
     'It landed at 813 and 15 seconds AM EDT.'),
    ('A U.S. astronaut opened the gate.','A US astronaut opened the gate.'),
    ('The café opened for order fulfilment.','The cafe opened for order fulfillment.'),
])
def test_observed_punctuation_and_spelling_keep_the_same_words(expected,observed):
    assert c(expected)==c(notation_observation(expected,observed))


@pytest.mark.parametrize('expected,observed',[
    ('A U.S. astronaut opened the gate.','A us astronaut opened the gate.'),
    ('The President\u0313s phone rang.','The Presidents phoned.'),
    ('The clock reads thirteen seconds A M EDT.','The clock reads thirteen seconds PM EDT.'),
    ('The caretaker said the door was locked.','The caretaker said the door was unlocked.'),
])
def test_equivalent_written_forms_preserve_disputed_words_and_am_pm(expected,observed):
    assert c(expected)!=c(notation_observation(expected,observed))


def test_compact_filenames_match_only_the_planned_stem_extension_and_site():
    expected = 'Open the file foo dot T E X and save it as test1 dot m d.'
    exact = 'Open the file foo.tex and save it as test1.md.'
    assert c(notation_observation(expected, exact)) == c(expected)
    assert notation_observation('foo dot T E X.', 'Foo.tex.') == 'foo dot T E X.'
    assert notation_observation('Use the file cover dot m 4 b.', 'Use the file cover.m4b.') == 'Use the file cover dot m 4 b.'

    for wrong in ('foo.txt', 'food.tex', 'foo.tex.bak', 'foo.tex.b', './foo.tex', 'foo.tex:17', 'foo'):
        observed = exact.replace('foo.tex', wrong)
        assert c(notation_observation(expected, observed)) != c(expected), wrong
    swapped = 'Open the file test1.md and save it as foo.tex.'
    assert notation_observation(expected, swapped) == swapped
    assert notation_observation(expected, 'Open the foo.tex and save it as test1.md.').startswith('Open the foo.tex')

    named = 'Open the filename test1 dot m d in Pandoc. Save it under the filename test1 dot h t m l.'
    printed = 'Open the file name, test1.md in Pandoc. Save it under the file name, test1.html.'
    assert c(notation_observation(named, printed)) == c(named)
    wrong_label = printed.replace('file name, test1.md', 'section name, test1.md')
    assert c(notation_observation(named, wrong_label)) != c(named)
