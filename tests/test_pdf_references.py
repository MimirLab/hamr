"""Bounded reference roles must not swallow later methods or numeric prose."""
from audiobook.core import Block,Document
from audiobook.pdf_references import mark_indexes
from audiobook.planner import plan


def page(number,heading='INDEX',native_heading=None):
    entries='\n'.join(f'{name} subject, {1+i%3}' for i,name in enumerate(('Ants','Bats','Cats','Deer','Eels','Foxes','Goats','Hares','Ibex','Jays','Kites','Lions','Mice','Newts','Owls','Pigs','Quail','Rats','Seals','Toads'),1))
    native=(native_heading or heading)+'\n'+entries;visual=heading+'\n'+entries
    words=[{'rect_pixels':[x,y,60,20]} for x in (100,700) for y in range(220,1100,40)]
    inv={'page':number,'size':[600,800],'rotation':0,'route':'visual_ocr','native_observation':native,'ocr_observation':visual,'ocr_words':words}
    blocks=[Block(f'h{number}',heading,{'format':'pdf','page':number,'rect':[300,730,540,755]}),
            Block(f'b{number}',entries,{'format':'pdf','page':number,'rect':[40,60,550,690]},issue='Conflicting page references.')]
    return blocks,inv


def test_index_continuation_is_bounded_and_methods_after_references_survive():
    blocks=[];inventory=[]
    for n,h in [(1,'INDEX'),(2,'BOOK TITLE'),(3,'INDEX'),(4,'Methods')]:
        bs,i=page(n,h)
        bs[0].role='heading'  # Independently identified page/section heading, not an unfinished body sentence.
        blocks+=bs;inventory.append(i)
    mark_indexes(blocks,inventory)
    assert {b.locator['page'] for b in blocks if b.role=='reference'}=={1,2,3}
    doc=Document('Book','pdf','source',blocks,inventory=inventory)
    current=plan(doc);legacy=plan(doc,rule_revision='faithful-6');study=plan(doc,{'mode':'study'})
    assert len(current['issues'])==1 and current['issues'][0]['block']=='b4'
    assert all(d.get('source_warning') for d in current['decisions'] if d['block'] in ('b1','b2','b3'))
    assert len(legacy['issues'])==4
    assert {i['block'] for i in study['issues'] if i['reason']=='Conflicting page references.'}=={'b1','b2','b3','b4'}
    assert 'Methods' in ' '.join(u['text'] for u in current['utterances'])


def test_index_heading_alone_body_number_or_single_column_is_not_a_lookup_page():
    for alteration in ('prose','single_column','uncorroborated','rotation','body_heading'):
        blocks,i=page(1)
        if alteration=='prose':i['native_observation']=i['ocr_observation']='INDEX\nAn index measures diversity. The value is 42.'
        if alteration=='single_column':i['ocr_words']=[w for w in i['ocr_words'] if w['rect_pixels'][0]<600]
        if alteration=='uncorroborated':i['native_observation']=i['native_observation'].replace('INDEX','RESULTS')
        if alteration=='rotation':i['rotation']=90
        if alteration=='body_heading':blocks[0].locator['rect']=[50,350,150,370]
        mark_indexes(blocks,[i]*20)
        assert all(b.role=='body' for b in blocks),alteration


def test_reference_disagreement_is_retained_in_companion_but_essential_body_still_holds():
    blocks=[Block('r','Alpha, 8',{'format':'pdf','page':1},role='reference',issue='Native reads 3; OCR reads 8.'),
            Block('b','Use 0.08 mg.',{'format':'pdf','page':2},issue='Native and visual values disagree.')]
    p=plan(Document('Book','pdf','source',blocks))
    assert p['decisions'][0]['source']=='Alpha, 8' and p['decisions'][0]['source_warning']=='Native reads 3; OCR reads 8.'
    assert [i['block'] for i in p['issues']]==['b']
