from zipfile import ZipFile

import pytest

from audiobook.intake import inspect_source
from audiobook.planner import plan


WORD = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
OFFICE_MATH = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
HTML_POWER = '<math><msup><mi>x</mi><mn>2</mn></msup></math>'
HTML_FRACTION = '<math><mfrac><mn>3</mn><mn>4</mn></mfrac></math>'


def omml(content):
    return f'<m:oMath>{content}</m:oMath>'


def math_run(value):
    return f'<m:r><m:t>{value}</m:t></m:r>'


def word_p(text, formula, ending='.'):
    return (f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r>{formula}'
            f'<w:r><w:t>{ending}</w:t></w:r></w:p>')


def write_word(path, body, notes=None):
    with ZipFile(path,'w') as archive:
        archive.writestr('word/document.xml',f'<w:document xmlns:w="{WORD}" xmlns:m="{OFFICE_MATH}">'
                         f'<w:body>{body}</w:body></w:document>')
        if notes:
            archive.writestr('word/footnotes.xml',f'<w:footnotes xmlns:w="{WORD}" xmlns:m="{OFFICE_MATH}">'
                             f'<w:footnote w:id="1">{notes}</w:footnote></w:footnotes>')


def write_writer(path, body, *, href='./Object%201', embedded=HTML_POWER):
    frame = (f'<draw:frame text:anchor-type="as-char"><draw:object xlink:href="{href}"/>'
             '<draw:image xlink:href="./ObjectReplacements/Object%201"/></draw:frame>')
    body = body.replace('FORMULA',frame)
    with ZipFile(path,'w') as archive:
        archive.writestr('mimetype','application/vnd.oasis.opendocument.text')
        archive.writestr('content.xml',
                         '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
                         ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
                         ' xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
                         ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
                         ' xmlns:xlink="http://www.w3.org/1999/xlink">'
                         f'<office:body><office:text>{body}</office:text></office:body></office:document-content>')
        archive.writestr('META-INF/manifest.xml',
                         '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                         '<manifest:file-entry manifest:full-path="Object 1/"'
                         ' manifest:media-type="application/vnd.oasis.opendocument.formula" /></manifest:manifest>')
        if embedded is not None:
            archive.writestr('Object 1/content.xml',embedded.replace('<math>',
                             '<math xmlns="http://www.w3.org/1998/Math/MathML">'))


WORD_POWER = omml(f'<m:sSup><m:e>{math_run("x")}</m:e><m:sup>{math_run("2")}</m:sup></m:sSup>')
WORD_FRACTION = omml(f'<m:f><m:num>{math_run("3")}</m:num><m:den>{math_run("4")}</m:den></m:f>')


@pytest.mark.parametrize(('html_formula','word_formula','reading'), [
    (HTML_POWER,WORD_POWER,'x to the power of 2'),
    (HTML_FRACTION,WORD_FRACTION,'the fraction with numerator 3 and denominator 4'),
    ('<math><msqrt><mn>9</mn></msqrt></math>',
     omml(f'<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/>'
          f'<m:e>{math_run("9")}</m:e></m:rad>'),'the square root of 9'),
    ('<math><msup><mi>x</mi><mrow><mn>2</mn><mo>+</mo><mn>3</mn></mrow></msup></math>',
     omml(f'<m:sSup><m:e>{math_run("x")}</m:e><m:sup>{math_run("2+3")}</m:sup></m:sSup>'),
     'x to the power of open parenthesis 2 plus 3 close parenthesis'),
])
def test_equivalent_formula_prose(tmp_path,html_formula,word_formula,reading):
    html,word,writer = (tmp_path/f'book.{suffix}' for suffix in ('html','docx','odt'))
    html.write_text(f'<p>The value is {html_formula}.</p>')
    write_word(word,word_p('The value is ',word_formula))
    write_writer(writer,'<text:p>The value is FORMULA.</text:p>',embedded=html_formula)
    for path in (html,word,writer):
        d = inspect_source(path,offline=True)
        result = plan(d)
        assert not result['issues']
        assert [u['text'] for u in result['utterances']]==[f'The value is {reading}.']
        block, = d.blocks
        span, = block.locator['inline_semantics']
        assert span['source']==block.source[span['source_start']:span['source_end']]
        if path==writer:
            assert span['source_locator'].startswith('Object 1/content.xml:')
        elif path==word:
            assert span['source_locator'].startswith('word/document.xml:')


