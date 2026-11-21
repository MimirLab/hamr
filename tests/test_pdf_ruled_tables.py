from io import BytesIO

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from audiobook.importers import pdf
from audiobook.planner import plan


def ruled_pdf(*, declaration=True, extra_image=False):
    data = BytesIO()
    canvas = Canvas(data, pagesize=(440, 360), invariant=True)
    rule = ImageReader(Image.new('RGB', (1, 1)))
    for y in (300, 278, 172):
        canvas.drawImage(rule, 20, y, width=390, height=.48)
    for x in (20, 410):
        for bottom, height in ((172, 49), (221, 57), (278, 22)):
            canvas.drawImage(rule, x, bottom, width=.48, height=height)
    canvas.setFont('Helvetica', 10)
    if declaration:
        canvas.drawString(24, 328, 'A number in parentheses is the uncertainty in the last digits.')
    columns = (30, 150, 210, 345)
    for x, name in zip(columns, ('Quantity', 'Symbol', 'Numerical value', 'Unit')):
        canvas.drawString(x, 288, name)
    for y, name, symbol, value, unit in (
        (251, 'Sample mass', 'm', '1.234 50(07) × 10', 'kg'),
        (225, 'Measured time', 't', '1 234 (exact)', 's'),
        (199, 'Other mass', 'm', '4.51', 'kg'),
    ):
        canvas.setFont('Helvetica',10)
        for x, text in zip(columns,(name,symbol,value,unit)):
            canvas.drawString(x,y,text)
        if y == 251:
            text = canvas.beginText(columns[2]+canvas.stringWidth(value,'Helvetica',10),y)
            text.setFont('Helvetica',7)
            text.setRise(3)
            text.textOut('-5')
            canvas.drawText(text)
    canvas.drawImage(rule,150,228,width=3,height=.45)
    if extra_image:
        canvas.drawImage(rule,32,70,width=80,height=40)
    canvas.save()
    return data.getvalue()


def test_single_pixel_rules_support_native_cells_without_hiding_overpaint(monkeypatch):
    def no_ocr(*_args, **_kwargs):
        raise AssertionError('The verified frame does not require whole-page OCR')
    monkeypatch.setattr('audiobook.pdf_ocr.configuration', no_ocr)
    d = pdf(ruled_pdf())
    assert d.inventory[0]['route']=='native_ruled_panels'
    assert len(d.inventory[0]['raster_table_rules'])==9
    assert len(d.inventory[0]['raster_text_marks'])==1
    table = [b for b in d.blocks if b.role=='table']
    assert len(table)==3
    assert [b.locator['panel_row'] for b in table]==[1,2,3]
    assert table[0].locator['rows'][1]==['Sample mass','m','1.234 50(07) × 10 -5','kg']
    p = plan(d)
    assert [issue['block'] for issue in p['issues']]==[table[1].id]
    assert 'raster mark' in p['issues'][0]['reason']
    annotated = p['decisions'][1]['context_structure']
    uncertainty = next(event for event in annotated if event.get('event_id')=='CR-N034')
    assert uncertainty['related_event_ids']==['CR-N051']
    assert uncertainty['fields']['coefficient']=='1.23450'
    assert uncertainty['fields']['uncertainty_last_digits']=='07' and uncertainty['fields']['exponent']=='-5'
    spoken = ' '.join(take['text'] for take in p['utterances'])
    assert 'one point two three four five zero times ten to the power of minus five' in spoken
    assert 'uncertainty of zero seven in the final digits' in spoken


def test_uncertainty_needs_its_source_convention_and_images_keep_ocr(monkeypatch):
    d = pdf(ruled_pdf(declaration=False))
    assert not next(b for b in d.blocks if b.source.startswith('Sample mass')).locator.get('pdf_cell_readings')
    assert sum(b.issue is not None for b in d.blocks)==2

    called = []
    def observe(*args):
        called.append(args)
        return 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n',False
    monkeypatch.setattr('audiobook.pdf_ocr.configuration', lambda: ())
    monkeypatch.setattr('audiobook.pdf_ocr.observe', observe)
    d = pdf(ruled_pdf(extra_image=True))
    assert len(called)==1 and d.inventory[0]['route']=='visual_ocr'
    assert len(d.inventory[0]['raster_content_regions'])==1
