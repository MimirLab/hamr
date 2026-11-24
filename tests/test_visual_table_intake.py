from io import BytesIO
import pytest

from PIL import Image, ImageDraw
from reportlab.pdfgen.canvas import Canvas

from audiobook.importers import pdf
from audiobook.planner import plan
from audiobook.visual_table_intake import visual_ocr_parts


def test_native_vector_rules_preserve_merged_cells_and_surrounding_prose():
    data = BytesIO()
    canvas = Canvas(data,pagesize=(360,240),invariant=True)
    canvas.setLineWidth(1)
    canvas.rect(24,48,324,132)
    canvas.line(24,92,348,92)
    canvas.line(124,136,348,136)
    canvas.line(124,48,124,180)
    canvas.setStrokeGray(.6)
    canvas.line(224,136,224,180)
    canvas.line(224,48,224,92)
    for x,y,text in [(24,210,'Before the table.'),(35,128,'North harbor'),
                     (135,154,'Still water'),(235,154,'Light wind'),
                     (135,110,'The gate was shut.'),(35,66,'South harbor'),
                     (135,66,'A lamp was lit.'),(235,66,'The watch ended.'),
                     (24,24,'After the table.')]:
        canvas.drawString(x,y,text)
    canvas.save()

    document = pdf(data.getvalue())
    before, table, after = document.blocks
    assert (before.source,table.role,after.source)==('Before the table.','table','After the table.')
    assert table.issue is None
    cells = table.locator['table_grid']['cells']
    assert [(c['row'],c['column'],c['rows'],c['columns'],c['parts'][0]['source']) for c in cells]==[
        (0,0,2,1,'North harbor'), (0,1,1,1,'Still water'), (0,2,1,1,'Light wind'),
        (1,1,1,2,'The gate was shut.'), (2,0,1,1,'South harbor'),
        (2,1,1,1,'A lamp was lit.'), (2,2,1,1,'The watch ended.')]
    assert all(c['location']['native_ranges'] for c in cells)


def test_scan_rule_glyph_requires_a_separate_matching_cell_observation():
    def word(text,x,y,w,group):
        return ['5','1',str(group),'1','1','1',str(x),str(y),str(w),'16','92',text]

    words = [word('Before',32,15,70,1),word('Water',32,90,48,2),
             word('|Wind',161,90,60,2),word('Quiet',32,150,53,3),
             word('Light',170,150,67,3),word('After',32,230,77,4)]
    with Image.new('L',(320,270),255) as image:
        rules = ImageDraw.Draw(image)
        rules.rectangle((24,64,280,192),outline=0,width=2)
        rules.line((160,64,160,192),fill=0,width=2)
        rules.line((24,128,280,128),fill=0,width=2)

        result = visual_ocr_parts(image,words,1,320,270,format='scan',scale=1,region=None,
                                  read_crop=lambda image: [word('Wind',2,16,48,1)])
        assert [p['text'] for p in result]==['Before','Water | Wind\nQuiet | Light','After']
        assert result[1]['issue'] is None
        cell = result[1]['locator']['table_grid']['cells'][1]
        assert cell['location']['ocr_word_indices']==[2]
        assert cell['location']['candidate_ocr']=='|Wind'
        assert cell['location']['verified_inner_crop']

        result = visual_ocr_parts(image,words,1,320,270,format='scan',scale=1,region=None,
                                  read_crop=lambda image: [word('Water',2,16,48,1)])
        assert 'vertical table border' in result[1]['issue']
        assert '||Wind' in result[1]['text'].replace(' ','')
        assert not result[0]['issue'] and not result[2]['issue']


def borderless_pdf(draws):
    data = BytesIO()
    canvas = Canvas(data,pagesize=(430,320),invariant=True)
    for x,y,text in draws:
        canvas.drawString(x,y,text)
    canvas.save()
    return pdf(data.getvalue())


