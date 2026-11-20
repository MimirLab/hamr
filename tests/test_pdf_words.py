"""Pre-synthesis source holds; no dictionary or text layer chooses a winner."""
from copy import deepcopy
import io
import pytest
from PIL import Image,ImageDraw
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from audiobook.pdf_words import localize_words,words
from audiobook.planner import plan
from audiobook import importers


def parts(first,second='The clear paragraph remains available.'):
    return [{'text':first,'locator':{'paragraph':0,'rect':[0,0,1,1]},'issue':None},
            {'text':second,'locator':{'paragraph':1,'rect':[0,2,1,3]},'issue':None}]


@pytest.mark.parametrize('native,visual',[
    ('Each family chooses either line.','Cach family chooses cither line.'),
    ('It had remained hidden.','Ithad remained hidden.'),
    ('The important exception remains.','The exception remains.'),
    ('Cats chase mice.','Mice chase cats.'),
    ('Smith described the sample.','Smyth described the sample.'),
])
def test_word_and_order_disagreements_hold_one_covered_region(native,visual):
    p=parts(visual);original=deepcopy(p)
    r=localize_words(p,native+' '+p[1]['text'],visual+' '+p[1]['text'],lambda box:native if box[1]==0 else p[1]['text'])
    assert r['scope']=='paragraph' and r['held_regions']==1
    assert p[0]['issue'] and not p[1]['issue']
    assert [(q['text'],q['locator']) for q in p]==[(q['text'],q['locator']) for q in original]


@pytest.mark.parametrize('native,visual',[("It's ready.",'It’s ready.'),('The office opens.','The oﬃce opens.'),('Café remains.','Cafe\u0301 remains.')])
def test_supported_typography_does_not_create_a_spelling_rewrite(native,visual):
    p=parts(visual);r=localize_words(p,native+' '+p[1]['text'],visual+' '+p[1]['text'],lambda box:native if box[1]==0 else p[1]['text'])
    assert r['native_word_coverage'] and r['visual_word_coverage'] and r['held_regions']==0
    assert p[0]['text']==visual


@pytest.mark.parametrize('qualified,reader',[(False,lambda _:''),(True,lambda _:'Each family. Clear text.')])
def test_incomplete_or_duplicated_geometry_cannot_clear_a_page_dispute(qualified,reader):
    p=parts('Cach family.','Clear text.');r=localize_words(p,'Each family. Clear text.','Cach family. Clear text.',reader,qualified)
    assert r['scope']=='page' and r['held_regions']==2 and all(q['issue'] for q in p)


def test_page_creation_order_does_not_override_complete_local_region_agreement():
    p=parts('First clear paragraph.','Second clear paragraph.')
    native=p[1]['text']+' '+p[0]['text'];visual=p[0]['text']+' '+p[1]['text']
    r=localize_words(p,native,visual,lambda box:p[0]['text'] if box[1]==0 else p[1]['text'])
    assert r['scope']=='paragraph' and not any(q['issue'] for q in p)


def test_only_explicit_native_soft_hyphen_evidence_supports_an_existing_visual_join():
    p=parts('An inter-\nnational result.')
    p[0]['locator']['line_wrap_joins']=[{'original':'inter-\nnational','spoken':'international'}]
    native='An inter\u00ad\nnational result.'
    r=localize_words(p,native+' '+p[1]['text'],p[0]['text']+' '+p[1]['text'],lambda box:native if box[1]==0 else p[1]['text'])
    assert r['held_regions']==0 and p[0]['text']=='An inter-\nnational result.'
    p=parts('We must resign today.');p[0]['locator']['line_wrap_joins']=[{'original':'re-\nsign','spoken':'resign'}]
    r=localize_words(p,'We must re-sign today. '+p[1]['text'],p[0]['text']+' '+p[1]['text'],lambda box:'We must re-sign today.' if box[1]==0 else p[1]['text'])
    assert r['held_regions']==1


