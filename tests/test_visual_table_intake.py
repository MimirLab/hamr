from io import BytesIO

from PIL import Image, ImageDraw
from reportlab.pdfgen.canvas import Canvas

from audiobook.importers import pdf
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
