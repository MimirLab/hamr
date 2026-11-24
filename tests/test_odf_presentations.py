import base64
from io import BytesIO
import json
from zipfile import ZipFile

from PIL import Image
import pytest

from audiobook.core import Error
from audiobook.intake import extract_location, inspect_source
from audiobook.planner import plan


PREFIX = 'urn:oasis:names:tc:opendocument:xmlns:'
MEDIA = 'application/vnd.oasis.opendocument.presentation'
NAMESPACES = ' '.join(
    f'xmlns:{prefix}="{PREFIX}{suffix}:1.0"' for prefix,suffix in (
        ('office','office'),('draw','drawing'),('text','text'),('style','style'),('presentation','presentation'),
        ('table','table'),('svg','svg-compatible'),('fo','xsl-fo-compatible'),('manifest','manifest')))
NAMESPACES += ' xmlns:xlink="http://www.w3.org/1999/xlink"'


def document(content,*,flat=False,styles=''):
    root = 'document' if flat else 'document-content'
    return (f'<office:{root} {NAMESPACES} office:mimetype="{MEDIA}">{styles}'
            f'<office:body><office:presentation>{content}</office:presentation></office:body></office:{root}>')


def frame(text,kind=''):
    return (f'<draw:frame presentation:class="{kind}"><draw:text-box><text:p>{text}</text:p>'
            '</draw:text-box></draw:frame>')


def fixture():
    table = ('<draw:frame><table:table>'
             '<table:table-header-rows><table:table-row><table:table-cell><text:p>Reading</text:p></table:table-cell>'
             '</table:table-row></table:table-header-rows>'
             '<table:table-row><table:table-cell><text:p>Clear water</text:p></table:table-cell></table:table-row>'
             '</table:table><draw:image xlink:href="Thumbnails/thumbnail.png"/></draw:frame>')
    slide = ('<draw:page draw:name="Watch">'+frame('The evening watch','title')+table+
             '<draw:g draw:name="Log"><draw:g draw:name="Gate">'+frame('The keeper took a reading.')+
             '<draw:frame><draw:text-box><text:p>The original label.</text:p></draw:text-box>'
             '<draw:text-box><text:p>The alternate label.</text:p></draw:text-box></draw:frame>'+
             '</draw:g></draw:g><presentation:notes><draw:page-thumbnail/>'+
             frame('The keeper took a reading.','notes')+frame('A note in the ledger.','notes')+
             frame('Fake page number','page-number')+'</presentation:notes></draw:page>')
    hidden = '<draw:page draw:style-name="invisible">'+frame('Hidden slide','title')+'</draw:page>'
    styles = ('<office:automatic-styles><style:style style:family="drawing-page" style:name="parent">'
              '<style:drawing-page-properties presentation:visibility="hidden"/></style:style>'
              '<style:style style:family="drawing-page" style:name="invisible" style:parent-style-name="parent"/>'
              '</office:automatic-styles>')
    return slide+hidden,styles


def ordinary_speech(doc,**kwargs):
    return [item['text'] for item in plan(doc,**kwargs)['utterances']]


def test_media_identification_and_saved_structures(tmp_path):
    slide,styles = fixture()
    deck = tmp_path/'Deck.zip'
    flat = tmp_path/'Deck.xml'
    xml = document(slide,styles=styles)
    flat.write_text(document(slide,styles=styles,flat=True))
    with ZipFile(deck,'w') as archive:
        archive.writestr('mimetype',MEDIA)
        archive.writestr('content.xml',xml)
        archive.writestr('Thumbnails/thumbnail.png',b'IMAGE FALLBACK')
        archive.writestr('word/document.xml',b'Incidental Office file')
    doc = inspect_source(deck)
    assert inspect_source(flat).format=='odp'
    assert [row['status'] for row in doc.inventory]==['represented','hidden by source']
    result = ordinary_speech(doc)
    assert result==ordinary_speech(inspect_source(flat))
    assert result.count('The keeper took a reading.')==1
    assert 'The original label.' in result and 'The alternate label.' not in result
    assert result[-1]=='A note in the ledger.'
    assert 'A note in the ledger.' not in ordinary_speech(doc,policy={'notes':'companion'})
    assert not any('Hidden' in word or 'thumbnail' in word or 'Fake' in word for word in result)
    paragraph = next(item for item in doc.blocks if item.source=='The keeper took a reading.')
    assert paragraph.locator['shape_groups']==['Log','Gate']


def test_archive_recovers_an_exact_inline_raster_from_its_xml(tmp_path,monkeypatch):
    from audiobook.pdf_visual import Observer

    catalog = ('<office:automatic-styles><style:page-layout style:name="geometry">'
               '<style:page-layout-properties fo:page-width="2cm" fo:page-height="2cm"/>'
               '</style:page-layout></office:automatic-styles><office:master-styles>'
               '<style:master-page style:name="master" style:page-layout-name="geometry"/></office:master-styles>')
    with BytesIO() as stream,Image.new('RGB',(20,20),'white') as image:
        image.save(stream,format='PNG')
        raw = stream.getvalue()
    encoded = base64.b64encode(raw).decode()
    slide = ('<draw:page draw:master-page-name="master"><draw:frame svg:width="2cm" svg:height="2cm">'
             '<draw:image><office:binary-data>'+encoded+'</office:binary-data></draw:image>'
             '<svg:desc>image.png</svg:desc></draw:frame></draw:page>')
    edition = tmp_path/'Archive.zip'
    with ZipFile(edition,'w') as archive:
        archive.writestr('Deck/Source.xml',document(slide,styles=catalog,flat=True))
    def read(self,load,original,entry,bounds,**kwargs):
        return [{'text':'A written note.','issue':None,'locator':{'rect':bounds}}],{'status':'observed'}
    monkeypatch.setattr(Observer,'read_raster',read)
    doc = inspect_source(edition)
    assert ordinary_speech(doc)==['Slide 1','A written note.']
    record, = doc.metadata['source_resources']
    assert record['member']=='Deck/Source.xml'
    assert doc.inventory[0]['visual_observations'][0]['status']=='observed'
    (tmp_path/'companion.json').write_text(json.dumps({
        'original_source':edition.name,'source_kind':'zip','source_sha256':doc.source_sha256,
        'source_locations':{record['location_id']:record}
    }))
    result = tmp_path/'recovered.png'
    extract_location(tmp_path,record['location_id'],result)
    assert result.read_bytes()==raw


def test_encrypted_presentation_part_is_reported_before_xml_parsing(tmp_path):
    path = tmp_path/'Protected.odp'
    with ZipFile(path,'w') as archive:
        archive.writestr('mimetype',MEDIA)
        archive.writestr('content.xml',b'encrypted bytes')
        archive.writestr('META-INF/manifest.xml',
            f'<manifest:manifest {NAMESPACES}><manifest:file-entry manifest:full-path="content.xml">'
            '<manifest:encryption-data/></manifest:file-entry></manifest:manifest>')
    with pytest.raises(Error,match='content.xml is encrypted'):
        inspect_source(path)