def test_absent_native_observation_is_unavailable_evidence_and_keeps_prior_issue():
    p=parts('Only OCR is available.');p[0]['issue']='Low confidence.'
    r=localize_words(p,'',p[0]['text'],lambda _:pytest.fail('No native text to read'))
    assert r['scope']=='unavailable' and not r['native_word_coverage']
    assert p[0]['issue']=='Low confidence.'


def fixture(native,visual,monkeypatch,visible=None):
    clear='The clear paragraph remains available.'
    image=Image.new('RGB',(400,300),'white');draw=ImageDraw.Draw(image)
    draw.text((20,70),visible or native,fill='black');draw.text((20,120),clear,fill='black')
    data=io.BytesIO();canvas=Canvas(data,pagesize=(400,300));canvas.drawImage(ImageReader(image),0,0,width=400,height=300)
    for y,text in [(220,native),(170,clear)]:
        hidden=canvas.beginText(20,y);hidden.setTextRenderMode(3);hidden.textLine(text);canvas.drawText(hidden)
    canvas.save()
    header='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    tsv=header+f'5\t1\t1\t1\t1\t1\t40\t140\t680\t26\t99\t{visual}\n'+f'5\t1\t2\t1\t1\t1\t40\t240\t680\t26\t99\t{clear}\n'
    monkeypatch.setattr('audiobook.pdf_ocr.configuration',lambda:None)
    monkeypatch.setattr('audiobook.pdf_ocr.observe',lambda *a:(tsv,False))
    return data.getvalue(),clear


def test_actual_pdf_import_holds_confident_word_corruption_before_request_creation(monkeypatch):
    data,clear=fixture('Each family chooses either line.','Cach family chooses cither line.',monkeypatch)
    doc=importers.pdf(data);prepared=plan(doc)
    assert not doc.inventory[0]['critical_disputes']  # Old numeric/symbol check misses this.
    assert doc.inventory[0]['lexical_localization']['scope']=='paragraph'
    assert doc.inventory[0]['lexical_localization']['held_regions']==1
    assert len(prepared['issues'])==1
    requests=' '.join(u['text'] for u in prepared['utterances'])
    assert clear in requests and 'Cach' not in requests and 'cither' not in requests
    bad=next(b for b in doc.blocks if b.source.startswith('Cach'))
    assert bad.issue and bad.source=='Cach family chooses cither line.'
    from audiobook.core import digest
    policy={'document':{'source_sha256':doc.source_sha256,'blocks':{bad.id:{'action':'speak','verified':True,'reason':'Known visible wording in this independent fixture.','expected_source_sha256':digest(bad.source.encode()),'spoken':'Each family chooses either line.'}}}}
    fixed=plan(doc,policy)
    assert not fixed['issues'] and 'Each family chooses either line.' in ' '.join(u['text'] for u in fixed['utterances'])


def test_stale_hidden_native_text_is_held_without_replacing_correct_visual_text(monkeypatch):
    data,clear=fixture('Cach family chooses cither line.','Each family chooses either line.',monkeypatch,visible='Each family chooses either line.')
    doc=importers.pdf(data);prepared=plan(doc)
    assert len(prepared['issues'])==1 and clear in ' '.join(u['text'] for u in prepared['utterances'])
    assert any(b.source=='Each family chooses either line.' and b.issue for b in doc.blocks)


def test_new_word_diagnostics_preserve_prior_block_ids_and_source_locators(monkeypatch):
    data,_=fixture('Each family chooses either line.','Cach family chooses cither line.',monkeypatch)
    import audiobook.pdf_words as module
    actual=module.localize_words
    with monkeypatch.context() as scope:
        scope.setattr(module,'localize_words',lambda *a,**k:{'scope':'not_applied'})
        previous=importers.pdf(data)
    current=importers.pdf(data)
    assert [(b.id,b.source,b.locator) for b in previous.blocks]==[(b.id,b.source,b.locator) for b in current.blocks]
    assert not previous.blocks[0].issue and current.blocks[0].issue
