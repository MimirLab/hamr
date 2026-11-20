from audiobook.qa import missing_final_clause

PREFIX='The observer followed the animal through the clearing and recorded its movements beside the trees before returning to the quiet station at the end of the day.'
TAIL='The closing exception remains essential because the procedure must be stopped if the final independent check does not recover the expected quantity and its original sign.'


def test_whole_long_sentence_loss_is_not_excluded_by_an_arbitrary_tail_cap():
    assert len(TAIL.split())>16
    observed=PREFIX.replace('followed','tracked').replace('quiet','silent')
    assert missing_final_clause(PREFIX+' '+TAIL,observed)


def test_full_tail_or_unrelated_ending_is_not_a_long_sentence_loss():
    assert not missing_final_clause(PREFIX+' '+TAIL,PREFIX+' '+TAIL)
    assert not missing_final_clause(PREFIX+' '+TAIL,'A recognizer produced a different passage with no matching source context at all.')


def test_long_suffix_requires_a_recovered_earlier_sentence_ending():
    assert not missing_final_clause(PREFIX+' '+TAIL,PREFIX.rsplit(' ',8)[0])
