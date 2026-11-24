import io
import json
from zipfile import ZipFile

from PIL import Image, ImageDraw

from audiobook.intake import inspect_source


def package(path, parts):
    with ZipFile(path,'w') as archive:
        for name,data in parts.items():
            archive.writestr(name,data)


def img():
    with io.BytesIO() as stream:
        with Image.new('RGB',(600,400),'white') as page:
            ImageDraw.Draw(page).text((95,110),'An ordinary page of text.',fill='black')
            page.save(stream,format='PNG')
        return stream.getvalue()


def test_corrupt_zip_page_is_held_and_unknown_image_suffix_can_be_read(tmp_path,monkeypatch):
    from audiobook.core import Error
    from audiobook.pdf_visual import Observer

    def observe(self,open_image,_text,entry,_rect,**_):
        assert open_image().size==(600,400)
        return ([{'text':'Page one.', 'locator':{'rect':[10,10,100,30]}, 'issue':None}],
                {'status':'observed'})

    monkeypatch.setattr(Observer,'read_raster',observe)
    source = tmp_path/'pages.zip'
    package(source,{'page1.unknown':img(),'page10.jpg':b'broken','Readme.txt':'Page names are in order.'})
    document = inspect_source(source)
    assert [(p['member'],p['status']) for p in document.inventory]==[
        ('page1.unknown','observed'),('page10.jpg','failed')]
    assert document.blocks[-1].issue


def test_scan_repair_identity_is_the_same_before_and_after_capture(tmp_path):
    from audiobook.intake import inspect_saved
    from audiobook.source_bundle import capture

    page = tmp_path/'page-12.jpg'
    page.write_bytes(b'corrupt')
    before = inspect_source(page,offline=True)
    snapshot_dir = tmp_path/'state'
    snapshot_dir.mkdir()
    snapshot,source = capture(page,snapshot_dir,offline=True)
    after = inspect_saved(snapshot,source)
    assert before.blocks[0].id==after.blocks[0].id
    assert after.blocks[0].locator['member']=='page-12.jpg'


def test_mets_physical_order_and_alto_binding(tmp_path):
    for name in ('a.png','z.png'):
        (tmp_path/name).write_bytes(img())
    (tmp_path/'z.alto.xml').write_text('''<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"><Description>
      <sourceImageInformation><fileName>z.png</fileName></sourceImageInformation></Description>
      <Layout><Page WIDTH="600" HEIGHT="400"><PrintSpace><TextBlock ID="b"><TextLine>
      <String CONTENT="The archive." HPOS="10" VPOS="20" WIDTH="130" HEIGHT="30"/>
      </TextLine></TextBlock></PrintSpace></Page></Layout></alto>''')
    (tmp_path/'a.alto.xml').write_text((tmp_path/'z.alto.xml').read_text().replace('z.png','a.png').replace('The archive.','Another page.'))
    mets = tmp_path/'order.mets'
    mets.write_text('''<mets xmlns="http://www.loc.gov/METS/" xmlns:xlink="http://www.w3.org/1999/xlink">
      <fileSec><fileGrp USE="MASTER"><file ID="A" MIMETYPE="image/png"><FLocat xlink:href="a.png"/></file>
      <file ID="Z" MIMETYPE="image/png"><FLocat xlink:href="z.png"/></file></fileGrp>
      <fileGrp USE="OCR"><file ID="ar"><FLocat xlink:href="a.alto.xml"/></file><file ID="zr"><FLocat xlink:href="z.alto.xml"/></file></fileGrp></fileSec>
      <structMap TYPE="PHYSICAL"><div><div ORDER="2"><fptr FILEID="A"/><fptr FILEID="ar"/></div>
      <div ORDER="1"><fptr FILEID="Z"/><fptr FILEID="zr"/></div></div></structMap></mets>''')
    doc = inspect_source(mets,offline=True)
    assert [b.source for b in doc.blocks]==['The archive.','Another page.']
    assert [row['member'] for row in doc.inventory]==['z.png','a.png']
    archive = tmp_path/'mets.zip'
    package(archive,{'scans/'+path.name:path.read_bytes() for path in tmp_path.iterdir() if path.is_file()})
    packaged = inspect_source(archive,offline=True)
    assert [b.source for b in packaged.blocks]==['The archive.','Another page.']
    assert [row['member'] for row in packaged.inventory]==['scans/z.png','scans/a.png']


