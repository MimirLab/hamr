from zipfile import ZipFile

import pytest

from audiobook.intake import inspect_source
from audiobook.planner import plan


PARAGRAPH = 'Run either tex first.tex or tex second.tex'
END = 'Keep the original.'
HTML = """<p>Run either</p>
<pre><code>tex first.tex</code></pre>
<p>or</p>
<pre><code>tex second.tex</code></pre>
<p>Keep the original.</p>
<pre><code>tex example.tex
tex another.tex</code></pre>
"""
MARKDOWN = """Run either

```
tex first.tex
```

or

```
tex second.tex
```

Keep the original.

```
tex example.tex
tex another.tex
```
"""
LATEX = r"""\documentclass{article}
\newenvironment{displaycode}{\begin{quote}}{\end{quote}}
\begin{document}
Run either
\begin{displaycode}
\verb|tex first.tex|
\end{displaycode}
or
\begin{displaycode}
\verb|tex second.tex|
\end{displaycode}

Keep the original.

\begin{verbatim}
tex example.tex
tex another.tex
\end{verbatim}
\end{document}
"""


@pytest.mark.parametrize(('suffix','source'),[('.html',HTML),('.md',MARKDOWN),('.tex',LATEX)])
def test_displayed_command_alternatives_join_in_source_preparation(tmp_path,suffix,source):
    main = tmp_path/('instructions'+suffix)
    main.write_text(source,encoding='utf-8')
    archive = tmp_path/'instructions.zip'
    with ZipFile(archive,'w') as target:
        target.write(main,'Edition/'+main.name)
    for path in (main,archive):
        doc = inspect_source(path)
        book = plan(doc)
        assert not book['issues']
        assert [part['text'] for part in book['utterances']]==[
            PARAGRAPH+'.',END,'A literal block is available in the companion.']
        compound = doc.blocks[0]
        pieces = compound.locator['source_fragments']
        assert [compound.source[a:b] for a,b in (part['passage_range'] for part in pieces)]==[
            'Run either','tex first.tex','or','tex second.tex']
        assert len({part['source_block'] for part in pieces})==4
        assert [span['source'] for span in compound.locator['inline_semantics'] if span['kind']=='literal']==[
            'tex first.tex','tex second.tex']
        assert doc.blocks[-1].role=='code'


@pytest.mark.parametrize('change',[
    ('Run either','Find the word <code>either</code>'),
    ('<p>or</p>','<p id="section-target">or</p>'),
    ('Keep the original.','depending on which edition you have.'),
    ('tex first.tex','tex first.tex\ntex third.tex'),
])
def test_command_group_requires_clear_clause_and_short_displays(tmp_path,change):
    main = tmp_path/'instructions.html'
    main.write_text(HTML.replace(*change),encoding='utf-8')
    doc = inspect_source(main)
    assert sum(block.role=='code' for block in doc.blocks)==3
    assert not any(block.locator.get('source_fragments') for block in doc.blocks)


@pytest.mark.parametrize('framed',[True,False])
def test_pdf_alternatives_require_layout_evidence_for_both_displays(tmp_path,framed):
    from reportlab.pdfgen.canvas import Canvas

    main = tmp_path/'page.pdf'
    writer = Canvas(str(main))
    writer.setFont('Times-Roman',12)
    writer.drawString(60,750,'Run either')
    writer.drawString(60,685,'or')
    writer.drawString(60,620,END)
    writer.setFont('Courier',12)
    writer.drawString(67,715,'tex first.tex')
    writer.drawString(67,650,'tex second.tex')
    writer.rect(60,709,120,20)
    if framed:
        writer.rect(60,644,120,20)
    writer.save()
    doc = inspect_source(main)
    if framed:
        book = plan(doc)
        assert not book['issues']
        assert [part['text'] for part in book['utterances']]==[PARAGRAPH+'.',END]
        parts = doc.blocks[0].locator['source_fragments']
        assert all(parts[index]['native_ranges'] for index in (1,3))
    else:
        assert not any(block.locator.get('source_fragments') for block in doc.blocks)


@pytest.mark.parametrize('format',['docx','odt'])
def test_office_literal_paragraph_style_inheritance_and_adjacency(tmp_path,format):
    path = tmp_path/('commands.'+format)
    entries = [('Run either',None),('tex first.tex','IndentedCode'),('or',None),
               ('tex second.tex','IndentedCode'),(END,None),('tex example.tex','IndentedCode'),
               ('tex another.tex','IndentedCode'),('',None),('tex separate.tex','IndentedCode')]
    if format=='docx':
        from test_word_intake import numbered, package

        styles = ('<w:style w:type="paragraph" w:styleId="BaseCode"><w:name w:val="Source Code"/></w:style>'
                  '<w:style w:type="paragraph" w:styleId="IndentedCode"><w:name w:val="Indented Code"/>'
                  '<w:basedOn w:val="BaseCode"/></w:style>')
        package(path,''.join(numbered(text,style=style) for text,style in entries),styles=styles)
    else:
        ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
              'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
              'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"')
        body = ''.join(f'<text:p text:style-name="{style or "Normal"}">{text}</text:p>' for text,style in entries)
        styles = ('<style:style style:name="BaseCode" style:display-name="Preformatted Text" style:family="paragraph"/>'
                  '<style:style style:name="IndentedCode" style:family="paragraph" style:parent-style-name="BaseCode"/>')
        with ZipFile(path,'w') as archive:
            archive.writestr('mimetype','application/vnd.oasis.opendocument.text')
            archive.writestr('content.xml',f'<office:document-content {ns}><office:body><office:text>'
                             f'{body}</office:text></office:body></office:document-content>')
            archive.writestr('styles.xml',f'<office:document-styles {ns}><office:styles>{styles}'
                             '</office:styles></office:document-styles>')
    doc = inspect_source(path)
    result = plan(doc)
    assert not result['issues']
    assert result['utterances'][0]['text']==PARAGRAPH+'.'
    code = [block.source for block in doc.blocks if block.role=='code']
    assert code==['tex example.tex\ntex another.tex','tex separate.tex']
    fragment = doc.blocks[0].locator['source_fragments'][1]
    assert fragment['literal_paragraph_style']['name']=='BaseCode'
    assert fragment['literal_paragraph_style']['source_file'].endswith('styles.xml')
