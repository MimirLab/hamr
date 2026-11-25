from zipfile import ZipFile

from audiobook.intake import inspect_source
from audiobook.planner import plan
from audiobook.word_stories import MC, SHAPE, VML, WORD


NS = (f'xmlns:w="{WORD}" xmlns:mc="{MC[1:-1]}" xmlns:s="{SHAPE}" xmlns:v="{VML}" '
      'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" xmlns:unknown="urn:unknown"')


def package(path,body,notes='',numbering='',styles=''):
    with ZipFile(path,'w') as book:
        book.writestr('word/document.xml',f'<w:document {NS}><w:body>{body}</w:body></w:document>')
        if notes:
            book.writestr('word/footnotes.xml',f'<w:footnotes {NS}>{notes}</w:footnotes>')
        if numbering or styles:
            relationship = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            entry = ''
            if numbering:
                entry += f'<Relationship Id="numbering" Type="{relationship}numbering" Target="lists/numbering.xml"/>'
                book.writestr('word/lists/numbering.xml',f'<w:numbering {NS}>{numbering}</w:numbering>')
            if styles:
                entry += f'<Relationship Id="styles" Type="{relationship}styles" Target="styles.xml"/>'
                book.writestr('word/styles.xml',f'<w:styles {NS}>{styles}</w:styles>')
            book.writestr('word/_rels/document.xml.rels',
                          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                          +entry+'</Relationships>')
    return path


def paragraph(text):
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def numbered(text,num=None,level=None,style=None):
    style_xml = f'<w:pStyle w:val="{style}"/>' if style else ''
    numbering = f'<w:numId w:val="{num}"/>' if num is not None else ''
    if level is not None:
        numbering = f'<w:ilvl w:val="{level}"/>'+numbering
    props = style_xml+(f'<w:numPr>{numbering}</w:numPr>' if numbering else '')
    return f'<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'


def test_saved_word_instances_styles_and_restarts_feed_common_list_reading(tmp_path):
    definition = (
        '<w:abstractNum w:abstractNumId="0">'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>'
        '<w:lvl w:ilvl="1"><w:start w:val="4"/><w:numFmt w:val="upperRoman"/>'
        '<w:pStyle w:val="Indented"/><w:lvlText w:val="%1.%2."/></w:lvl>'
        '</w:abstractNum><w:num w:numId="2"><w:abstractNumId w:val="0"/>'
        '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="3"/></w:lvlOverride></w:num>'
        '<w:num w:numId="3"><w:abstractNumId w:val="0"/></w:num>')
    styles = ('<w:style w:type="paragraph" w:styleId="Indented"><w:pPr>'
              '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr>'
              '</w:pPr></w:style><w:style w:type="paragraph" w:styleId="Child">'
              '<w:basedOn w:val="Indented"/></w:style>')
    body = (numbered('3. Open the gate.',2)+numbered('Check the hinge.',style='Child')
            +paragraph('Watch the road.')+numbered('Inspect the latch.',2,1)
            +numbered('Check the lamp.',2)+numbered('Check the cover.',style='Child')
            +numbered('Close the gate.',3)+numbered('Park the cart.',0,style='Child'))
    source = package(tmp_path/'counter.docx',body,numbering=definition,styles=styles)
    wrapped = tmp_path/'counter.zip'
    with ZipFile(wrapped,'w') as zipfile:
        zipfile.write(source,'manual/Counter.docx')
    for path in (source,wrapped):
        result = plan(inspect_source(path,offline=True))
        assert not result['issues']
        assert [unit['text'] for unit in result['utterances']]==[
            'Number three, Open the gate.','Number three dot four, Check the hinge.',
            'Watch the road.','Number three dot five, Inspect the latch.',
            'Number four, Check the lamp.','Number four dot four, Check the cover.',
            'Number one, Close the gate.','Park the cart.']
        labels = [a for row in result['decisions'] for a in row.get('source_numbering',())]
        assert [label['authored_marker'] for label in labels]==['3.','3.IV.','3.V.','4.','4.IV.','1.']
        assert labels[0]['source']=='3. ' and labels[1]['ordinal_values']==[3,4]
        assert labels[1]['marker_location']['source_file']=='word/lists/numbering.xml'
        assert labels[0]['marker_location']['override_path'].endswith('/w:lvlOverride')


def test_word_bullet_never_gains_speech_and_unsupported_marker_holds_one_item(tmp_path):
    definition = ('<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
                  '<w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl></w:abstractNum>'
                  '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0">'
                  '<w:numFmt w:val="hebrew1"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
                  '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
                  '<w:num w:numId="7"><w:abstractNumId w:val="1"/></w:num>')
    path = package(tmp_path/'markers.docx',numbered('Watch the road.',1)+numbered('Inspect the latch.',7),
                   numbering=definition)
    doc = inspect_source(path)
    result = plan(doc)
    assert len(result['issues'])==1 and result['issues'][0]['block']==doc.blocks[1].id
    assert [unit['text'] for unit in result['utterances']]==['Watch the road.']


def drawing(content):
    return f'<w:drawing><s:wsp>{content}</s:wsp></w:drawing>'


def box(content,ident=None):
    owner = '' if ident is None else f' id="{ident}"'
    return drawing(f'<s:txbx{owner}><w:txbxContent>{content}</w:txbxContent></s:txbx>')


