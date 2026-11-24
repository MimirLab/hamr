from io import BytesIO
from zipfile import ZipFile

import json
from PIL import Image

from audiobook.intake import extract_location, inspect_source
from audiobook.planner import plan


P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
TYPES = 'http://schemas.openxmlformats.org/package/2006/content-types'


def rels(entries):
    return f'<Relationships xmlns="{PKG}">'+''.join(
        f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{target}"/>' for rid,kind,target in entries
    )+'</Relationships>'


def page(root,content,**attrs):
    fields = ''.join(f' {key}="{value}"' for key,value in attrs.items())
    return (f'<p:{root} xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}" xmlns:c="{C}"{fields}>'
            f'<p:cSld><p:spTree>{content}</p:spTree></p:cSld></p:{root}>')


def paragraph(text):
    return f'<a:p><a:r><a:t>{text}</a:t></a:r></a:p>'


def shape(number,text,kind=None):
    marker = f'<p:ph type="{kind}"/>' if kind else ''
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{number}"/><p:nvPr>{marker}</p:nvPr></p:nvSpPr>'
            f'<p:txBody>{paragraph(text)}</p:txBody></p:sp>')


def deck(path,*,invalid_chart=False):
    table = (
        '<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tblPr firstRow="1" firstCol="1"/>'
        '<a:tblGrid><a:gridCol/><a:gridCol/></a:tblGrid>'
        '<a:tr><a:tc><a:txBody>'+paragraph('Site')+'</a:txBody></a:tc><a:tc><a:txBody>'
        +paragraph('Condition')+'</a:txBody></a:tc></a:tr>'
        '<a:tr><a:tc rowSpan="2"><a:txBody>'+paragraph('The gate')+'</a:txBody></a:tc><a:tc><a:txBody>'
        +paragraph('Clear sky')+'</a:txBody></a:tc></a:tr>'
        '<a:tr><a:tc vMerge="1"/><a:tc><a:txBody>'+paragraph('Calm water')
        +'</a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>')
    group = ('<p:grpSp><p:nvGrpSpPr><p:cNvPr id="8"/></p:nvGrpSpPr>'
             +shape(9,'The keeper checked the gate.')+
             '<p:grpSp><p:nvGrpSpPr><p:cNvPr id="10"/></p:nvGrpSpPr>'
             +shape(11,'The lamp stayed lit.')+'</p:grpSp></p:grpSp>')
    chart_frame = '<p:graphicFrame><a:graphic><a:graphicData><c:chart r:id="chart"/></a:graphicData></a:graphic></p:graphicFrame>'
    numeric_cache = '<c:formatCode>0%</c:formatCode><c:ptCount val="2"/><c:pt idx="0"><c:v>0.25</c:v></c:pt>'
    if invalid_chart:
        numeric_cache += '<c:pt idx="0"><c:v>0.3</c:v></c:pt>'
    chart = (f'<c:chartSpace xmlns:c="{C}"><c:chart><c:plotArea><c:barChart><c:ser><c:order val="0"/>'
             '<c:tx><c:v>Capacity</c:v></c:tx><c:cat><c:strLit><c:ptCount val="2"/>'
             '<c:pt idx="0"><c:v>Morning</c:v></c:pt><c:pt idx="1"><c:v>Evening</c:v></c:pt></c:strLit></c:cat>'
             '<c:val><c:numLit>'+numeric_cache+'</c:numLit></c:val></c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>')
    files = {
        '[Content_Types].xml':f'<Types xmlns="{TYPES}"><Override PartName="/Deck/Presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/></Types>',
        '_rels/.rels':rels([('main','officeDocument','Deck/Presentation.xml')]),
        'Deck/Presentation.xml':f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst><p:sldId r:id="last"/><p:sldId r:id="hidden"/><p:sldId r:id="first"/></p:sldIdLst></p:presentation>',
        'Deck/_rels/Presentation.xml.rels':rels([
            ('first','slide','Slides/Page1.xml'),('hidden','slide','Slides/Page2.xml'),('last','slide','Slides/Page10.xml')
        ]),
        'Deck/Slides/Page1.xml':page('sld',shape(2,'The evening watch','title')+table+chart_frame),
        'Deck/Slides/_rels/Page1.xml.rels':rels([('chart','chart','../Graphs/Chart.xml')]),
        'Deck/Graphs/Chart.xml':chart,
        'Deck/Slides/Page2.xml':page('sld',shape(2,'Hidden draft words','title'),show='0'),
        'Deck/Slides/Page10.xml':page('sld',shape(2,'The morning watch','title')+group),
        'Deck/Slides/_rels/Page10.xml.rels':rels([('notes','notesSlide','../Notes/Notes.xml')]),
        'Deck/Notes/Notes.xml':page('notes',shape(2,'Do not speak header text','hdr')
                                           +shape(3,'The keeper checked the gate.','body')
                                           +shape(4,'The crew kept a written record.','body')),
    }
    with ZipFile(path,'w') as archive:
        for member,value in sorted(files.items()):
            archive.writestr(member,value)
    return files


def requests(document,**kwargs):
    return [row['text'] for row in plan(document,**kwargs)['utterances']]


