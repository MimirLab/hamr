import io
from PIL import Image,ImageDraw,ImageFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from audiobook.importers import pdf
from audiobook.planner import plan


def mixed_pdf():
    # Independently authored visible and deceptive hidden observations.
    im=Image.new('RGB',(1200,400),'white'); draw=ImageDraw.Draw(im); font=ImageFont.load_default(size=34)
    draw.text((30,40),'Stop at 0.08 mg. Do not restart.',font=font,fill='black')
    draw.text((30,110),'Only continue after cooling below 8 C.',font=font,fill='black')
    b=io.BytesIO(); c=Canvas(b,pagesize=(600,800)); c.drawString(30,750,'Digital text beside a scanned region.')
    c.drawImage(ImageReader(im),20,420,width=560,height=187)
    t=c.beginText(30,550); t.setTextRenderMode(3); t.textLine('Stop at 0.80 mg.'); c.drawText(t); c.showPage()
    c.drawString(30,700,'A separate clear page can continue.'); c.showPage(); c.save(); return b.getvalue()


def test_hidden_numeric_text_challenged():
    data=mixed_pdf(); d=pdf(data); p=plan(d)
    assert d.inventory[0]['route']=='visual_ocr'
    assert '0.08' in d.inventory[0]['ocr_observation'] and '0.80' in d.inventory[0]['native_observation']
    assert any('Only continue after cooling below 8 C.' in b.source for b in d.blocks if b.locator['page']==1)
    assert p['issues'] and p['issues'][0]['location']['page']==1
    assert any('separate clear page' in u['text'] for u in p['utterances'])
