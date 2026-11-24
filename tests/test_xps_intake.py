from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
import reportlab
from PIL import Image, ImageDraw, ImageFont

from audiobook.intake import inspect_source
from audiobook.planner import plan
from audiobook.xps_intake import NAMESPACES


RELS = 'http://schemas.openxmlformats.org/package/2006/relationships'
TYPES = 'http://schemas.openxmlformats.org/package/2006/content-types'


def write_book(path, *, openxps=False, story=False, missing_page=False):
    ns = NAMESPACES[bool(openxps)]
    family = 'openxps' if openxps else 'ms-package.xps'
    font = Path(reportlab.__file__).parent/'fonts'/'Vera.ttf'

    def glyph(text, x, y, *, name='', indices=''):
        label = f' Name="{name}"' if name else ''
        return (f'<Glyphs FontUri="/Fonts/regular.ttf" FontRenderingEmSize="16" OriginX="{x}" OriginY="{y}" '
                f'Fill="#000000" UnicodeString="{text}" Indices="{indices}"{label}/>')

    def page(body):
        return f'<FixedPage xmlns="{ns}" Width="720" Height="500" xml:lang="en-US">{body}</FixedPage>'

    def rel(kind,target):
        return f'<Relationships xmlns="{RELS}"><Relationship Id="r1" Type="{ns}/{kind}" Target="{target}"/></Relationships>'

    sequence = f'<FixedDocumentSequence xmlns="{ns}"><DocumentReference Source="../Book/Part.fdoc"/></FixedDocumentSequence>'
    pages = f'<FixedDocument xmlns="{ns}"><PageContent Source="Pages/10.fpage"/><PageContent Source="Pages/2.fpage"/></FixedDocument>'
    first = glyph('F',44,58,indices=',57')+glyph('irst Edition',53.12,58)
    first += glyph('The lantern at the gate was still lit.',44,111,name='opening')
    # Invisible original glyphs are retained with the page, outside the reading.
    first += '<Canvas Opacity="0">'+glyph('Do not read this overlay.',44,111)+'</Canvas>'
    first += glyph('The watch ended at dawn.',44,142,name='end')
    second = glyph('A quiet voice said, “Keep the lamp burning.”',44,65,name='voice')
    second += glyph('The keeper nodded.',44,110,name='keeper')
    parts = {
        '[Content_Types].xml':f'<Types xmlns="{TYPES}"><Default Extension="fdseq" ContentType="application/vnd.{family}-fixeddocumentsequence+xml"/></Types>',
        '_rels/.rels':rel('fixedrepresentation','/Root/Sequence.fdseq'),
        'Root/Sequence.fdseq':sequence,
        'Book/Part.fdoc':pages,
        'Fonts/regular.ttf':font.read_bytes(),
        'Book/Pages/10.fpage':page(first),
    }
    if not missing_page:
        parts['Book/Pages/2.fpage'] = page(second)
    if story:
        parts['Book/Pages/_rels/10.fpage.rels'] = rel('storyfragments','../Stories/Intro.xml')
        parts['Book/Stories/Intro.xml'] = f'''
            <StoryFragments xmlns="{ns}/documentstructure">
              <StoryFragment FragmentType="Content">
                <ParagraphStructure><NamedElement NameReference="end"/></ParagraphStructure>
                <TableStructure><TableRowGroupStructure>
                  <TableRowStructure>
                    <TableCellStructure RowSpan="2"><ParagraphStructure><NamedElement NameReference="opening"/></ParagraphStructure></TableCellStructure>
                    <TableCellStructure/>
                  </TableRowStructure>
                  <TableRowStructure><TableCellStructure/></TableRowStructure>
                </TableRowGroupStructure></TableStructure>
              </StoryFragment>
            </StoryFragments>'''
    with ZipFile(path,'w') as archive:
        for member in reversed(parts):
            archive.writestr(member,parts[member])


@pytest.mark.parametrize('suffix',('.xps','.oxps'))
def test_xps_reads_declared_order_and_preserves_exact_inner_member(tmp_path,suffix):
    original = tmp_path/('watch'+suffix)
    write_book(original,openxps=suffix=='.oxps')
    wrapper = tmp_path/'reading.zip'
    with ZipFile(wrapper,'w') as archive:
        archive.write(original,'Shelf/watch'+suffix)
    parsed = inspect_source(original)
    packed = inspect_source(wrapper)
    assert [row['member'] for row in parsed.inventory]==['Book/Pages/10.fpage','Book/Pages/2.fpage']
    assert [b.source for b in parsed.blocks]==[b.source for b in packed.blocks]
    assert parsed.blocks[0].source=='First Edition'
    assert len(parsed.blocks[0].locator['xps_glyphs'])==2
    assert all('overlay' not in b.source for b in parsed.blocks)
    assert packed.blocks[0].locator['inner_member']['member']=='Book/Pages/10.fpage'
    assert not plan(packed)['issues']


