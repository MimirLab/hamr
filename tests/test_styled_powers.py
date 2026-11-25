from zipfile import ZipFile

import pytest
from reportlab.pdfgen import canvas

from audiobook.intake import inspect_source
from audiobook.pdf_geometry import redundant_note_icons
from audiobook.planner import plan
from test_authored_math import word_p, write_word
from test_upstream_epub import book as epub_book


EXPECTED = 'Compute ten to the power of three to check the result.'
MARKUP = 'Compute 10<sup>3</sup> to check the result.'


@pytest.mark.parametrize('suffix',['.html','.md','.epub','.adoc','.docx','.fodt','.odt'])
def test_default_styled_power_reading_preserves_source_offsets(tmp_path,suffix):
    source = tmp_path/('Powers'+suffix)
    if suffix=='.epub':
        source.write_bytes(epub_book({'c':f'<p>{MARKUP}</p>'},['c']))
    elif suffix=='.adoc':
        source.write_text('= Powers\n\n'+MARKUP.replace('<sup>3</sup>','^3^'))
    elif suffix=='.docx':
        raised = '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>3</w:t></w:r>'
        write_word(source,word_p('Compute 10',raised,' to check the result.'))
    elif suffix in ('.fodt','.odt'):
        odf_source(source)
    elif suffix=='.md':
        source.write_text(MARKUP)
    else:
        source.write_text(f'<p>{MARKUP}</p>')

    document = inspect_source(source,offline=True)
    result = plan(document)
    assert result['issues']==[]
    assert [row['text'] for row in result['utterances'] if row['text']!='Powers']==[EXPECTED]
    body = next(block for block in document.blocks if block.role=='body')
    assert body.source=='Compute 103 to check the result.'
    formula = next(s for s in body.locator['inline_semantics'] if s['kind']=='formula')
    assert body.source[formula['source_start']:formula['source_end']]==formula['source']=='103'


def odf_source(source):
    styles = ('<office:styles><style:style style:name="Parent" style:family="text">'
              '<style:text-properties style:text-position="super 58%"/></style:style>'
              '<style:style style:name="Exponent" style:family="text" style:parent-style-name="Parent"/>'
              '</office:styles>')
    write_odf(source,styles,'<text:p>Compute 10<text:span text:style-name="Exponent">3</text:span>'
              ' to check the result.</text:p>')


def write_odf(source,styles,paragraphs):
    ns = (' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
          ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
          ' xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
          ' xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"')
    media = 'application/vnd.oasis.opendocument.text'
    body = f'<office:body><office:text>{paragraphs}</office:text></office:body>'
    if source.suffix=='.fodt':
        source.write_text(f'<office:document{ns} office:mimetype="{media}">{styles}{body}</office:document>')
    else:
        with ZipFile(source,'w') as archive:
            archive.writestr('mimetype',media)
            archive.writestr('content.xml',f'<office:document-content{ns}>{body}</office:document-content>')
            archive.writestr('styles.xml',f'<office:document-styles{ns}>{styles}</office:document-styles>')


@pytest.mark.parametrize('suffix',['.odt','.fodt'])
def test_odf_character_styles_use_effective_marks_on_the_exact_word(tmp_path,suffix):
    source = tmp_path/('Marks'+suffix)
    styles = ('''<office:styles>
        <style:style style:name="Lean" style:family="text">
            <style:text-properties fo:font-style="italic"/>
        </style:style>
        <style:style style:name="Upright" style:family="text">
            <style:text-properties fo:font-style="normal"/>
        </style:style>
        <style:style style:name="Heavy" style:family="text">
            <style:text-properties fo:font-weight="700"/>
        </style:style>
        <style:style style:name="Light" style:family="text">
            <style:text-properties fo:font-weight="normal"/>
        </style:style>
        <style:style style:name="Alias" style:family="text" style:parent-style-name="Lean"/>
        <style:style style:name="Green" style:family="text">
            <style:text-properties fo:color="#008800"/>
        </style:style>
        </office:styles>''')
    body = '''<text:p>“Did <text:span text:style-name="Alias">y<text:span text:style-name="Green">o</text:span>u</text:span> see who left?” the doctor asked.</text:p>
        <text:p>“Can <text:span text:style-name="Lean"><text:span text:style-name="Upright">you</text:span></text:span> come here?” the doctor asked.</text:p>
        <text:p>“Have <text:span text:style-name="Lean" text:class-names="Upright Heavy">you</text:span> read this?” the doctor asked.</text:p>
        <text:p>“May <text:span text:style-name="Heavy"><text:span text:style-name="Light">I</text:span></text:span> take this?” the doctor asked.</text:p>
        <text:p>“Will <text:span text:style-name="Lean">y<text:span text:style-name="Upright">o</text:span>u</text:span> go first?” the doctor asked.</text:p>'''
    write_odf(source,styles,body)
    document = inspect_source(source)
    result = plan(document)
    assert not result['issues']
    assert result['performance']['candidates']==2
    first,reset,bold,light,partial = document.blocks
    mark, = first.locator['inline_semantics']
    assert mark['source']=='you' and mark['style']=='italic'
    assert mark['style_definition']['id']=='Lean'
    assert mark['style_definition']['source_file']==('styles.xml' if suffix=='.odt' else source.name)
    mark, = reset.locator['inline_semantics']
    assert mark['source']=='you' and mark['style']=='normal'
    assert [(m['kind'],m['source']) for m in bold.locator['inline_semantics']]==[
        ('font_style','you'),('emphasis','you')]
    assert not light.locator['inline_semantics']
    assert [m['source'] for m in partial.locator['inline_semantics']]==['y','o','u']


