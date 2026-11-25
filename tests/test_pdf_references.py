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


def test_bibliography_preserves_readable_notes_and_stops_before_a_new_section():
    from audiobook.pdf_references import mark_bibliographies

    citations='Smith, A. 1976. One important survey.\nContinued publication details.\nClark, T. 1978b. Another study.\nParker, C. 1982. A final source.'
    text=['Notes\nA narrative note describes the first experiment.',
          'Bibliography\n'+citations,citations,'Methods\n'+citations,citations]
    blocks = [Block(f'p{n}-{i}',line,{'format':'pdf','page':n},issue='Source uncertainty.' if i else None)
              for n,page in enumerate(text,1) for i,line in enumerate(page.splitlines())]
    inventory = [{'page':n,'route':'visual_ocr','rotation':0,'native_geometry_qualified':True,
                  'native_observation':page,'ocr_observation':page} for n,page in enumerate(text,1)]
    mark_bibliographies(blocks,inventory)
    assert {b.locator['page'] for b in blocks if b.role=='reference'}=={2,3}
    doc=Document('A source','pdf','source',blocks)
    default,read_refs=plan(doc),plan(doc,{'references':'speak'})
    assert any(d.get('source_warning') for d in default['decisions'] if d['block']=='p2-1')
    assert 'p2-1' not in {i['block'] for i in default['issues']}
    assert 'p2-1' in {i['block'] for i in read_refs['issues']}
    assert 'p4-1' in {i['block'] for i in default['issues']}


def test_bibliography_author_repeat_at_page_start_can_be_a_weak_dash_observation():
    from audiobook.pdf_references import citation_page

    subject = '1976. The first long study.\nPublisher and journal information.\nRice, K. 1978. A second study.\nLee, B. 1982. Another source.'
    assert citation_page('-. '+subject)==citation_page('. '+subject)==(True,False)
    assert citation_page('Analysis begins here.\nA separate section reports changes.\n'+subject)==(False,False)


def test_mixed_bibliography_and_following_prose_is_not_excluded():
    from audiobook.pdf_references import citation_page

    mixed = ('Bibliography\nSmith, A. 1976. One study.\nClark, T. 1978. Another study.\n'
             'Methods\nThe next experiment tested a different hypothesis.')
    assert citation_page(mixed)==(False,False)