def test_zip_page_manifest_owns_sequence_repeats_and_separate_books(tmp_path,monkeypatch):
    import pytest
    from audiobook.core import Error
    from audiobook.pdf_visual import Observer

    monkeypatch.setattr(Observer,'read_raster',lambda _observer,_view,_words,entry,*_a,**_kw:(
        [{'text':'The page is readable.','locator':{'rect':[10,10,80,30]},'issue':None}],
        {'status':'observed'}))
    archive = tmp_path/'book.zip'
    manifest = {'schema':'hamr.page-sequence.v1','title':'Ordered book','pages':[
        {'path':'page10.png','occurrence':'opening','crop':[0,0,.5,1]},
        {'path':'page2.png'}, {'path':'page10.png','occurrence':'reprise'}]}
    members = {'scan/page2.png':img(),'scan/page10.png':img(),'scan/unselected.png':img(),
               'scan/pages.hamr.json':json.dumps(manifest),'readme.txt':'Unspoken metadata.'}
    package(archive,members)
    document = inspect_source(archive,offline=True)
    assert document.title=='Ordered book'
    assert [row['member'] for row in document.inventory]==['scan/page10.png','scan/page2.png','scan/page10.png']
    assert [row['archive_entry'] for row in document.inventory]==[2,1,2]
    assert document.inventory[0]['image_transform']['crop_pixels']==[0,0,300,400]
    assert [row['occurrence'] for row in document.inventory]==['opening','p000002','reprise']
    package(archive,{**members,'another.txt':'An unrelated novel.'})
    with pytest.raises(Error) as error:
        inspect_source(archive,offline=True)
    assert error.value.code=='source_selection'
    assert [row['id'] for row in error.value.candidates]==['pages','6']
    assert inspect_source(archive,entry='pages',offline=True).title=='Ordered book'


def test_archived_abbyy_original_geometry_uses_authored_transcription(tmp_path):
    archive = tmp_path/'proof.zip'
    sentence = 'The page stays in order.'
    letters = ''.join(
        f'<charParams l="{10+6*i}" t="12" r="{16+6*i}" b="27">{c}</charParams>'
        for i,c in enumerate(sentence))
    side = '<document xmlns="http://www.abbyy.com/FineReader_xml/FineReader10-schema-v1.xml">' \
           '<page width="600" height="400" originalCoords="true"><block blockType="Text">' \
           f'<text><par><line><formatting>{letters}</formatting></line></par></text></block></page></document>'
    package(archive,{'page.png':img(),'page.abbyy.xml':side})
    doc = inspect_source(archive,offline=True)
    assert [block.source for block in doc.blocks]==[sentence]
    assert doc.inventory[0]['sidecar']['format']=='abbyy'
    assert doc.inventory[0]['sidecar']['word_count']==5
    assert doc.inventory[0]['route']=='supplied_text'


def test_print_replica_uses_embedded_pdf_and_keeps_original_hash(tmp_path):
    import struct
    from audiobook.core import filehash
    from reportlab.pdfgen.canvas import Canvas

    buffer = io.BytesIO()
    page = Canvas(buffer)
    page.drawString(40,150,'The written evidence stays in order.')
    page.save()
    payload = buffer.getvalue()
    content = struct.pack('>4s4I',b'%MOP',1,1,20,len(payload))+payload
    header = bytearray(78)
    header[60:68] = b'BOOKMOBI'
    struct.pack_into('>H',header,76,4)
    first = 112
    palm = struct.pack('>HHIHHHH',1,0,len(content),2,4096,0,0)
    entries = (first, first+len(palm), first+len(palm)+90, first+len(palm)+len(content))
    records = b''.join(struct.pack('>II',offset,i+1) for i,offset in enumerate(entries))
    path = tmp_path/'edition.azw4'
    path.write_bytes(header+records+b'\0\0'+palm+content+b'Metadata')
    doc = inspect_source(path,offline=True)
    assert doc.format=='print-replica' and doc.source_sha256==filehash(path)
    assert doc.blocks[0].source=='The written evidence stays in order.'
    assert doc.blocks[0].locator['converter_intermediate'] is True


def test_archive_legacy_text_uses_explicit_encoding(tmp_path):
    import pytest
    from audiobook.core import Error

    archive = tmp_path/'letters.zip'
    package(archive,{'letter.nfo':'The archive’s account.'.encode('cp1252')})
    with pytest.raises(Error,match='cannot be decoded') as error:
        inspect_source(archive,offline=True)
    assert '--encoding' in error.value.action
    doc = inspect_source(archive,offline=True,encoding='windows-1252')
    assert doc.blocks[0].source=='The archive’s account.'
    assert doc.inventory[0]['encoding']=='cp1252'
    package(archive,{'letter.nfo':b'a\0b'})
    with pytest.raises(Error,match='embedded NUL'):
        inspect_source(archive,offline=True)