def test_page_stories_override_paint_order_and_supply_shared_table_cells(tmp_path):
    path = tmp_path/'watch.oxps'
    write_book(path,openxps=True,story=True)
    parsed = inspect_source(path)
    assert parsed.blocks[0].source=='The watch ended at dawn.'
    table = parsed.blocks[1].locator['table_grid']
    assert table['cells'][0]['rows']==2
    assert table['cells'][2]['column']==1
    assert table['cells'][0]['parts'][0]['source']=='The lantern at the gate was still lit.'
    assert parsed.blocks[2].source=='First Edition'
    assert not plan(parsed)['issues']


def test_missing_xps_page_keeps_earlier_content_and_names_the_original_reference(tmp_path):
    path = tmp_path/'watch.xps'
    write_book(path,missing_page=True)
    parsed = inspect_source(path)
    assert parsed.inventory[1]['status']=='unavailable'
    assert parsed.inventory[1]['reference']=='Pages/2.fpage'
    result = plan(parsed)
    issue, = result['issues']
    assert issue['location']['member']=='Book/Part.fdoc'
    assert issue['location']['page']==2
    assert 'First Edition' in ' '.join(u['text'] for u in result['utterances'])


def test_positioned_ordinal_keeps_its_suffix_and_returns_to_the_prose_line():
    from audiobook.xps_page import GlyphRun, assemble

    def glyph(index,text,x,y,em,width):
        return GlyphRun(index,text,text,(x,y-em,x+width,y),(x,y),width,4,em,None)

    runs = [glyph(1,'The 1',50,60,16,40),glyph(2,'st',90.1,53,10,7),glyph(3,' edition.',97.2,60,16,56)]
    text,observed,issue = next(assemble(runs))
    assert text=='The 1st edition.'
    assert observed==runs and issue is None


RASTER_READING = ('The keeper returned at sunrise.', 'A green lamp marked the shore.')


def write_raster_book(path):
    ns = NAMESPACES[1]
    parts = {
        '[Content_Types].xml':f'<Types xmlns="{TYPES}"><Default Extension="fdseq" ContentType="application/vnd.openxps-fixeddocumentsequence+xml"/></Types>',
        '_rels/.rels':f'<Relationships xmlns="{RELS}"><Relationship Id="r1" Type="{ns}/fixedrepresentation" Target="/Book.fdseq"/></Relationships>',
        'Book.fdseq':f'<FixedDocumentSequence xmlns="{ns}"><DocumentReference Source="/Z/Part.fdoc"/><DocumentReference Source="/A/Part.fdoc"/></FixedDocumentSequence>',
    }
    font = ImageFont.truetype(str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf'),58)
    for directory,sentence in zip(('Z','A'),RASTER_READING):
        parts[directory+'/Part.fdoc'] = f'<FixedDocument xmlns="{ns}"><PageContent Source="Page.fpage"/></FixedDocument>'
        parts[directory+'/Page.fpage'] = f'''
            <FixedPage xmlns="{ns}" Width="720" Height="480" xml:lang="en-US">
                <Path Data="M 0,0 L 720,0 L 720,480 L 0,480 Z">
                    <Path.Fill>
                        <ImageBrush ImageSource="/{directory}/Scan.png"
                            Viewbox="0,0,1500,1000" ViewboxUnits="Absolute"
                            Viewport="0,0,720,480" ViewportUnits="Absolute"/>
                    </Path.Fill>
                </Path>
            </FixedPage>'''
        with Image.new('RGB',(1500,1000),'white') as image,BytesIO() as data:
            ImageDraw.Draw(image).text((100,180),sentence,font=font,fill='black')
            image.save(data,format='PNG')
            parts[directory+'/Scan.png'] = data.getvalue()
    with ZipFile(path,'w') as archive:
        for member,value in sorted(parts.items()):
            archive.writestr(member,value)


def test_raster_openxps_follows_declared_document_order(tmp_path):
    from audiobook.xps_visual import available

    if not available():
        pytest.skip('Resident XPS renderer needs a compatible Linux host with Bubblewrap')
    source = tmp_path/'watch.oxps'
    write_raster_book(source)
    result = inspect_source(source,tmp_path/'cache')
    if result.inventory[0].get('visual_failure')=='source_helper_failed' and any(
            'namespace' in b.issue for b in result.blocks if b.issue):
        pytest.skip('Bubblewrap cannot create a namespace on this host')
    assert [page['member'] for page in result.inventory]==['Z/Page.fpage','A/Page.fpage']
    assert [page['route'] for page in result.inventory]==['visual_ocr','visual_ocr']
    assert [block.source for block in result.blocks]==list(RASTER_READING)
    assert not plan(result)['issues']


def test_missing_renderer_names_each_original_page(tmp_path,monkeypatch):
    from audiobook import xps_visual

    monkeypatch.setattr(xps_visual,'available',lambda:False)
    source = tmp_path/'watch.oxps'
    write_raster_book(source)
    result = inspect_source(source)
    assert [page['visual_failure'] for page in result.inventory]==['source_helper_unavailable']*2
    assert [issue['location']['member'] for issue in plan(result)['issues']]==['Z/Page.fpage','A/Page.fpage']