def test_note_and_cell_use_the_same_formula_reading(tmp_path):
    html,word,writer = (tmp_path/f'book.{ext}' for ext in ('html','docx','odt'))
    html.write_text('<p>See the derivation<a role="doc-noteref" href="#n">1</a>.</p>'
                    f'<aside role="doc-footnote" id="n">Use {HTML_POWER}.</aside>'
                    f'<table><tr><th>Value</th></tr><tr><td>{HTML_POWER}</td></tr></table>')
    write_word(word,word_p('See the derivation','<w:r><w:footnoteReference w:id="1"/></w:r>')
               +'<w:tbl><w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc>'
               +word_p('Value','','')+'</w:tc></w:tr><w:tr><w:tc>'+word_p('',WORD_POWER,'')
               +'</w:tc></w:tr></w:tbl>',notes=word_p('Use ',WORD_POWER))
    write_writer(writer,'<text:p>See the derivation<text:note><text:note-citation>1</text:note-citation>'
                 '<text:note-body><text:p>Use FORMULA.</text:p></text:note-body></text:note>.</text:p>'
                 '<table:table><table:table-header-rows><table:table-row><table:table-cell>'
                 '<text:p>Value</text:p></table:table-cell></table:table-row></table:table-header-rows>'
                 '<table:table-row><table:table-cell><text:p>FORMULA</text:p></table:table-cell>'
                 '</table:table-row></table:table>')
    for path in (html,word,writer):
        d = inspect_source(path,offline=True)
        result = plan(d)
        assert not result['issues'], path
        spoken = ' '.join(unit['text'] for unit in result['utterances'])
        assert spoken=='See the derivation. Note. Use x to the power of 2. End note. Table. Value: x to the power of 2.'
        link, = d.blocks[0].links
        assert link['text']=='Use x2.'
        assert any(span['source']=='x2' and span.get('reading')=='x to the power of 2'
                   for span in link['inline_semantics'])
        assert next(block for block in d.blocks if block.role=='table').locator['rows']==[['Value'],['x2']]


def test_flat_writer_reads_body_and_inline_formula_from_files_and_archives(tmp_path):
    directory = tmp_path/'reading'
    directory.mkdir()
    flat = directory/'Harbor.fodt'
    formula = HTML_POWER.replace('<math>','<math xmlns="http://www.w3.org/1998/Math/MathML">')
    flat.write_text(
        '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
        ' xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' office:mimetype="application/vnd.oasis.opendocument.text">'
        '<office:meta><dc:title>The Harbor</dc:title></office:meta>'
        '<office:master-styles><text:p>Repeated running header.</text:p></office:master-styles>'
        '<office:body><office:text><text:p>The value is '
        f'<draw:frame text:anchor-type="as-char"><draw:object>{formula}</draw:object></draw:frame>.'
        '</text:p></office:text></office:body></office:document>',
        encoding='utf-8')
    archive = tmp_path/'Harbor.zip'
    with ZipFile(archive,'w') as container:
        container.write(flat,'edition/Harbor.xml')
    for path,original in ((flat,'Harbor.fodt'),(directory,'Harbor.fodt'),(archive,'edition/Harbor.xml')):
        doc = inspect_source(path,offline=True)
        planned = plan(doc)
        assert doc.title=='The Harbor'
        assert planned['issues']==[]
        assert [unit['text'] for unit in planned['utterances']]==['The value is x to the power of 2.']
        block, = doc.blocks
        assert block.locator['element'].startswith(original+':')
        assert block.locator['inline_semantics'][0]['source_locator'].startswith(original+':')


@pytest.mark.parametrize('href',['https://example.invalid/Math','../Object%201','./Object%201#different'])
def test_writer_does_not_bind_unrelated_or_external_formula(tmp_path,href):
    path = tmp_path/'book.odt'
    write_writer(path,'<text:p>The value is FORMULA.</text:p>',href=href)
    result = plan(inspect_source(path,offline=True))
    assert not result['utterances']
    assert 'needs a verified reading' in result['issues'][0]['reason']


