from reportlab.pdfgen.canvas import Canvas

from audiobook.intake import inspect_source
from audiobook.planner import plan


def test_native_pdf_preserves_boxed_terminal_and_declared_folio(tmp_path):
    path = tmp_path/'Manual.pdf'
    pdf = Canvas(str(path),pagesize=(420,380))
    pdf.addPageLabel(0,style='D',start=219)
    pdf.drawString(40,330,'Before the demonstration, create a clean copy.')
    pdf.setFillGray(.92)
    pdf.rect(32,242,335,65,stroke=0,fill=1)
    pdf.setFillGray(0)
    pdf.setFont('Courier',11)
    pdf.drawString(43,285,'$ git show abcde1')
    pdf.drawString(43,264,'abcde1 Open the harbor')
    pdf.setFont('Helvetica',11)
    pdf.drawString(40,210,'The verifier checked all 219 records.')
    pdf.drawString(40,148,'$ 150 is the fee for this example.')
    pdf.drawString(352,15,'219')
    pdf.save()

    doc = inspect_source(path,offline=True)
    literal, = [row for row in doc.blocks if row.role=='code']
    assert literal.source=='$ git show abcde1\nabcde1 Open the harbor'
    assert len(literal.locator['native_ranges'])==2
    footer, = [row for row in doc.blocks if row.role=='furniture']
    assert footer.source=='219'
    outcome = plan(doc)
    spoken = ' '.join(row['text'] for row in outcome['utterances'])
    assert not outcome['issues']
    assert 'A literal block is available in the companion.' in spoken
    assert 'abcde1' not in spoken and '219 records' in spoken
    assert any(row.source=='$ 150 is the fee for this example.' and row.role=='body' for row in doc.blocks)


def test_explicit_plain_arithmetic_uses_shared_source_reading(tmp_path):
    left = 'The formula for collision probability is '
    expression = 'p = (n(n-1)/2) * (1/2^160)'
    final = '. Store the result.'
    plain = tmp_path/'Manual.txt'
    plain.write_text(left+expression+final,encoding='utf-8')
    html = tmp_path/'Manual.html'
    html.write_text(f'<p>{left}<code>{expression}</code>{final}</p>',encoding='utf-8')
    pdf = tmp_path/'Manual.pdf'
    canvas = Canvas(str(pdf))
    canvas.drawString(54,720,'The formula')
    canvas.drawString(54,706,'for collision probability is '+expression+final)
    canvas.save()
    readings = []
    for path in (plain,html,pdf):
        doc = inspect_source(path,offline=True)
        outcome = plan(doc)
        assert not outcome['issues']
        formula, = [span for row in doc.blocks for span in row.locator.get('inline_semantics',[])
                    if span['kind']=='formula']
        assert formula['source']==expression
        readings.append(' '.join(row['text'] for row in outcome['utterances']))
    assert readings[0]==readings[1]==readings[2]
    assert 'n times open parenthesis n minus 1 close parenthesis divided by 2' in readings[0]
    assert '2 to the power of 160 close parenthesis. Store the result.' in readings[0]


def test_arithmetic_requires_the_formula_cue_and_unambiguous_operators(tmp_path):
    path = tmp_path/'Syntax.txt'
    path.write_text('Store the literal p = 2^80. The formula is y = 3^2.5. The formula is p = 1//2^3.',
                    encoding='utf-8')
    doc = inspect_source(path,offline=True)
    assert not any(row.locator.get('inline_semantics') for row in doc.blocks)


def test_pdf_tip_icon_requires_both_converter_and_titled_panel(monkeypatch):
    from audiobook.pdf_geometry import asciidoctor_tips,raw

    source = '\uf0eb\r\nUse the short form.'

    class TextPage:
        def count_chars(self):
            return len(source)

        def get_text_range(self,index,count):
            return source[index:index+count]

        def get_charbox(self,index):
            return (60,58,85,81) if index==0 else (120,72,125,83)

    class Divider:
        def get_bounds(self):
            return (108,40,109,91)

    class Page:
        def get_objects(self,**options):
            return [Divider()]

    def font(page,index,buffer,length,flags):
        buffer.value = b'FontAwesome5Free-Regular'

    monkeypatch.setattr(raw,'FPDFText_GetFontInfo',font)
    record, = asciidoctor_tips(Page(),TextPage(),source,'Asciidoctor PDF 2.3.17')
    assert record['label']=='Tip.' and record['heading_index']==3
    assert not asciidoctor_tips(Page(),TextPage(),source,'PDF generator')
    monkeypatch.setattr(Page,'get_objects',lambda self,**options: [])
    assert not asciidoctor_tips(Page(),TextPage(),source,'Asciidoctor PDF 2.3.17')
