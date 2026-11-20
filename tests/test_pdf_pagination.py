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
