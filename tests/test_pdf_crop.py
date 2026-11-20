import io
import pypdfium2 as pdfium
from reportlab.pdfgen.canvas import Canvas
from audiobook.importers import pdf
from audiobook.planner import plan


def source(rotation=0,clip=False):
    raw=io.BytesIO();c=Canvas(raw,pagesize=(600,800));c.drawString(20,750,'OUTSIDE_CANARY.');c.drawString(140 if not clip else 95,600,'VISIBLE_CANARY.')
    c.bookmarkPage('heading',fit='XYZ',left=140,top=500,zoom=0);c.addOutlineEntry('Crop chapter','heading',level=0);c.drawString(140,500,'Crop chapter');c.drawString(140,470,'BODY_CANARY.');c.save()
    doc=pdfium.PdfDocument(raw.getvalue());page=doc[0];page.set_cropbox(100,100,500,700);page.set_rotation(rotation)
    out=io.BytesIO();doc.save(out);page.close();doc.close();return out.getvalue()


def test_visible_crop_excludes_offpage_glyphs_and_normalizes_outline_coordinates():
    d=pdf(source());p=plan(d);speech=' '.join(u['text'] for u in p['utterances'])
    assert not p['issues'] and 'VISIBLE_CANARY.' in speech and 'BODY_CANARY.' in speech and 'OUTSIDE_CANARY' not in speech
    assert p['chapters']==['Opening','Crop chapter'] and d.inventory[0]['excluded_crop_glyphs']
    assert 'OUTSIDE_CANARY' in d.inventory[0]['native_observation']
    assert d.inventory[0]['source_outlines'][0]['status']=='source_matched'


def test_partial_glyph_clipping_is_a_source_review_not_an_invented_word():
    p=plan(pdf(source(clip=True)))
    assert p['issues'] and any('CropBox' in i['reason'] for i in p['issues']) and not p['utterances']


def test_rotated_native_text_does_not_pass_an_unqualified_order_guess():
    for rotation in (90,180,270):
        d=pdf(source(rotation));p=plan(d)
        assert p['issues'] and not p['utterances']
        assert all(e['status']=='unresolved' for e in d.inventory[0]['source_outlines'])
