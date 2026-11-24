from audiobook.intake import inspect_source
from audiobook.planner import plan


ITEMS = ('The lamp stayed lit','The tide rose 3.5 feet.','The bell rang.')
EXPECTED = ['Number one, '+ITEMS[0],'Number two, The tide rose three point five feet.','Number three, '+ITEMS[2]]


def test_printed_paragraph_numbers_match_plain_markup_and_ordered_lists(tmp_path):
    sources = {
        'sequence.txt': '\n'.join(f'{i}. {text}' for i,text in enumerate(ITEMS,1)),
        'sequence.pml': '\n\n'.join(f'{i}. {text}' for i,text in enumerate(ITEMS,1)),
        'sequence.html': '<ol>'+''.join(f'<li><p>{text}</p></li>' for text in ITEMS)+'</ol>',
        'sequence.md': '\n'.join(f'1. {text}' for text in ITEMS),
    }
    for name,source in sources.items():
        path = tmp_path/name
        path.write_text(source,encoding='utf-8')
        document = inspect_source(path)
        result = plan(document)
        assert [u['text'] for u in result['utterances']]==EXPECTED, name
        original_label = next(row for row in result['decisions'] if row.get('source_numbering'))
        assert original_label['source_numbering'][0]['ordinal_values']==[1]
        assert original_label['source']==document.blocks[0].source
        if path.suffix=='.txt':
            assert original_label['source_numbering'][1]['source']=='2. '


def test_running_number_with_two_adjacent_identifiers_keeps_both(tmp_path):
    path = tmp_path/'excerpt.pml'
    path.write_text('4. The tide turned.\n\n5,6. The lights went on\n\n7. The boat arrived.',encoding='ascii')
    assert [u['text'] for u in plan(inspect_source(path))['utterances']]==[
        'Number four, The tide turned.','Numbers five and six, The lights went on','Number seven, The boat arrived.']


def test_roman_headings_convert_only_in_an_authored_sequence(tmp_path):
    path = tmp_path/'heading.html'
    path.write_text('<h1>VIII. The shore</h1><p>Body.</p><h1>IX. A new light</h1><p>Body.</p>'
                    '<h1>I. Am here</h1><p>Body.</p>',encoding='utf-8')
    document = inspect_source(path)
    result = plan(document)
    headings = [row for row in result['decisions'] if row['source']!='Body.']
    assert [row['spoken'] for row in headings]==['Eight, The shore','Nine, A new light','I. Am here']
    assert result['chapters']==['VIII. The shore','IX. A new light','I. Am here']
    assert [u['text'] for u in plan(document,rule_revision='faithful-25')['utterances']][0]=='VIII.'


def test_generated_list_ordinals_follow_saved_start_reverse_and_value(tmp_path):
    source = tmp_path/'steps.html'
    source.write_text('<ol start="8" reversed><li>The first check</li><li value="3">The next check</li>'
                      '<li>2. The final check</li></ol><p>In 1944, 5.2 feet was the reading.</p>',encoding='utf-8')
    result = plan(inspect_source(source))
    assert [u['text'] for u in result['utterances']]==[
        'Number eight, The first check','Number three, The next check','Number two, The final check',
        'In 1944, five point two feet was the reading.']
