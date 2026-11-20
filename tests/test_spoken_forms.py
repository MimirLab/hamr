import pytest
from audiobook.spoken_forms import expand
from audiobook.planner import normalize
from audiobook.speech_compare import comparison_tokens


@pytest.mark.parametrize('source,expected',[
    ('Dated 03/04.','Dated zero three slash zero four.'),
    ('Use 1/4 cup.','Use one quarter cup.'),
    ('Use 3/4 cups.','Use three quarters cups.'),
    ('08:05 UTC','eight oh five U T C'),
    ('08:05','zero eight colon zero five'),
    ('an 8:5 ratio','an eight to five ratio'),
    ('§ 4.2(a)','section four point two open parenthesis a close parenthesis'),
    ('Louis XIV read Chapter XIV.','Louis the fourteenth read Chapter fourteen.'),
])
def test_supported_written_forms_have_explicit_readings(source,expected):
    assert expand(source)==expected
    assert comparison_tokens(source)==comparison_tokens(expected)


def test_identifiers_urls_full_dates_and_invalid_romans_are_not_reclassified():
    source='Room 007 runs v2.4.10. See https://site.invalid/03/04 and ISO03/04. Chapter IIII. Dated03/04/2026.'
    assert expand(source)==source


def test_new_readings_are_versioned_and_changes_of_values_still_disagree():
    assert normalize('Louis XIV at 08:05 UTC.',rule_revision='faithful-11')=='Louis XIV at 08:05 UTC.'
    assert 'fourteenth' in normalize('Louis XIV at 08:05 UTC.')
    for a,b in [('03/04','04/03'),('08:05 UTC','08:50 UTC'),('an 8:5 ratio','an 5:8 ratio'),('§ 4.2(a)','§ 4.2(b)'),('1/4 cup','3/4 cup')]:
        assert comparison_tokens(a)!=comparison_tokens(b)