def vml(content,ident='',style=''):
    return (f'<w:pict><v:shape id="{ident}"><v:textbox style="{style}">'
            f'<w:txbxContent>{content}</w:txbxContent></v:textbox></v:shape></w:pict>')


def test_editable_stories_follow_word_compatibility_and_preserve_anchor_text(tmp_path):
    wrong = box(paragraph('Do not narrate this branch.'))
    fallback = vml(paragraph('The inset contains a warning.'))
    nested = (f'<mc:AlternateContent><mc:Choice Requires="s unknown">{wrong}</mc:Choice>'
              f'<mc:Fallback>{fallback}</mc:Fallback></mc:AlternateContent>')
    selected = box(paragraph('The side note explains the lock.')+f'<w:p><w:r>{nested}</w:r></w:p>')
    alternative = (f'<mc:AlternateContent><mc:Choice Requires="s">{selected}</mc:Choice>'
                   f'<mc:Choice Requires="s">{wrong}</mc:Choice><mc:Fallback>{wrong}</mc:Fallback></mc:AlternateContent>')
    native = package(tmp_path/'edited.docx',
                     '<w:p><w:r><w:t xml:space="preserve">The keeper </w:t>'
                     f'{alternative}<w:t>opened the gate.</w:t></w:r></w:p>'
                     +paragraph('The ferry left at dawn.'))
    archive = tmp_path/'book.zip'
    with ZipFile(archive,'w') as outer:
        outer.write(native,'edition/edited.docx')
    for path in (native,archive):
        doc = inspect_source(path,offline=True)
        assert [block.source for block in doc.blocks]==[
            'The keeper opened the gate.','The side note explains the lock.',
            'The inset contains a warning.','The ferry left at dawn.']
        assert not plan(doc)['issues']
        assert '/mc:Choice[1]/' in doc.blocks[1].locator['text_box']
        assert '/mc:Fallback/' in doc.blocks[2].locator['text_box']
        assert doc.blocks[1].locator['text_box_anchor']=='/w:document/w:body/w:p[1]'


def test_linked_frames_use_story_order_once(tmp_path):
    continuation = drawing('<s:linkedTxbx id="9" seq="1"/>')
    body = (f'<w:p><w:r>{continuation}</w:r></w:p>'
            +f'<w:p><w:r>{box(paragraph("This story has one owner."),ident=9)}</w:r></w:p>'
            +f'<w:p><w:r>{vml(paragraph("This is the second legacy frame."),ident="second")}</w:r></w:p>'
            +paragraph('A paragraph between the legacy anchors.')
            +f'<w:p><w:r>{vml(paragraph("This is the first legacy frame."),ident="first",style="mso-next-textbox:#second")}</w:r></w:p>')
    doc = inspect_source(package(tmp_path/'linked.docx',body))
    assert [block.source for block in doc.blocks]==[
        'This story has one owner.','A paragraph between the legacy anchors.',
        'This is the first legacy frame.','This is the second legacy frame.']
    assert not plan(doc)['issues']


def test_unresolved_frames_and_missing_branches_are_reported(tmp_path):
    hidden = f'<w:r><w:rPr><w:vanish/></w:rPr>{box(paragraph("Hidden owner."),ident=9)}</w:r>'
    continuation = drawing('<s:linkedTxbx id="9" seq="1"/>')
    missing = '<mc:AlternateContent><mc:Choice Requires="unknown"><w:r/></mc:Choice></mc:AlternateContent>'
    doc = inspect_source(package(tmp_path/'unresolved.docx',f'<w:p>{hidden}<w:r>{continuation}</w:r></w:p>'
                                 +f'<w:p>{missing}</w:p>'+paragraph('The chapter continues.')))
    result = plan(doc)
    assert [unit['text'] for unit in result['utterances']]==['The chapter continues.']
    assert len(result['issues'])==2
    assert 'no unique story owner' in result['issues'][0]['reason']
    assert 'no readable compatibility branch' in result['issues'][1]['reason']


def test_word_text_box_table_uses_common_note_and_math_readings(tmp_path):
    formula = ('<m:oMath><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e>'
               '<m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:oMath>')
    table = ('<w:tbl><w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc>'+paragraph('Value')+'</w:tc></w:tr>'
             f'<w:tr><w:tc><w:p>{formula}<w:r><w:footnoteReference w:id="2"/></w:r></w:p>'
             '</w:tc></w:tr></w:tbl>')
    note = '<w:footnote w:id="2">'+paragraph('The source labels this a measurement.')+'</w:footnote>'
    doc = inspect_source(package(tmp_path/'table.docx',f'<w:p><w:r>{box(table)}</w:r></w:p>',note))
    result = plan(doc)
    assert not result['issues']
    item, definition = doc.blocks
    assert item.locator['rows']==[['Value'],['x2']]
    assert '/w:txbxContent/' in item.locator['xml_path']
    assert item.links[0]['destination']=='source.html#'+definition.id
    assert definition.locator['source_file']=='word/footnotes.xml'
    assert ' '.join(unit['text'] for unit in result['utterances'])==(
        'Table. Value: x to the power of 2. Note. The source labels this a measurement. End note.')
