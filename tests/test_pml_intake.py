import json
from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import extract_location, inspect_source
from audiobook.planner import plan


PROSE = r'''\vTITLE="Harbor watch"\v
\C0="Pier"
\xThe evening watch\x
\a147Wait here,\a148 Mira whispered.\Fn="wind"1\Fn
When ready, read \Sd="shore"the shore report\Sd.
She checked the \iaft\i rail. The caf\U00E9 is open. A literal slash is \\.
\X1Morning\X1
They returned at dawn.
<footnote id="wind">
The wind had stopped.
</footnote>
<sidebar id="shore">
The lantern stood near the railway.
</sidebar>
'''


def test_loose_pml_uses_shared_chapters_notes_and_narration(tmp_path):
    source = tmp_path/'watch.pml'
    source.write_text(PROSE,encoding='ascii')
    document = inspect_source(source)
    assert document.title=='Harbor watch'
    assert [block.source for block in document.blocks if block.role=='heading']==['The evening watch','Morning']
    marker = next(block for block in document.blocks if block.role=='navigation')
    assert marker.source=='Pier' and marker.locator['chapter_marker']
    body = next(block for block in document.blocks if 'Mira whispered' in block.source)
    assert body.locator['lines']==[4,4]
    assert body.locator['exact'].endswith(r'\Fn="wind"1\Fn')
    result = plan(document)
    joined = ' '.join(u['text'] for u in result['utterances'])
    assert 'The wind had stopped.' in joined
    assert 'the shore report' in joined
    assert 'The lantern stood near the railway.' in joined
    assert 'café' in joined
    assert 'TITLE=' not in joined
    assert [u['text'] for u in result['utterances']].count('The wind had stopped.')<=1


def test_asset_capture_folder_archive_and_exact_extraction(tmp_path):
    root = tmp_path/'originals'
    root.mkdir()
    (root/'watch_img').mkdir()
    main = root/'watch.pml'
    raw = b'\\xMorning\\x\n\\m="cover.png"\nThe lamp was lit.\n'
    main.write_bytes(raw)
    png = b'\x89PNG\r\n\x1a\nportable cover bytes'
    (root/'watch_img/cover.png').write_bytes(png)
    loose = inspect_source(main)
    folder = inspect_source(root)
    assert [b.source for b in loose.blocks]==[b.source for b in folder.blocks]
    book = tmp_path/'book'
    book.mkdir()
    archive = book/'original.zip'
    with ZipFile(archive,'w') as packed:
        packed.writestr('edition/watch.pml',raw)
        packed.writestr('edition/images/cover.png',png)
    wrapped = inspect_source(archive)
    assert [b.source for b in loose.blocks]==[b.source for b in wrapped.blocks]
    image, = wrapped.metadata['source_resources']
    index = image['location_id']
    companion = {'source_locations':{index:image},'original_source':'original.zip',
                 'source_kind':'zip','source_sha256':wrapped.source_sha256}
    (book/'companion.json').write_text(json.dumps(companion))
    recovered = tmp_path/'cover.png'
    extract_location(book,index,recovered)
    assert recovered.read_bytes()==png


def test_explicit_pmlz_reads_parts_in_order_and_raw_zip_offers_distinct_books(tmp_path):
    source = tmp_path/'edition.pmlz'
    with ZipFile(source,'w') as packed:
        packed.writestr('02.pml','Second part.')
        packed.writestr('01.pml','First part.')
    assert [b.source for b in inspect_source(source).blocks]==['First part.','Second part.']
    renamed = source.rename(tmp_path/'archive.zip')
    with pytest.raises(Error,match='Choose the publication') as error:
        inspect_source(renamed)
    assert {item['name'] for item in error.value.candidates}=={'01.pml','02.pml'}


def test_invalid_command_holds_only_its_original_paragraph(tmp_path):
    source = tmp_path/'damaged.pml'
    source.write_bytes(b'Clear first.\nA \\Zbad instruction.\nClear last.\n')
    document = inspect_source(source)
    assert [b.issue is not None for b in document.blocks]==[False,True,False]
    assert document.blocks[1].locator['exact']==r'A \Zbad instruction.'
    assert document.blocks[1].locator['lines']==[2,2]
