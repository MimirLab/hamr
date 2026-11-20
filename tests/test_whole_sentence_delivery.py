import pytest

from audiobook.backend import validate_request
from audiobook.core import Block, Document, Error
from audiobook.planner import plan


def text_of_size(words):
    observations = [f'item{chr(97 + i // 26)}{chr(97 + i % 26)}' for i in range(words)]
    return 'Although the evidence matters, ' + ' '.join(observations) + ' continued.'


def test_one_intact_request_above_the_previous_sentence_budget():
    text = text_of_size(180)
    doc = Document('Example', 'text', 'source', [Block('body', text, {})])
    old = plan(doc)
    policy = {'document': {'source_sha256': 'source', 'delivery': {'whole_sentences': True}}}
    checked = plan(doc, policy)
    assert len(old['utterances']) > 1 and old['utterances'][0]['delivery_boundary'] == 'clause'
    assert [(u['text'], u['delivery_boundary']) for u in checked['utterances']] == [(text, 'sentence')]
    assert checked['issues'] == []
    validate_request(checked['utterances'][0]['text'])


def test_over_budget_sentence_holds_its_source_without_removing_the_next_sentence():
    long = text_of_size(250)
    doc = Document('Example', 'pdf', 'source', [
        Block('long', long, {}),
        Block('next', 'The next sentence remains clear.', {}),
    ])
    policy = {'document': {'source_sha256': 'source', 'delivery': {'whole_sentences': True}}}
    checked = plan(doc, policy)
    assert [u['text'] for u in checked['utterances']] == ['The next sentence remains clear.']
    assert len(checked['issues']) == 1
    assert checked['issues'][0]['source_blocks'] == ['long']


@pytest.mark.parametrize('text', [text_of_size(180).removesuffix('.'),
                                 text_of_size(180) + ' A second sentence.',
                                 'x' * 1290 + ' ends.',
                                 text_of_size(250)])
def test_extension_keeps_bounds_on_the_actual_request(text):
    with pytest.raises(Error) as info:
        validate_request(text)
    assert info.value.code == 'speech_input_limit'
