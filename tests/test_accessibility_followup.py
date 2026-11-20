from test_upstream_epub import book,speech
from audiobook.importers import epub
from audiobook.planner import plan


def test_ruby_is_one_representation_and_explicit_gloss_stays_distinct():
    d=epub(book({'c':'<p>The <ruby>base<rp>(</rp><rt>reading</rt><rp>)</rp></ruby> remains.</p><p>A <ruby>term<rt role="definition">meaning</rt></ruby> follows.</p>'},['c']))
    p=plan(d)
    assert speech(p)=='The base remains. A term, meaning meaning follows.'
    assert 'reading' in d.blocks[0].source
    assert any(s['kind']=='ruby' and s['annotation']=='reading' for s in d.blocks[0].locator['inline_semantics'])


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
