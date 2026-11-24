from zipfile import ZipFile

from audiobook.intake import inspect_source
from audiobook.planner import plan
from audiobook.word_stories import MC, SHAPE, VML, WORD


NS = (f'xmlns:w="{WORD}" xmlns:mc="{MC[1:-1]}" xmlns:s="{SHAPE}" xmlns:v="{VML}" '
      'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" xmlns:unknown="urn:unknown"')


def package(path,body,notes=''):
    with ZipFile(path,'w') as book:
        book.writestr('word/document.xml',f'<w:document {NS}><w:body>{body}</w:body></w:document>')
        if notes:
            book.writestr('word/footnotes.xml',f'<w:footnotes {NS}>{notes}</w:footnotes>')
    return path


def paragraph(text):
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


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
