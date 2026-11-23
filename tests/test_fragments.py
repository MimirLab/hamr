import pytest

from audiobook import performance, planner
from audiobook.importers import markdown


OUTGOING = '“I have seen them at dinn—” she checked herself hastily.'
INCOMING = '“Where is Dinn?” asked the turtle.'


def test_named_echo_keeps_the_written_fragment_and_controls_synthesis():
    book = markdown(f'{OUTGOING}\n\n{INCOMING}'.encode())
    compiled = planner.plan(book)
    first, second = compiled['utterances']
    assert first['text'] == OUTGOING
    assert second['text'] == INCOMING
    assert performance.request(first)[0] == OUTGOING.replace('dinn','Dinn')
    assert performance.request(second)[0] == INCOMING
    first_target, = first['fragment_echo']['targets']
    second_target, = second['fragment_echo']['targets']
    assert first_target['role']=='cutoff' and second_target['role']=='reply'
    assert first['text'][slice(*first_target['span'])]=='dinn'
    assert second['text'][slice(*second_target['span'])]=='Dinn'
    assert compiled['decisions'][0]['source']==OUTGOING

    for options in ({'policy':{'performance':'off'}}, {'delivery_context':28}):
        old = planner.plan(book, **options)
        assert all('fragment_echo' not in unit for unit in old['utterances'])
        assert performance.request(old['utterances'][0])[0]==OUTGOING


def test_recognizer_spelling_is_licensed_at_the_named_echo_only():
    source = '“Din showed me at dinn—” he checked himself. “Who is Dinn?” they asked.'
    unit, = planner.plan(markdown(source.encode()))['utterances']
    heard = 'Din showed me at din, he checked himself. Who is din, they asked.'
    recognized = performance.lexical_observation(unit,heard)
    assert recognized == 'Din showed me at dinn, he checked himself. Who is dinn, they asked.'
    for changed in (heard.replace('at din','at dent'),heard.replace('showed me','hired me'),
                    heard.replace('at din','at dinner')):
        assert performance.lexical_observation(unit,changed)==changed


@pytest.mark.parametrize(('outgoing','incoming'),[
    (OUTGOING.replace('checked herself hastily','said softly'),INCOMING),
    (OUTGOING,'“Where is the dinner?” asked the turtle.'),
    (OUTGOING,'“I said Dinn twice today,” asked the turtle.'),
    (OUTGOING,'## Another chapter\n\n'+INCOMING),
    ('“Look at me sw----” and down he went.', '“Look at me swimming!” he said later.'),
])
def test_punctuation_and_nearby_words_leave_other_readings_intact(outgoing,incoming):
    doc = markdown(f'{outgoing}\n\n{incoming}'.encode())
    assert all('fragment_echo' not in unit for unit in planner.plan(doc)['utterances'])