def test_prose_relations_guide_scientific_notation_but_not_citations(tmp_path):
    source = tmp_path/'Probability.epub'
    body = '''<style>.raised {vertical-align:super}</style>
        <p>The probability threshold is about 2<sup>80</sup> (the formula for testing follows).</p>
        <p>2<sup>80</sup> is 1.2 x 10<span class="raised">24</span>.</p>
        <p>2<sup>3</sup> = 8.</p>
        <p>Compute 10<span class="raised">-3</span>.</p>
        <p>Compute 10<a role="doc-noteref" href="#note"><span class="raised">3</span></a> volts.</p>
        <aside role="doc-footnote" id="note">Confirm the limit first.</aside>
        <p>Section 2<sup>3</sup> is elsewhere.</p>
        <p><span hidden="hidden">Compute </span>10<sup>3</sup> volts.</p>'''
    source.write_bytes(epub_book({'c':body},['c']))
    result = plan(inspect_source(source,offline=True))
    speech = ' '.join(row['text'] for row in result['utterances'])
    assert 'about two to the power of eighty' in speech
    assert 'two to the power of eighty is one point two times ten to the power of twenty four.' in speech
    assert 'two to the power of three equals eight.' in speech
    assert 'Compute ten to the power of minus three.' in speech
    assert 'Compute 10 volts. Note. Confirm the limit first. End note.' in speech
    assert len(result['issues'])==2 and all('Styled superscript' in row['reason'] for row in result['issues'])


def test_pdf_geometry_keeps_raised_digits_with_their_actual_line(tmp_path):
    path = tmp_path/'Powers.pdf'
    pdf = canvas.Canvas(str(path))
    for index,rise in enumerate((2,4,6)):
        line = pdf.beginText(72,700-index*30)
        line.setFont('Helvetica',12)
        line.textOut('Compute 10')
        line.setRise(rise)
        line.setFont('Helvetica',8)
        line.textOut('3')
        line.setRise(0)
        line.setFont('Helvetica',12)
        line.textOut(' to check the result.')
        pdf.drawText(line)
    pdf.drawString(72,600,'Room 103 is open.')
    pdf.save()

    document = inspect_source(path,offline=True)
    assert len(document.blocks)==4
    result = plan(document)
    assert result['issues']==[]
    actual = [row['text'] for row in result['utterances']]
    assert actual[:3]==[EXPECTED]*3 and actual[3]=='Room one zero three is open.'
    for block in document.blocks[:3]:
        script = next(s for s in block.locator['inline_semantics'] if s['kind']=='math_script')
        assert script['native_range']==[script['native_base']+1,script['native_base']+2]


def test_pdf_information_icon_requires_a_matching_font_and_authored_label(monkeypatch):
    from audiobook.pdf_geometry import raw

    class TextPage:
        def __init__(self,source):
            self.source = source

        def count_chars(self):
            return len(self.source)

        def get_text_range(self,index,count):
            return self.source[index:index+count]

        def get_charbox(self,index):
            return (0,10,5,20) if self.source[index]=='\uf05a' else (10,20,15,25)

    def font(page,index,buffer,length,flags):
        buffer.value = b'ABCDZZ+FontAwesome5Free-Solid'

    monkeypatch.setattr(raw,'FPDFText_GetFontInfo',font)
    source = 'Previous paragraph.\r\n\uf05a\r\nA short note about safety.'
    item, = redundant_note_icons(TextPage(source),source)
    assert item['native_index']==source.index('\uf05a') and item['codepoint']=='U+F05A'
    unknown = source.replace('A short note','An unrelated figure')
    assert not redundant_note_icons(TextPage(unknown),unknown)
    monkeypatch.setattr(raw,'FPDFText_GetFontInfo',lambda page,index,buffer,length,flags: None)
    assert not redundant_note_icons(TextPage(source),source)