def test_repeated_typed_columns_use_the_same_narration_as_authored_cells():
    rows = [
        ('Landing','Arrivals','Departures'),
        ('River pier','9','12'),
        ('West platform','14','8'),
        ('Island dock','19','10'),
    ]
    draws = [(x,y,text) for y,row in zip((220,193,166,139),rows)
             for x,text in zip((55,174,290),row)]
    document = borderless_pdf([(32,274,'The ferry log follows.'),*draws,(32,95,'The log is closed.')])
    before,table,after = document.blocks
    assert (before.source,after.source)==('The ferry log follows.','The log is closed.')
    assert table.issue is None
    assert table.locator['rows']==[list(row) for row in rows]
    assert all(cell['location']['native_ranges'] for cell in table.locator['table_grid']['cells'])
    speech = ' '.join(u['text'] for u in plan(document)['utterances'])
    assert 'Landing: West platform. Arrivals: 14. Departures: 8.' in speech


def test_aligned_prose_columns_do_not_infer_table_labels_or_pause_the_book():
    draws = [(x,y,text) for y,pair in zip((255,236,217,198,179),[
        ('Winter roads','At the station'),
        ('The first plow arrived.', 'Trains left on time.'),
        ('A second one followed.', 'The coffee was good.'),
        ('Salt was low.', 'A child waved goodbye.'),
        ('A storm was brewing.', 'Snow fell before dawn.'),
    ]) for x,text in zip((35,245),pair)]
    document = borderless_pdf(draws)
    assert len(document.blocks)==10
    assert all(b.role=='body' and not b.issue for b in document.blocks)
    assert all(word in ' '.join(b.source for b in document.blocks)
               for word in ('Salt was low.', 'A child waved goodbye.'))


def test_missing_gaps_do_not_turn_adjacent_values_into_one_numeric_cell():
    rows = [
        ('Landing','Arrivals','Departures'),
        ('River pier','6 12','3 9'),
        ('West platform','9 27','4 8'),
        ('Island dock','4 23','6 8'),
    ]
    document = borderless_pdf([(x,y,text) for y,row in zip((220,193,166,139),rows)
                               for x,text in zip((55,174,290),row)])
    assert all(not b.locator.get('table_grid') for b in document.blocks)


@pytest.mark.parametrize('suffix',['',' pier'])
def test_native_word_gaps_keep_labels_inside_their_repeated_table_columns(suffix):
    before = 'Harbor observations follow.'
    after = 'These measurements were recorded before dawn.'
    rows = [('Station','Water','Wind'),
            *zip([name+suffix for name in ('North','East','South','West')],
                 ['3.5','4.2','3.8','4.1'],['12','9','14','10'])]
    inputs = [(x,y,text) for y,row in zip((250,221,192,163,134),rows)
              for x,text in zip((40,210,335),row)]
    doc = borderless_pdf([(35,300,before),*inputs,(35,65,after)])
    start,table,end = doc.blocks
    assert (start.source,end.source)==(before,after)
    assert doc.inventory[0]['glyph_character_coverage']=='exact'
    assert table.locator['rows']==[list(row) for row in rows]
    result = plan(doc)
    assert not result['issues']
    spoken = ' '.join(unit['text'] for unit in result['utterances'])
    assert spoken.count(before)==spoken.count(after)==1
    assert all(f'Station: {label}. Water: {water}. Wind: {wind}.' in spoken for label,water,wind in rows[1:])


def test_geometric_fallback_retains_every_fragment_at_a_shared_baseline(monkeypatch):
    import audiobook.visual_table_intake as visual

    monkeypatch.setattr(visual,'native_parts',lambda *args:[])
    header = [(35,288,'Station'),(200,288,'Water'),(291,288,'Wind')]
    columns = [(x,y,text) for y,pair in zip((253,222,191),[
        ('The left side began.','The right side began.'),
        ('The left side continued.','The right side continued.'),
        ('The left side finished.','The right side finished.')
    ]) for x,text in zip((35,291),pair)]
    doc = borderless_pdf(header+columns)
    assert doc.inventory[0]['route']=='geometric_two_columns'
    assert doc.inventory[0]['glyph_character_coverage']=='exact'
    assert [block.source for block in doc.blocks[:3]]==['Station','Water','Wind']
    assert all(block.issue for block in doc.blocks[:3])
    assert all(not block.issue for block in doc.blocks[3:])
