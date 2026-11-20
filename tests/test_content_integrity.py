from audiobook.qa import content_gaps,misplaced_negation,reference_leakage
from audiobook.pipeline import critical


PREFIX='The observer walked through the quiet forest and recorded the conditions beside the stream. '
SUFFIX=' The following measurements were independently checked before the final report was prepared.'


def test_below_threshold_internal_phrase_loss_has_recovered_anchors():
    expected=PREFIX+'The essential exception remains applicable.'+SUFFIX;observed=PREFIX+SUFFIX
    assert len('The essential exception remains applicable.'.split())/len(expected.split())<.2
    assert content_gaps(expected,observed) and 'internal_gap' in critical(expected,observed)
    assert not content_gaps(expected,expected)
    assert not content_gaps(expected,'An entirely unrelated recognizer result.')
    assert not content_gaps(PREFIX+'three closing words',PREFIX)


def test_moved_negation_cannot_pass_from_an_unchanged_negation_count():
    expected=PREFIX+'Do not open the hatch. Close the door.'+SUFFIX
    wrong=PREFIX+'Do open the hatch. Do not close the door.'+SUFFIX
    assert misplaced_negation(expected,wrong) and 'negation_scope' in critical(expected,wrong)
    assert not misplaced_negation('Do not open the hatch.','Do not open a hatch.')


def test_distinctive_reference_phrase_cannot_leak_into_target_without_review():
    reference='The violet marmot carried seven painted umbrellas beside the moonlit fountain.'
    expected=PREFIX+SUFFIX
    observed=expected+' The violet marmot carried seven painted umbrellas beside the moonlit fountain.'
    assert reference_leakage(expected,observed,reference)
    assert 'reference_leakage' in critical(expected,observed,reference=reference)
    assert not reference_leakage(expected,expected,reference)
    assert not reference_leakage(reference,reference,reference)


def test_measurement_unit_change_is_critical_with_unchanged_values():
    assert 'math_or_unit' in critical('Use one quarter cup.','Use one quarter tablespoon.')
