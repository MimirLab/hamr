import io
from PIL import Image,ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from audiobook.importers import pdf
from audiobook.planner import plan
from audiobook.pdf_regions import localize,glyph_reader


def parts(first='Bad value 8.',second='Clear value 12.'):
    return [{'text':first,'locator':{'paragraph':0,'rect':[0,0,1,1]},'issue':None},
            {'text':second,'locator':{'paragraph':1,'rect':[0,2,1,3]},'issue':None}]


def test_precise_native_visual_coverage_holds_only_the_disputed_region():
    p=parts();r=localize(p,'Bad value 3. Clear value 12.','Bad value 8. Clear value 12.',lambda rect:'Bad value 3.' if rect[1]==0 else 'Clear value 12.')
    assert r['scope']=='paragraph' and p[0]['issue'] and not p[1]['issue']


def test_equal_whole_page_values_do_not_hide_swapped_regions():
    p=parts('First 8.','Second 3.');r=localize(p,'First 3. Second 8.','First 8. Second 3.',lambda rect:'First 3.' if rect[1]==0 else 'Second 8.')
    assert r['scope']=='paragraph' and all(part['issue'] for part in p)


def test_missing_duplicate_or_unqualified_regions_cannot_clear_page_dispute():
    for qualified,reader in [(False,lambda _:''),(True,lambda _:'Bad value 3. Clear value 12.')]:
        p=parts();r=localize(p,'Bad value 3. Clear value 12.','Bad value 8. Clear value 12.',reader,qualified)
        assert r['scope']=='page' and all(part['issue'] for part in p)


def test_actual_pdf_geometry_preserves_clear_paragraph_on_a_disputed_scan(monkeypatch):
    image=Image.new('RGB',(600,800),'white');draw=ImageDraw.Draw(image);draw.text((50,90),'Bad value 3.',fill='black');draw.text((50,240),'CLEAR region remains narratable.',fill='black')
    data=io.BytesIO();canvas=Canvas(data,pagesize=(600,800));canvas.drawImage(ImageReader(image),0,0,width=600,height=800)
    for y,text in [(700,'Bad value 3.'),(550,'CLEAR region remains narratable.')]:
        hidden=canvas.beginText(50,y);hidden.setTextRenderMode(3);hidden.textLine(text);canvas.drawText(hidden)
    canvas.save()
    header='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    tsv=header+'5\t1\t1\t1\t1\t1\t100\t180\t500\t24\t99\tBad value 8.\n'+'5\t1\t2\t1\t1\t1\t100\t480\t500\t24\t99\tCLEAR region remains narratable.\n'
    monkeypatch.setattr('audiobook.pdf_ocr.configuration',lambda:None);monkeypatch.setattr('audiobook.pdf_ocr.observe',lambda *a:(tsv,False))
    doc=pdf(data.getvalue());p=plan(doc)
    assert doc.inventory[0]['critical_localization']['scope']=='paragraph'
    assert len(p['issues'])==1 and 'Bad value' not in ' '.join(u['text'] for u in p['utterances'])
    assert 'CLEAR region remains narratable.' in ' '.join(u['text'] for u in p['utterances'])


def test_adjacent_hidden_glyph_boxes_do_not_duplicate_a_number_into_clear_paragraph():
    import pypdfium2 as pdfium
    data=io.BytesIO();canvas=Canvas(data,pagesize=(200,300));canvas.setFont('Helvetica',12)
    canvas.drawString(20,150,'Clear region.');canvas.drawString(20,138,'Value 1981.');canvas.save()
    doc=pdfium.PdfDocument(data.getvalue());page=doc[0];tp=page.get_textpage()
    try:
        p=[{'text':'Clear region.','locator':{'paragraph':0,'rect':[20,148,180,161]},'issue':None},
           {'text':'Value 1891.','locator':{'paragraph':1,'rect':[20,135,180,147]},'issue':None}]
        reader=glyph_reader(tp)
        assert '1981' not in reader(p[0]['locator']['rect'])
        assert '1981' in reader(p[1]['locator']['rect'])
        result=localize(p,tp.get_text_range(),'Clear region. Value 1891.',reader)
        assert result['scope']=='paragraph' and not p[0]['issue'] and p[1]['issue']
    finally:tp.close();page.close();doc.close()
