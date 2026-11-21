from test_upstream_epub import book,speech
from audiobook.importers import epub
from audiobook.planner import plan


def test_ruby_is_one_representation_and_explicit_gloss_stays_distinct():
    d=epub(book({'c':'<p>The <ruby>base<rp>(</rp><rt>reading</rt><rp>)</rp></ruby> remains.</p><p>A <ruby>term<rt role="definition">meaning</rt></ruby> follows.</p>'},['c']))
    p=plan(d)
    assert speech(p)=='The base remains. A term, meaning meaning follows.'
    assert 'reading' in d.blocks[0].source
    assert any(s['kind']=='ruby' and s['annotation']=='reading' for s in d.blocks[0].locator['inline_semantics'])


def test_ruby_kana_replaces_only_complete_authored_base_groups():
    markup=('<p>We saw <ruby><rb>漢</rb><rp>(</rp><rt>かん</rt><rp>)</rp><rb>字</rb><rt><em>じ</em></rt></ruby> today.</p>'
            '<p>Another word was <ruby>東京<rt>とうきょう</rt></ruby>.</p>'
            '<p>Keep <ruby>漢字2026<rt>かんじ</rt></ruby> and <ruby>漢<rt>かん</rt>字</ruby> whole.</p>'
            '<p>A literal <code><ruby>漢字<rt>かんじ</rt></ruby></code> follows.</p>')
    d=epub(book({'c':markup},['c']))
    p=plan(d)
    assert not p['issues']
    assert speech(p)=='We saw かんじ today. Another word was とうきょう. Keep 漢字2026 and 漢字 whole. A literal 漢字 follows.'
    chosen=[(r['source'],r['selected_reading']) for row in p['decisions']
            for r in row.get('context_structure',[]) if r.get('event_id')=='CR-L014']
    assert chosen[0]==('漢(かん)字じ',{'kind':'ruby_reading','fields':{
        'base':'漢字','annotation':'かん じ','reading':'かんじ','speak_once':True}})
    assert [choice['kind'] for _,choice in chosen]==['ruby_reading','ruby_reading','base_reading','base_reading','base_reading']
    assert d.blocks[0].source=='We saw 漢(かん)字じ today.'
    assert '<rt><em>じ</em></rt>' in d.blocks[0].locator['exact']


def test_chapter_anchor_cannot_split_an_authored_ruby_group():
    markup='<p>Start. <ruby>漢<rp id="anchor">(</rp><rt>かん</rt><rp>)</rp>字<rt>じ</rt></ruby> remains whole.</p>'
    nav='<nav epub:type="toc"><ol><li><a href="c.xhtml#anchor">Reading</a></li></ol></nav>'
    d=epub(book({'c':markup},['c'],nav=nav))
    p=plan(d)
    assert len(d.blocks)==1 and '漢(かん)字じ' in d.blocks[0].source
    assert any('preserve a protected span' in warning for warning in d.warnings)
    assert not p['issues'] and speech(p)=='Start. かんじ remains whole.'


def test_table_ruby_uses_the_same_reading_as_prose_and_keeps_original_cells():
    markup=('<table><tr><th>Word</th><th>Context</th></tr>'
            '<tr><td><ruby>漢字<rt>かんじ</rt></ruby></td><td>On the board</td></tr></table>')
    d=epub(book({'c':markup},['c']))
    p=plan(d)
    assert not p['issues'] and speech(p)=='Table. Word: かんじ; Context: On the board.'
    assert d.blocks[0].locator['rows'][1]==['漢字かんじ','On the board']


def test_ruby_hides_alternate_layers_and_explicitly_hidden_text_in_speech():
    markup='<p>Read <ruby><span hidden="hidden">SECRET</span>漢字<rtc><rt>reading</rt></rtc></ruby> today.</p>'
    d=epub(book({'c':markup},['c']))
    assert 'SECRET漢字reading' in d.blocks[0].source
    assert speech(plan(d))=='Read 漢字 today.'


def test_explicit_xhtml_checkbox_states_and_mixed_state_review():
    d=epub(book({'c':'<p><input type="checkbox" checked="checked"/>Snapshot made.</p><p><span role="checkbox" aria-checked="false">Rollback tested.</span></p><p><span role="checkbox" aria-checked="mixed">Partly checked.</span></p>'},['c']))
    p=plan(d)
    assert 'Checked. Snapshot made.' in speech(p) and 'Unchecked. Rollback tested.' in speech(p)
    assert 'Partly checked.' not in speech(p) and any('mixed' in i['reason'] for i in p['issues'])


def test_math_presentation_wrapper_is_transparent_without_losing_fraction_structure():
    d=epub(book({'c':'<p>Compute <math><mstyle><mfrac><mrow><mn>2</mn><mo>+</mo><mn>3</mn></mrow><mn>4</mn></mfrac></mstyle></math>.</p>'},['c']))
    p=plan(d)
    assert not p['issues'] and speech(p)=='Compute the fraction with numerator 2 plus 3 and denominator 4.'


def test_declared_decorative_images_and_aria_hidden_stay_in_companion():
    d=epub(book({'c':'<img alt="" role="presentation"/><p>Keep <span aria-hidden="true">DECORATIVE</span> this.</p><p>Literal false null 0.</p>'},['c']))
    p=plan(d)
    assert speech(p)=='Keep this. Literal false null 0.' and not p['issues']
    assert any('DECORATIVE' in row['source'] for row in p['decisions'])
