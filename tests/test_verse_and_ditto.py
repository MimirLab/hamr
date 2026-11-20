from audiobook.importers import markdown,epub
from audiobook.planner import plan
from test_upstream_epub import book


def test_authored_lines_stay_in_one_context_with_line_breaks_and_print_wraps_do_not():
    hard=markdown(b'First deliberate line  \nSecond deliberate line.\n')
    soft=markdown(b'First printed wrap\ncontinues the same sentence.\n')
    p=plan(hard);assert len(p['utterances'])==1
    assert p['utterances'][0]['text']=='First deliberate line\nSecond deliberate line.'
    assert p['utterances'][0]['delivery_boundary']=='authored_lines'
    assert plan(soft)['utterances'][0]['text']=='First printed wrap continues the same sentence.'
    assert '\n' not in plan(hard,rule_revision='faithful-11')['utterances'][0]['text']


def test_xhtml_br_keeps_a_title_and_name_loaded_in_the_same_request():
    d=epub(book({'c':'<p>Meet Dr.<br/>Chen beside the station.</p>'},['c']))
    p=plan(d);assert len(p['utterances'])==1
    assert p['utterances'][0]['text']=='Meet Doctor\nChen beside the station.'


def test_source_defined_ditto_resolves_same_column_and_blank_stays_unspecified():
    d=markdown('The mark 〃 means same as above.\n\n| Name | Value |\n| --- | --- |\n| First | 5 |\n| Second | 〃 |\n| Third | |\n'.encode())
    p=plan(d);text=' '.join(u['text'] for u in p['utterances'])
    assert 'Name: Second; Value: 5.' in text and 'Name: Third; Value: unspecified.' in text
    assert not p['issues'] and next(b for b in d.blocks if b.role=='table').locator['rows'][2][1]=='〃'


def test_undefined_ditto_and_blank_predecessor_require_source_review():
    for prefix,rows in [('', '| First | 5 |\n| Second | 〃 |'),('〃 means same as above.\n\n','| First | |\n| Second | 〃 |')]:
        d=markdown((prefix+'| Name | Value |\n| --- | --- |\n'+rows+'\n').encode());p=plan(d)
        assert any('Ditto' in i['reason'] for i in p['issues'])