def test_unsupported_equation_kind_is_held_without_flattening(tmp_path):
    html,word,writer = (tmp_path/f'book.{ext}' for ext in ('html','docx','odt'))
    html_formula = HTML_FRACTION.replace('<mfrac>','<mfrac linethickness="0">')
    html.write_text(f'<p>The value is {html_formula}.</p>')
    write_word(word,word_p('The value is ',WORD_FRACTION.replace('<m:f>','<m:f><m:fPr>'
                           '<m:type m:val="noBar"/></m:fPr>')))
    write_writer(writer,'<text:p>The value is FORMULA.</text:p>',embedded=html_formula)
    for path in (html,word,writer):
        d = inspect_source(path,offline=True)
        assert d.blocks[0].source=='The value is 34.'
        result = plan(d)
        assert not result['utterances']
        assert 'needs a verified reading' in result['issues'][0]['reason']


def test_dev8_pinned_prose_keeps_delivery_context(tmp_path):
    path = tmp_path/'book.txt'
    path.write_text('"Wait for the lantern,\nthen count the bells," she whispered.')
    pinned = 'dae46ffba0f49f2a1ba6f415a8f6c878a3829bf188ebdce5936d99fc9529415c'
    result = plan(inspect_source(path),{'context_implementation':pinned},rule_revision='faithful-18',
                  delivery_context=35)
    assert result['policy']['context_implementation']==pinned
    assert result['performance']['context_version']==35
    assert result['utterances'][0]['performance_candidate']['recipe']=='whisper'


def write_jats(path, body):
    path.write_text('<article xmlns:mml="http://www.w3.org/1998/Math/MathML" '
                    'xmlns:xlink="http://www.w3.org/1999/xlink">'
                    f'<body>{body}</body></article>')


def jats_formula(body, *, tag='inline-formula'):
    return f'<{tag}><alternatives>{body}<graphic xlink:href="outside.png"/></alternatives></{tag}>'


def test_publishing_xml_uses_one_authored_rendition_in_prose_tables_and_notes(tmp_path):
    path = tmp_path/'geometry.jats'
    power = jats_formula(HTML_POWER.replace('<math>','<math xmlns="http://www.w3.org/1998/Math/MathML">'))
    ratio = jats_formula(HTML_FRACTION.replace('<math>','<math xmlns="http://www.w3.org/1998/Math/MathML">'),
                         tag='disp-formula')
    write_jats(path,f'<p>The area is {power}.</p>{ratio}'
               f'<table><tr><th>Value</th></tr><tr><td>{power}</td></tr></table>'
               '<p>See the note<xref rid="f1">1</xref>.</p>'
               f'<fn id="f1"><p>Read {power} here.</p></fn>')
    doc = inspect_source(path,offline=True)
    result = plan(doc)
    assert not result['issues']
    spoken = ' '.join(unit['text'] for unit in result['utterances'])
    assert spoken=='The area is x to the power of 2. the fraction with numerator 3 and denominator 4. Table. Value: x to the power of 2. See the note. Note. Read x to the power of 2 here. End note.'
    spans = doc.blocks[0].locator['inline_semantics']
    for span in (s for s in spans if s['kind']=='formula'):
        assert doc.blocks[0].source[span['source_start']:span['source_end']]=='x2'
        assert 'geometry.jats:/article/body/p[1]/inline-formula/alternatives/' in span['source_locator']
    assert 'outside.png' in path.read_text()


@pytest.mark.parametrize('representation',[jats_formula(HTML_POWER),
    jats_formula('<mml:math><mml:mi>a</mml:mi></mml:math><mml:math><mml:mi>b</mml:mi></mml:math>'),
    jats_formula('<tex-math>33</tex-math>')])
def test_publishing_xml_requires_one_bound_mathml_rendition(tmp_path,representation):
    path = tmp_path/'geometry.jats'
    write_jats(path,f'<p>The value is {representation}.</p><p>Another sentence.</p>')
    result = plan(inspect_source(path,offline=True))
    assert [u['text'] for u in result['utterances']]==['Another sentence.']
    assert 'one bound MathML' in result['issues'][0]['reason']
