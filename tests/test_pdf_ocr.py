import io
import pytest
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


def test_mixed_crop_preserves_native_text_and_binds_authored_caption(monkeypatch):
    from PIL import ImageChops
    from audiobook.pdf_ocr import observe

    image=Image.new('RGB',(900,300),'white')
    ImageDraw.Draw(image).multiline_text((30,25), 'The scanned region contains these words.\nThis line belongs to the same inset.',
                                         font=ImageFont.load_default(size=37),fill='black',spacing=16)
    output=io.BytesIO()
    canvas=Canvas(output,pagesize=(600,800))
    canvas.drawString(20,750,'Native prose remains in the book.')
    canvas.drawImage(ImageReader(image),20,410,width=450,height=150)
    canvas.drawString(25,395,'Figure 1. The original caption.')
    canvas.save()
    sizes=[]

    def record(png,*args):
        with Image.open(io.BytesIO(png)) as crop:
            sizes.append(crop.size)
            assert ImageChops.invert(crop.convert('RGB')).getbbox()
        return observe(png,*args)

    monkeypatch.setattr('audiobook.pdf_ocr.observe',record)
    doc=pdf(output.getvalue())
    assert sizes == [(900,300)]
    assert [b.source for b in doc.blocks].count('Native prose remains in the book.') == 1
    assert any('The scanned region contains these words.' in b.source for b in doc.blocks)
    assert doc.inventory[0]['route']=='regional_visual_ocr'
    observation,=doc.inventory[0]['visual_observations']
    caption=next(b for b in doc.blocks if b.role=='caption')
    assert observation['caption_block']==caption.id
    assert observation['rect']==[20,410,470,560]


def test_real_scanned_columns_preserve_column_major_order():
    im=Image.new('RGB',(1600,2100),'white')
    drawing=ImageDraw.Draw(im)
    font=ImageFont.load_default(size=32)
    left=['LEFT alpha.','LEFT beta.','LEFT gamma.','LEFT delta.']
    right=['RIGHT one.','RIGHT two.','RIGHT three.','RIGHT four.']
    for i,(first,last) in enumerate(zip(left,right)):
        drawing.text((82,118+75*i),first,font=font,fill='black')
        drawing.text((855,118+75*i),last,font=font,fill='black')
    output=io.BytesIO()
    canvas=Canvas(output,pagesize=(800,1050))
    canvas.drawImage(ImageReader(im),0,0,width=800,height=1050)
    canvas.save()
    doc=pdf(output.getvalue())
    assert ' '.join(' '.join(block.source.split()) for block in doc.blocks) == ' '.join(left+right)


@pytest.mark.parametrize(('line_reads','expected_initial'), [
    ((('"I','opened'),('"I','opened')), '"I'),
    ((('"I','opened'),('"T','opened')), '"T'),
    ((('"I','closed'),), '"T'),
    ((('"T','opened'),), '"T'),
])
def test_quoted_initial_rechecked_before_planning(monkeypatch, line_reads, expected_initial):
    image = Image.new('RGB',(440,240),'white')
    ImageDraw.Draw(image).text((40,35),'"I opened the door."',fill='black')
    output = io.BytesIO()
    canvas = Canvas(output,pagesize=(220,120))
    canvas.drawImage(ImageReader(image),0,0,width=220,height=120)
    canvas.save()
    remaining = iter(line_reads)
    observed = []
    recipe = {'page_segmentation':'default'}

    def observe(png, config, cache, scope):
        segmentation = config[2]['page_segmentation']
        observed.append(segmentation)
        initial, verb = next(remaining) if segmentation==7 else ('"T','opened')
        rows = ['level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext']
        for index, word in enumerate((initial,verb,'the','door."')):
            rows.append(f'5\t1\t1\t1\t1\t{index+1}\t{40+40*index}\t35\t30\t20\t82\t{word}')
        return '\n'.join(rows)+'\n',False

    monkeypatch.setattr('audiobook.pdf_ocr.observe',observe)
    doc = pdf(output.getvalue(),ocr_config=(None,None,recipe,False))
    unit, = plan(doc)['utterances']
    assert unit['text'] == f'{expected_initial} opened the door."'
    assert observed == ['default', *([7]*len(line_reads))]
    if expected_initial == '"I':
        correction, = doc.inventory[0]['ocr_rechecks']
        assert (correction['original'],correction['text']) == ('"T','"I')
        assert correction['rect_pixels'] == [40,35,30,20]
    else:
        assert 'ocr_rechecks' not in doc.inventory[0]


@pytest.mark.parametrize(('connector','stem','bar_bottom','expected'),[
    ('until','come',43,'I'),
    ('until','come',49,'|'),
    ('until','sum',43,'|'),
    ('or','come',43,'|'),
])
def test_ocr_pipe_needs_prose_and_a_matching_glyph(connector,stem,bar_bottom,expected):
    from audiobook.pdf_visual import recheck_pipe_pronouns

    page = Image.new('L',(230,80),255)
    draw = ImageDraw.Draw(page)
    draw.rectangle((40,20,42,bar_bottom),fill=0)
    draw.rectangle((154,20,157,43),fill=0)
    words = [
        ['5','1','1','1','1',str(position),str(x),'20',str(width),str(height),'94',token]
        for position,(token,x,width,height) in enumerate((
            (connector,8,18,24),('|',40,1,bar_bottom-19),(stem,65,39,24),
            ('“I',140,18,24),('can',180,30,24)
        ),1)
    ]
    correction = recheck_pipe_pronouns(page,words)
    assert words[1][11]==expected
    assert bool(correction)==(expected=='I')
    if correction:
        assert correction[0]['original']=='|' and correction[0]['reason']=='matching_pronoun_on_line'


def test_native_cache_does_not_probe_ocr(tmp_path,monkeypatch):
    from audiobook.importers import ADAPTERS, cached_inspect

    source=tmp_path/'Native.pdf'
    canvas=Canvas(str(source),pagesize=(600,800))
    canvas.drawString(20,750,'All source words are visible native text.')
    canvas.save()
    calls=[]

    def unexpected_ocr(*args):
        raise AssertionError('A native PDF must bypass OCR')

    def inspect(*args,**kwargs):
        calls.append(1)
        return pdf(*args,**kwargs)

    monkeypatch.setattr('audiobook.pdf_ocr.configuration',unexpected_ocr)
    monkeypatch.setitem(ADAPTERS,'.pdf',inspect)
    cache=tmp_path/'cache'
    first=cached_inspect(source,cache)
    second=cached_inspect(source,cache)
    assert first.json()==second.json()
    assert calls == [1]


def test_authored_decorative_raster_bypasses_ocr(monkeypatch):
    output=io.BytesIO()
    canvas=Canvas(output,pagesize=(600,800))
    canvas.drawString(20,750,'Native text remains the source.')
    canvas._code.append('/Artifact BMC')
    canvas.drawImage(ImageReader(Image.new('RGB',(20,20),'black')),510,710,width=20,height=20)
    canvas._code.append('EMC')
    canvas.save()

    def unexpected_ocr(*args):
        raise AssertionError('An authored PDF artifact must not request OCR')

    monkeypatch.setattr('audiobook.pdf_ocr.configuration',unexpected_ocr)
    doc=pdf(output.getvalue())
    assert doc.inventory[0]['authored_raster_artifacts']==[[510,710,530,730]]
    assert doc.inventory[0]['route']=='native'
