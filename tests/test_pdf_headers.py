from audiobook.core import Block,Document
from audiobook.pdf_headers import mark_headers
from audiobook.planner import plan


def test_repeated_folio_header_keeps_uncertainty_in_companion_but_not_spoken_body():
    inventory=[{'page':n,'size':[600,800]} for n in range(1,7)]
    blocks=[Block(str(n),f'{n} NATURAL HISTORY',{'page':n,'rect':[30,760,300,773]},issue='OCR folio disagreement.') for n in range(1,7)]
    legitimate=Block('body','42 NATURAL HISTORY',{'page':2,'rect':[30,400,300,413]});blocks.append(legitimate)
    mark_headers(blocks,inventory);p=plan(Document('Book','pdf','source',blocks,inventory=inventory))
    assert all(b.role=='furniture' and b.issue for b in blocks[:-1]) and legitimate.role=='body'
    assert not p['issues'] and len(p['utterances'])==1 and '42' in p['utterances'][0]['text']
    assert all(d['source'] for d in p['decisions'])


def test_recurring_uppercase_body_without_folio_or_short_occurrence_does_not_disappear():
    blocks=[Block(str(n),'NATURAL HISTORY',{'page':n,'rect':[30,760,300,773]}) for n in range(1,7)]
    blocks+=[Block('rare'+str(n),f'{n} SHORT SECTION',{'page':n,'rect':[30,760,300,773]}) for n in range(1,4)]
    mark_headers(blocks,[{'page':n,'size':[600,800]} for n in range(1,7)])
    assert all(b.role=='body' for b in blocks)


def corroborated_header(text='NATURAL HISTORY n',native='NATURAL HISTORY 12'):
    blocks=[Block(str(n),f'{n} NATURAL HISTORY',{'page':n,'rect':[30,760,300,773]}) for n in range(1,6)]
    b=Block('corrupt',text,{'page':6,'rect':[30,760,300,773],'paragraph':0},issue='Disputed printed folio.')
    blocks.append(b)
    inventory=[{'page':n,'size':[600,800]} for n in range(1,6)]+[{'page':6,'size':[600,800],'rotation':0,'route':'visual_ocr','critical_localization':{'scope':'paragraph','qualified_geometry':True,'native_critical_coverage':True,'visual_critical_coverage':True,'regions':[{'paragraph':0,'rect':[30,760,300,773],'native_observation':native}]}}]
    return blocks,inventory,b


def test_corrupt_folio_header_needs_same_localized_native_label_and_five_distinct_support_pages():
    for text in ('NATURAL HISTORY n','3n NATURAL HISTORY'):
        blocks,inventory,b=corroborated_header(text);source=b.source;identity=b.id
        mark_headers(blocks,inventory)
        assert b.role=='furniture' and b.issue and b.source==source and b.id==identity
        assert b.locator['running_header']['native_folio_corroborated'] is True
        assert b.locator['running_header']['occurrences']==5
        p=plan(Document('Book','pdf','source',blocks,inventory=inventory))
        assert not p['issues'] and next(d for d in p['decisions'] if d['block']==identity)['source_warning']==b.issue


def test_corrupt_header_abstains_on_missing_or_conflicting_affiliation_evidence():
    for fault in ('native_label','native_folio','coverage','geometry','rect','rotation','body_position','duplicate_support_page','unknown_visual_suffix'):
        blocks,inventory,b=corroborated_header();local=inventory[-1]['critical_localization']
        if fault=='native_label':local['regions'][0]['native_observation']='OTHER CHAPTER 12'
        if fault=='native_folio':local['regions'][0]['native_observation']='NATURAL HISTORY'
        if fault=='coverage':local['native_critical_coverage']=False
        if fault=='geometry':local['qualified_geometry']=False
        if fault=='rect':local['regions'][0]['rect']=[30,750,300,773]
        if fault=='rotation':inventory[-1]['rotation']=90
        if fault=='body_position':b.locator['rect']=[30,400,300,413]
        if fault=='duplicate_support_page':blocks[0].locator['page']=2
        if fault=='unknown_visual_suffix':b.source='NATURAL HISTORY Methods'
        mark_headers(blocks,inventory)
        assert b.role=='body' and b.issue,fault


def test_split_folio_and_short_running_label_follow_a_document_sequence():
    inventory = [{'page': page, 'size': [600, 800]} for page in range(40, 49)]
    blocks = []
    for page in range(40, 45):
        blocks.append(Block(f'anchor-{page}', f'THE LONG TITLE {page - 30}',
                            {'page': page, 'rect': [90, 755, 450, 765]}))
    for page in (45, 47):
        blocks.extend([Block(f'number-{page}', str(page - 30), {'page': page, 'rect': [50, 756, 70, 764]}),
                       Block(f'label-{page}', 'NATURAL HISTORY', {'page': page, 'rect': [160, 755, 361, 765]})])
    blocks.extend([Block('isolated', '18', {'page': 48, 'rect': [50, 765, 70, 775]}),
                   Block('chapter', 'NATURAL HISTORY', {'page': 48, 'rect': [160, 741, 361, 751]}),
                   Block('body', 'A separate region carries prose.', {'page': 48, 'rect': [50, 685, 450, 715]})])

    mark_headers(blocks, inventory)

    assert all(b.role == 'furniture' and b.locator['running_header']['sequence_folio'] for b in blocks[5:9])
    assert all(b.role == 'body' for b in blocks[9:])


def test_two_split_labels_without_a_document_folio_sequence_stay_in_the_body():
    inventory = [{'page': n, 'size': [600, 800]} for n in (12, 13)]
    blocks = []
    for n in (12, 13):
        blocks.extend([Block(f'n{n}', '25', {'page': n, 'rect': [50, 755, 75, 765]}),
                       Block(f'h{n}', 'NATURAL HISTORY', {'page': n, 'rect': [160, 755, 350, 765]})])
    mark_headers(blocks, inventory)
    assert all(b.role == 'body' for b in blocks)