def test_local_iiif_uses_declared_sequence(tmp_path,monkeypatch):
    from audiobook.pdf_visual import Observer
    for name in ('a.png','z.png'):
        (tmp_path/name).write_bytes(img())
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'type':'Manifest','label':{'en':['Book']},'items':[
        {'id':label,'type':'Canvas','items':[{'items':[
            {'motivation':'painting','body':{'id':label+'.png','type':'Image'}}]}]} for label in ('z','a')]}))
    monkeypatch.setattr(Observer,'read_raster',lambda *_a,**_kw:([],{'status':'verified_blank'}))
    from audiobook.source_bundle import capture
    directory = tmp_path/'state'
    directory.mkdir()
    original,_ = capture(manifest,directory)
    from audiobook.archive_intake import ZipMembers
    from audiobook.scan_intake import bundle_index
    with ZipMembers(original) as archive:
        assert [p['path'] for p in bundle_index(archive)['pages']]==['z.png','a.png']


def test_image_only_epub_follows_spine_instead_of_filenames(tmp_path,monkeypatch):
    from audiobook.pdf_visual import Observer
    file = tmp_path/'fixed.epub'
    package(file,{'META-INF/container.xml':'<container><rootfile full-path="book.opf"/></container>',
        'book.opf':'<package><metadata><title>Scanned book</title><meta property="rendition:layout">pre-paginated</meta></metadata>'
        '<manifest><item id="end" href="a.xhtml"/><item id="start" href="z.xhtml"/></manifest>'
        '<spine><itemref idref="start"/><itemref idref="end"/></spine></package>',
        'a.xhtml':'<html><body><img src="a.png"/></body></html>',
        'z.xhtml':'<html><body><img src="z.png"/></body></html>','a.png':img(),'z.png':img()})
    monkeypatch.setattr(Observer,'read_raster',lambda _observer,_view,_tp,entry,*_a,**_k:(
        [{'text':'Scanned words.','locator':{'rect':[10,10,100,30]},'issue':None}], {'status':'observed'}))
    doc = inspect_source(file)
    assert doc.title=='Scanned book'
    assert [p['member'] for p in doc.inventory]==['z.png','a.png']


def test_workbook_uses_author_sheet_order_and_retains_saved_formula(tmp_path):
    file = tmp_path/'table.xlsx'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    package(file,{'xl/workbook.xml':f'<workbook xmlns:r="{rel}"><sheets><sheet name="East" r:id="r9"/>'
                           '<sheet name="West" state="hidden" r:id="r1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':f'<Relationships><Relationship Id="r9" Target="worksheets/sheet9.xml" Type="{rel}/worksheet"/>'
                                  f'<Relationship Id="r1" Target="worksheets/sheet1.xml" Type="{rel}/worksheet"/></Relationships>',
        'xl/worksheets/sheet1.xml':'<worksheet><row><c r="A1"><v>999</v></c></row></worksheet>',
        'xl/worksheets/sheet9.xml':'<worksheet><row r="1"><c r="A1" t="inlineStr"><is><t>Oak</t></is></c>'
                                 '<c r="B1"><f>2+3</f><v>5</v></c></row></worksheet>'})
    doc = inspect_source(file)
    assert [b.source for b in doc.blocks]==['East','Oak | 5']
    assert doc.inventory[0]['cached_formulas']==[{'cell':'B1','formula':'2+3','cached_value':'5'}]
    assert doc.inventory[1]['status']=='hidden by source'


def test_amiga_reader_follows_next_and_retains_action_without_running_it(tmp_path):
    file = tmp_path/'guide.guide'
    file.write_text('@database Proof\n@node MAIN "Opening"\n@next last\nStart.\n@endnode\n'
                    '@node middle "Middle"\nThe middle.\n@endnode\n'
                    '@node last "Last"\n@next middle\nRead @{"here" system "echo example"}.\n@endnode\n')
    doc = inspect_source(file)
    assert [row['node'] for row in doc.inventory]==['main','last','middle']
    assert {'label':'here','command':['system','echo example']} in doc.inventory[1]['logical_links']
    assert doc.blocks[-1].issue  # The default Browse order cycles from Middle to Last.


def test_tcr_uses_resident_dictionary(tmp_path):
    path = tmp_path/'compressed.tcr'
    dictionary = {0:b'The ',1:b'book ',2:b'is ready.'}
    path.write_bytes(b'!!8-Bit!!'+b''.join(bytes([len(dictionary.get(i,b''))])+dictionary.get(i,b'')
                                          for i in range(256))+bytes([0,1,2]))
    doc = inspect_source(path)
    assert doc.blocks[0].source=='The book is ready.'