def test_slides_keep_authored_order_merges_and_notes_policy(tmp_path):
    path = tmp_path/'Watch.pptx'
    deck(path)
    doc = inspect_source(path)
    assert [row['member'].rsplit('/',1)[-1] for row in doc.inventory]==['Page10.xml','Page2.xml','Page1.xml']
    assert not plan(doc)['issues']
    text = ' '.join(requests(doc))
    assert text.index('The morning watch')<text.index('The crew kept a written record')<text.index('The evening watch')
    assert text.count('The keeper checked the gate.')==1
    assert 'draft' not in text and 'header text' not in text
    without_notes = ' '.join(requests(doc,policy={'notes':'companion'}))
    assert 'written record' not in without_notes
    lamp = next(block for block in doc.blocks if block.source=='The lamp stayed lit.')
    assert lamp.locator['shape_groups']==['8','10']
    grid,chart = [item.locator['table_grid'] for item in doc.blocks if item.role=='table']
    merged = next(item for item in grid['cells'] if item['rows']==2)
    assert len(merged['continuations'])==1
    assert chart['cells'][3]['location']['cached_value']=='0.25'
    assert chart['cells'][3]['parts'][0]['source']=='25%'
    assert not any(cell['row']==2 and cell['column']==1 for cell in chart['cells'])


def test_archive_and_folder_reading_recover_the_exact_notes_part(tmp_path):
    original = tmp_path/'Watch.pptx'
    parts = deck(original)
    outer,flat,folder = tmp_path/'Outer.zip',tmp_path/'Flat.zip',tmp_path/'Folder'
    with ZipFile(outer,'w') as archive:
        archive.write(original,'Course/Watch.pptx')
    with ZipFile(flat,'w') as archive:
        for name,value in parts.items():
            archive.writestr('Course/Deck copy/'+name,value)
    with ZipFile(original) as archive:
        archive.extractall(folder)
    direct,packed,expanded,unpacked = [inspect_source(path) for path in (original,outer,flat,folder)]
    assert requests(direct)==requests(packed)==requests(expanded)==requests(unpacked)
    notes = next(item.locator for item in packed.blocks if item.source=='The crew kept a written record.')
    assert notes['inner_member']['member']=='Deck/Notes/Notes.xml'
    assert notes['member']=='Course/Watch.pptx'
    (tmp_path/'companion.json').write_text(json.dumps({
        'original_source':outer.name,'source_kind':'zip','source_sha256':packed.source_sha256,
        'source_locations':{notes['location_id']:notes}
    }))
    result = tmp_path/'recovered.xml'
    extract_location(tmp_path,notes['location_id'],result)
    assert result.read_bytes()==parts['Deck/Notes/Notes.xml'].encode()


def test_conflicting_chart_values_hold_their_exact_part(tmp_path):
    path = tmp_path/'Watch.pptx'
    deck(path,invalid_chart=True)
    result = plan(inspect_source(path))
    issue, = result['issues']
    assert 'same category position' in issue['reason']
    assert issue['location']['member']=='Deck/Graphs/Chart.xml'
    assert issue['location']['slide']==3
    assert 'written record' in ' '.join(row['text'] for row in result['utterances'])


def test_ocr_cache_receipt_preserves_stable_source_block_identity(tmp_path,monkeypatch):
    from audiobook.pdf_visual import Observer

    path = tmp_path/'Watch.pptx'
    parts = deck(path)
    picture = ('<p:pic><p:nvPicPr><p:cNvPr id="2" descr="image.png"/></p:nvPicPr>'
               '<p:blipFill><a:blip r:embed="image"/></p:blipFill>'
               '<p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="20" cy="20"/></a:xfrm></p:spPr></p:pic>')
    parts['Deck/Slides/Page10.xml'] = page('sld',picture)
    parts['Deck/Slides/_rels/Page10.xml.rels'] = rels([('image','image','../Media/page.png')])
    parts['Deck/Presentation.xml'] = parts['Deck/Presentation.xml'].replace(
        '</p:presentation>','<p:sldSz cx="20" cy="20"/></p:presentation>')
    with BytesIO() as stream,Image.new('RGB',(20,20),'white') as image:
        image.save(stream,format='PNG')
        parts['Deck/Media/page.png'] = stream.getvalue()
    with ZipFile(path,'w') as archive:
        for member,value in sorted(parts.items()):
            archive.writestr(member,value)

    reused = iter((False,True))
    def read(observer,load,original,entry,bounds,**kwargs):
        receipt = {'status':'observed','raw_OCR_reused':next(reused)}
        return [{'text':'The light stayed on.','issue':None,'locator':{'rect':bounds}}],receipt

    monkeypatch.setattr(Observer,'read_raster',read)
    cold,warm = (inspect_source(path) for _ in range(2))
    assert cold.inventory[0]['visual_observations'][0]['raw_OCR_reused'] is False
    assert warm.inventory[0]['visual_observations'][0]['raw_OCR_reused'] is True
    first,second = plan(cold),plan(warm)
    assert first['utterances']==second['utterances']
    assert first['plan_sha256']==second['plan_sha256']
