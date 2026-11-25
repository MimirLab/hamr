from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import inspect_saved, inspect_source
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe


MIF = """<MIFFile 2019> # FrameMaker
<VariableFormats <VariableFormat <VariableName `Material'> <VariableDef `oak'>>>
<Page <PageType RightMasterPage> <PageNum `Right'> <TextRect <ID 1>>>
<Page <PageType BodyPage> <PageNum `1'> <TextRect <ID 2>>
 <Frame <TextLine <String `At the harbor'>>>>
<Page <PageType BodyPage> <PageNum `2'> <TextRect <ID 3>>>
<AFrames <Frame <ID 7> <TextRect <ID 4>>
  <ImportObject <ImportObFileDI `<u\\><c\\>art<c\\>window.png'>
   <AltText `A lantern hangs in the window.'>
=PNG
& Graphic data is opaque: <String `fictional'>
=EndInset
  >>>
<Tbls <Tbl <TblID 12>
 <TblH <Row <Cell <CellContent <Para <ParaLine <String `Place'>>>>>>>
 <TblBody <Row <Cell <CellContent <Para
  <ParaLine <String `The north '>><ParaLine <String `pier.'>>
 >>>>>>>
<TextFlow <TFTag `A'> <Para <ParaLine <TextRectID 1> <String `Master furniture.'>>>>
<TextFlow <Notes <FNote <ID 22> <Para <ParaLine <String `The keeper waited.'>>>>>
 <TFTag `A'>
 <Para <PgfTag `Head 1'> <ParaLine <TextRectID 2><String `Harbor Watch'>>>
 <Para <PgfTag `Body'>
  <ParaLine <String `The keeper opened the '> <Font <FWeight `Bold'>>>
  <ParaLine <TextRectID 3> <Variable <VariableName `Material'>> <Font>
   <String ` gate.'> <FNote 22> <AFrame 7> <ATbl 12>>>
 <Para <ParaLine <String `The boat rocked in the wind.'> <Char HardReturn>
   <String `We returned at dawn.'>>>>
<TextFlow <TFTag `A'> <Para <ParaLine <TextRectID 4>
 <String `This text belongs to the anchored frame.'>>>>
"""


def test_saved_flows_enter_shared_narration_across_file_folder_and_zip(tmp_path):
    root = tmp_path/'Publication'
    manuscript = root/'chapters'/'Harbor.mif'
    manuscript.parent.mkdir(parents=True)
    raw = MIF.encode()
    manuscript.write_bytes(raw)
    art = root/'art'/'window.png'
    art.parent.mkdir()
    art.write_bytes(b'\x89PNG\r\n\x1a\nopaque image bytes')
    archive = tmp_path/'publication.zip'
    with ZipFile(archive,'w') as package:
        package.write(manuscript,'Book/chapters/Harbor.mif')
        package.write(art,'Book/art/window.png')

    previous = None
    for source in (manuscript,root,archive):
        doc = inspect_source(source,offline=True)
        assert doc.format=='mif'
        assert doc.metadata['mif_version']=='2019'
        rows = [(block.role,block.source) for block in doc.blocks]
        assert ('heading','Harbor Watch') in rows
        assert ('body','The keeper opened the oak gate.') in rows
        assert ('body','The boat rocked in the wind.\nWe returned at dawn.') in rows
        assert ('body','This text belongs to the anchored frame.') in rows
        assert rows.index(('body','This text belongs to the anchored frame.')) < rows.index(('table','Place\nThe north pier.'))
        assert ('note','The keeper waited.') in rows
        assert ('body','Master furniture.') not in rows
        result = plan(doc)
        assert not result['issues']
        assert 'Note. The keeper waited. End note.' in ' '.join(item['text'] for item in result['utterances'])
        if previous is not None:
            assert previous == rows
            assert any(link['kind']=='source_resource' for block in doc.blocks for link in block.links)
        previous = rows
        block = next(block for block in doc.blocks if block.source.startswith('The keeper opened'))
        assert block.locator['lines'][0]<block.locator['lines'][1]
        assert block.locator['mif_statement']=='Para'

    state = tmp_path/'state'
    state.mkdir()
    snapshot,route = capture(root,state,offline=True)
    manuscript.write_text('changed draft')
    with ZipFile(snapshot) as originals:
        assert originals.read('chapters/Harbor.mif') == raw
        assert originals.read('art/window.png') == art.read_bytes()
    assert not plan(inspect_saved(snapshot,route))['issues']


def test_mif_strings_wrapping_and_scoped_reference_issue(tmp_path):
    path = tmp_path/'text.mif'
    path.write_text(r"""<MIFFile 9.00>
<TextFlow <Para
 <ParaLine <String `Here: <, \>, \Qmark\q, \\, and #. '>>
 <ParaLine <String `This is still one paragraph.'> <FNote 123>>>
 <Para <ParaLine <String `The next paragraph is complete.'>>>>
""")
    doc = inspect_source(path)
    assert doc.blocks[0].source == "Here: <, >, `mark', \\, and #. This is still one paragraph."
    assert doc.blocks[1].source == 'The next paragraph is complete.'
    assert [item['reason'] for item in plan(doc)['issues']] == [
        'This FrameMaker footnote has no unique saved definition.',
    ]


def test_mif_routing_refuses_mapinfo_and_reports_incomplete_saved_source(tmp_path):
    geo = tmp_path/'Map.mif'
    geo.write_text('Version 300\nCharset "WindowsLatin1"\nColumns 0\nData\n')
    with pytest.raises(Error) as error:
        describe(geo)
    assert error.value.code == 'source_mif_signature'
    with ZipFile(tmp_path/'maps.zip','w') as package:
        package.write(geo,geo.name)
    with pytest.raises(Error) as error:
        describe(tmp_path/'maps.zip')
    assert error.value.code == 'unsupported_format'

    source = tmp_path/'Book.mif'
    source.write_text("<MIFFile 9.00>\n<TextFlow <Para <String `missing close'>>")
    with pytest.raises(Error) as error:
        inspect_source(source)
    assert error.value.code == 'source_mif_syntax'
    assert 'line 2' in error.value.message
    source.write_text("<MIFFile 7.00>\n<Para <ParaLine <String `Text.'>>>")
    with pytest.raises(Error) as error:
        inspect_source(source)
    assert error.value.code == 'source_mif_version'
