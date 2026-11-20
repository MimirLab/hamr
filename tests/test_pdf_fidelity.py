"""Independent scientific-value controls; mocked OCR is labeled explicitly."""
import io

import pytest
from PIL import Image,ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from audiobook.importers import pdf
from audiobook.pdf_fidelity import disputes
from audiobook.planner import plan


@pytest.mark.parametrize('native,visual',[
    ('Oil 3 27','Oil 8 27'),('6.00 ± 4.39','6.00 = 4.39'),
    ('6.00 ± 4.39','6.00 + 4.39'),('Use -3 readings','Use 3 readings'),
    ('Use 0.080','Use 0.08'),('Use 10⁴','Use 104'),
    ('Transfer 5 MB/s','Transfer 5 Mb/s'),('Stop at ≥ 8','Stop at > 8'),
    ('George I and Henry II','George II and Henry I'),
])
def test_critical_disagreement_cannot_hide_behind_confidence_or_matching_digits(native,visual):
    assert disputes(native,visual)


def test_column_order_is_not_mistaken_for_a_changed_value_or_an_accuracy_pass():
    assert not disputes('A B 3 27 23 7','A 23 7 B 3 27')
    assert not disputes('Use −3 readings','Use -3 readings')
    assert not disputes('', 'Only visual text is available')
    assert disputes('3 3 27','3 27')  # Duplicate-value multiplicity matters.


@pytest.mark.parametrize('native,visual',[
    ('Value 3 27','Value 8 27'),('Value 6.00 ± 4.39','Value 6.00 = 4.39'),
])
def test_actual_pdf_route_holds_high_confidence_dispute_and_keeps_clear_page(native,visual,monkeypatch):
    # Raster+native PDF, with an explicit99-confidence OCR control. This tests
    # import/planning, not Tesseract accuracy or a real synthesis backend.
    image=Image.new('RGB',(500,90),'white');ImageDraw.Draw(image).text((12,20),native,fill='black')
    data=io.BytesIO();canvas=Canvas(data,pagesize=(600,800));canvas.drawImage(ImageReader(image),40,600,width=500,height=90)
    text=canvas.beginText(50,650);text.setTextRenderMode(3);text.textLine(native);canvas.drawText(text);canvas.showPage()
    canvas.drawString(40,700,'CLEAR_SECOND_PAGE remains narratable.');canvas.save()
    header='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    tsv=header+''.join(f'5\t1\t1\t1\t1\t{i+1}\t{40+i*90}\t200\t80\t30\t99\t{word}\n' for i,word in enumerate(visual.split()))
    monkeypatch.setattr('audiobook.pdf_ocr.configuration',lambda:None)
    monkeypatch.setattr('audiobook.pdf_ocr.observe',lambda *a:(tsv,False))
    doc=pdf(data.getvalue());p=plan(doc)
    assert doc.inventory[0]['ocr_confidence']==99
    assert doc.inventory[0]['critical_disputes']
    assert all(b.issue and 'Native and visual' in b.issue for b in doc.blocks if b.locator['page']==1)
    assert p['issues'] and all('Value' not in u['text'] for u in p['utterances'])
    assert any('CLEAR_SECOND_PAGE' in u['text'] for u in p['utterances'])


@pytest.mark.parametrize('native,visual,hold', [
    ('', 'George [ sent a letter.', True),
    ('George [ sent a letter.', 'George [ sent a letter.', True),
    ('George I sent a letter.', 'George II sent a letter.', True),
    ('', 'George I sent a letter.', False),
    ('', 'George [as quoted] sent a letter.', False),
])
def test_pdf_regnal_ocr_retains_source_and_holds_uncertain_readings(native,visual,hold,monkeypatch):
    raster=Image.new('RGB',(500,90),'white')
    ImageDraw.Draw(raster).text((12,20),'George I sent a letter.',fill='black')
    data=io.BytesIO()
    canvas=Canvas(data,pagesize=(600,800))
    canvas.drawImage(ImageReader(raster),40,600,width=500,height=90)
    if native:
        hidden=canvas.beginText(50,650)
        hidden.setTextRenderMode(3)
        hidden.textLine(native)
        canvas.drawText(hidden)
    canvas.showPage()
    canvas.drawString(40,700,'A separate page is clear.')
    canvas.save()
    header='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    tsv=header+f'5\t1\t1\t1\t1\t1\t80\t250\t600\t70\t99\t{visual}\n'
    monkeypatch.setattr('audiobook.pdf_ocr.configuration',lambda:None)
    monkeypatch.setattr('audiobook.pdf_ocr.observe',lambda *args:(tsv,False))

    doc=pdf(data.getvalue())
    result=plan(doc)
    region=next(block for block in doc.blocks if block.locator['page']==1)
    assert region.source==visual
    assert region.locator['ocr_confidence']==99
    assert bool(result['issues'])==hold
    assert any('A separate page is clear.' in u['text'] for u in result['utterances'])
    readings=[u['text'] for u in result['utterances'] if region.id in u['sources']]
    if hold:
        assert not readings
    elif visual=='George I sent a letter.':
        assert readings==['George the first sent a letter.']
    else:
        assert readings==[visual]
