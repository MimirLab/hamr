from audiobook.core import Block
from audiobook.pdf_pagination import gaps


def observed(values):
    blocks=[];inventory=[]
    for page,value in enumerate(values,1):
        blocks.append(Block(str(page),str(value),{'format':'pdf','page':page,'rect':[30,20,60,30]}))
        inventory.append({'page':page,'size':[600,800],'route':'visual_ocr','native_observation':f'Authored prose.\n{value}'})
    return blocks,inventory


def test_persistent_corroborated_page_gap_is_a_source_review_not_invented_content():
    blocks,inventory=observed([129,130,131,134,135,136]);found=gaps(blocks,inventory)
    assert len(found)==1 and found[0]['unavailable_printed_pages']==[132,133]
    assert found[0]['prior_PDF_page']==3 and found[0]['page']==4


def test_isolated_ocr_misread_body_numbers_or_numbering_reset_do_not_assert_missing_pages():
    for values in [[20,21,2,23,24,25],[57,58,58,60,61,62],[129,130,131,1,2,3]]:
        assert not gaps(*observed(values))
    blocks,inventory=observed([129,130,131,134,135,136]);blocks[3].locator['rect']=[30,400,60,413]
    assert not gaps(blocks,inventory)


def test_competing_folio_observation_or_insufficient_neighbours_abstains():
    blocks,inventory=observed([129,130,131,134,135,136]);inventory[3]['native_observation']='Authored prose.\n132'
    assert not gaps(blocks,inventory)
    assert not gaps(*observed([131,134]))


def test_missing_one_recognized_folio_and_native_missing_line_break_keep_supported_gap():
    blocks,inventory=observed([129,130,131,134,135,136,137])
    inventory[2]['native_observation']='unfinished word\ufffe131'
    blocks[4].source='Unrecognized footer.'
    found=gaps(blocks,inventory)
    assert len(found)==1 and found[0]['unavailable_printed_pages']==[132,133]


def test_one_native_observation_is_not_misrepresented_as_independent_ocr_corroboration():
    blocks,inventory=observed([129,130,131,134,135,136])
    for i in inventory:i['route']='native'
    assert not gaps(blocks,inventory)


def test_two_scans_of_the_same_folios_are_reported_without_dropping_content():
    from audiobook.pdf_pagination import repeats

    values=[135,136,137,136,137,138]
    blocks,inventory=observed(values)
    first='The migration starts when cool weather reaches the highlands and the mountain plants go dormant.'
    second='Nine independent groups were followed across the winter before the researchers repeated their measurements.'
    texts=['before '*60,first*6,second*6,first*6,second*6,'after '*60]
    for number,entry in enumerate(inventory):
        entry['native_observation']=entry['ocr_observation']=texts[number]+'\n'+str(values[number])
    found=repeats(blocks,inventory)
    assert len(found)==1 and found[0]['first_PDF_pages']==[2,3]
    assert found[0]['repeated_PDF_pages']==[4,5]
    assert [block.source for block in blocks]==list(map(str,values))
    inventory[4]['ocr_observation']='Different body text. '*30
    assert not repeats(blocks,inventory)
