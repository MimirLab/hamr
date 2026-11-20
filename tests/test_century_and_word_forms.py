from audiobook.pdf_fidelity import disputes
from audiobook.speech_compare import comparison_tokens,quantities


def test_ordinal_century_equivalence_preserves_different_values_and_literal_identifiers():
    for words,digits in [('twentieth','20th'),('nineteenth','19th'),('twenty-first','21st')]:
        assert comparison_tokens(words+' century')==comparison_tokens(digits+' century')
    assert quantities(comparison_tokens('twentieth century'))!=quantities(comparison_tokens('21st century'))
    assert comparison_tokens('Room 007 version20th')!=comparison_tokens('Room 7 version20th')
    assert quantities(comparison_tokens('20th century'))!=quantities(comparison_tokens('20 century'))


def test_competing_pronoun_or_letter_forms_are_reviewed_without_global_rewriting():
    for native,visual in [('I use its plan.','T use its plan.'),('I have tried.','l have tried.'),('I ONCE WORKED there.','T ONCE WORKED there.')]:
        assert any(r['kind']=='word_forms' for r in disputes(native,visual))
    assert not disputes('Let T use its value.','Let T use its value.')
    assert not disputes('I use its plan.','I use its plan.')


def test_new_word_form_diagnostic_does_not_change_source_locator_identity():
    from audiobook.pdf_regions import localize
    part={'text':'T use its plan.','locator':{'paragraph':0,'rect':[0,0,1,1]},'issue':None}
    original=dict(part['locator']);result=localize([part],'I use its plan.','T use its plan.',lambda rect:'I use its plan.')
    assert part['issue'] and part['locator']==original
    assert result['regions'][0]['critical_disputes'][0]['kind']=='word_forms'
