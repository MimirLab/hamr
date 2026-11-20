from audiobook.core import Block,Document,digest
from audiobook.pdf_flow import overlaps
from audiobook.planner import plan


def test_nested_ocr_word_region_is_reviewed_without_erasing_either_observation():
    a=Block('paragraph','The source paragraph has several complete lines of ordinary prose which end with an unfinished word and then continue into the next line.',{'format':'pdf','page':1,'rect':[20,50,340,105]})
    b=Block('fragment','fragment as',{'format':'pdf','page':1,'rect':[300,97,340,105]})
    inventory=[{'page':1,'route':'visual_ocr','critical_localization':{'qualified_geometry':True}}]
    findings=overlaps([a,b],inventory)
    assert len(findings)==1 and findings[0]['source_blocks']==['paragraph','fragment']
    assert a.source.endswith('next line.') and b.source=='fragment as' and not a.issue
    b.locator['rect']=[350,97,390,105]
    assert not overlaps([a,b],inventory)
    b.locator['rect']=[300,97,340,105];inventory[0]['critical_localization']['qualified_geometry']=False
    assert not overlaps([a,b],inventory)


def test_known_source_hole_holds_lowercase_continuation_across_furniture_but_keeps_next_complete_paragraph():
    blocks=[Block('hole','Unknown important source words.',{},issue='Resolve the preceding body region.'),Block('header','12 BOOK TITLE',{},role='furniture'),Block('tail','adapted to cold. The rest of this paragraph follows.',{}),Block('clear','The next paragraph is independently complete.',{})]
    doc=Document('Book','pdf','source',blocks)
    current=plan(doc);legacy=plan(doc,rule_revision='faithful-9')
    assert [u['text'] for u in current['utterances']]==[blocks[-1].source]
    assert {i['block'] for i in current['issues']}=={'hole','tail'}
    assert any('adapted to cold.' in u['text'] for u in legacy['utterances'])


def test_verified_companion_header_preserves_the_actual_cross_page_sentence():
    blocks=[Block('a','Information passes between generations',{'format':'pdf','page':1},chapter='One'),Block('h','INTRODUCTION n',{'format':'pdf','page':2},chapter='One',issue='Printed folio differs.'),Block('b','by means of inherited traits.',{'format':'pdf','page':2},chapter='One')]
    policy={'source_sha256':'source','blocks':{'h':{'action':'companion','verified':True,'reason':'Source-verified repeated margin header.'}}}
    p=plan(Document('Book','pdf','source',blocks),{'document':policy})
    assert not p['issues'] and p['utterances'][0]['text']=='Information passes between generations by means of inherited traits.'


def test_explicit_heading_and_standalone_reading_can_start_lowercase_after_a_source_hold():
    for kind in ('heading','standalone'):
        blocks=[Block('hole','Unknown source.',{},issue='Inspect source.'),Block('new','evolution: a new section',{},role='heading' if kind=='heading' else 'body')]
        policy={'source_sha256':'source','blocks':{}}
        if kind=='standalone':policy['blocks']['new']={'action':'speak','verified':True,'expected_source_sha256':digest(blocks[1].source.encode()),'placement':'standalone','reason':'Verified standalone title.'}
        p=plan(Document('Book','pdf','source',blocks),{'document':policy})
        assert any(u['text']==blocks[1].source for u in p['utterances']) and [i['block'] for i in p['issues']]==['hole']


def test_independent_caption_and_pause_do_not_supply_a_missing_body_prefix():
    blocks=[Block('hole','Unknown source.',{},issue='Inspect source.'),Block('caption','a fox by its den.',{}),Block('pause','***',{}),Block('tail','and the continued sentence ends here.',{})]
    policy={'source_sha256':'source','blocks':{'caption':{'action':'speak','verified':True,'expected_source_sha256':digest(blocks[1].source.encode()),'placement':'caption_after_paragraph','reason':'Verified separate photograph caption.'}}}
    p=plan(Document('Book','pdf','source',blocks),{'document':policy})
    assert {i['block'] for i in p['issues']}=={'hole','tail'}
    assert [u['text'] for u in p['utterances']]==['Illustration caption. a fox by its den.']
