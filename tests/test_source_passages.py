from xml.sax.saxutils import escape
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen.canvas import Canvas

from audiobook.intake import inspect_source
from audiobook.planner import plan


LINES = ('"Wait for the lantern,', 'then count the bells,"', 'she whispered.')
PROSE = ' '.join(LINES)


def write_word(path, text):
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    with ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{ns}"><w:body><w:p>'
                         f'<w:r>{text}</w:r></w:p></w:body></w:document>')


def write_scan(path, lines):
    image = Image.new('RGB', (900,420), 'white')
    ImageDraw.Draw(image).multiline_text((80,60), '\n'.join(lines),
        font=ImageFont.load_default(size=54), fill='black', spacing=5)
    image.save(path)


def test_equivalent_prose_has_one_delivery_across_native_and_ocr_readers(tmp_path):
    text = tmp_path/'book.txt'
    markdown = tmp_path/'book.md'
    html = tmp_path/'book.html'
    docx = tmp_path/'book.docx'
    pdf = tmp_path/'book.pdf'
    scan = tmp_path/'book.png'
    text.write_text('\n'.join(LINES))
    markdown.write_text('\n'.join(LINES))
    html.write_text(f'<html><body><p>{escape(PROSE)}</p></body></html>')
    write_word(docx, f'<w:t>{escape(PROSE)}</w:t>')
    canvas = Canvas(str(pdf))
    canvas.setFont('Helvetica', 14)
    for index,line in enumerate(LINES):
        canvas.drawString(64, 720-index*16, line)
    canvas.save()
    write_scan(scan, LINES)
    requests = set()
    for path in (text,markdown,html,docx,pdf,scan):
        document = inspect_source(path,offline=True)
        result = plan(document)
        assert not result['issues'], path.suffix
        unit, = result['utterances']
        cue = unit['performance_candidate']
        assert cue['recipe'] == 'whisper' and cue['speech'] == PROSE, path.suffix
        block = next(block for block in document.blocks if block.id==cue['source']['block'])
        assert block.source[slice(*cue['source']['span'])] == 'whispered', path.suffix
        requests.add((cue['speech'],cue['synthesis'],cue['instruction'],cue['cfg_scale']))
    assert len(requests)==1
    assert '\n' in inspect_source(text).blocks[0].source
    assert '\n' in inspect_source(scan).blocks[0].source


def test_word_authored_line_break_remains_a_boundary(tmp_path):
    docx = tmp_path/'verse.docx'
    write_word(docx, f'<w:t>{escape(LINES[0])}</w:t><w:br/>'
                     f'<w:t>{escape(" ".join(LINES[1:]))}</w:t>')
    document = inspect_source(docx)
    assert document.blocks[0].locator['authored_line_breaks']
    unit, = plan(document)['utterances']
    assert unit['delivery_boundary']=='authored_lines'
    assert 'performance_candidate' not in unit


def test_pinned_plan_keeps_the_previous_text_layout_and_context_reader(tmp_path):
    text = tmp_path/'book.txt'
    text.write_text('\n'.join(LINES))
    pinned = '163ddcdc598df9b419aba3de219f26bd20bd5912c331e729ed921fd98c579287'
    result = plan(inspect_source(text), {'context_implementation':pinned}, rule_revision='faithful-17')
    assert result['policy']['context_implementation']==pinned
    assert result['performance']['context_version']==34
    assert not any('performance_candidate' in unit for unit in result['utterances'])


def test_scan_context_uses_the_same_time_evidence_as_text(tmp_path):
    lines = ('We board the train at about', '05:15 in the morning.')
    text = tmp_path/'train.txt'
    scan = tmp_path/'train.png'
    text.write_text('\n'.join(lines))
    write_scan(scan,lines)
    outputs = []
    for path in (text,scan):
        result = plan(inspect_source(path,offline=True))
        unit, = result['utterances']
        assert 'at about five fifteen in the morning' in unit['text']
        outputs.append(unit['text'])
    assert outputs[0]==outputs[1]


def test_scan_holds_a_word_split_across_two_pages(tmp_path):
    scans = tmp_path/'book'
    scans.mkdir()
    write_scan(scans/'page1.png', ['We met at the inter-'])
    write_scan(scans/'page2.png', ['national gathering.'])
    source = inspect_source(scans,offline=True)
    assert [block.source for block in source.blocks]==['We met at the inter-','national gathering.']
    result = plan(source)
    assert not result['utterances']
    assert all('split across source pages' in issue['reason'] for issue in result['issues'])
    assert [issue['location']['page'] for issue in result['issues']]==[1,2]
