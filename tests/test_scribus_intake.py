from gzip import compress
from io import BytesIO
from xml.sax.saxutils import quoteattr
from zipfile import ZipFile

from PIL import Image
import pytest

from audiobook.core import Error
from audiobook.intake import inspect_saved, inspect_source
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe, original_name


def xml_document(content,version='1.7.0'):
    return f'<SCRIBUSUTF8NEW Version="{version}"><DOCUMENT TITLE="Harbor Watch">\n{content}\n</DOCUMENT></SCRIBUSUTF8NEW>'


def text_frame(ident,text,*,page=0,next_id='-1',previous='-1',layer='0',tag='PAGEOBJECT',y=0):
    identity = f' ItemID="{ident}"' if ident is not None else ''
    return (f'<{tag} PTYPE="4" OwnPage="{page}" YPOS="{y}" LAYER="{layer}"'
            f' NEXTITEM="{next_id}" BACKITEM="{previous}"{identity}>{text}</{tag}>')


def manuscript(alt='The lamp shines by the water.'):
    note = quoteattr('<StoryText><ITEXT CH="The keeper returned at dawn."/><trail/></StoryText>')
    chapter = '<StoryText><ITEXT CH="Harbor Watch"/><trail PARENT="Heading 1"/></StoryText>'
    story = '''<StoryText><DefaultStyle/>
      <ITEXT CH="The "/><ITEXT CH="lantern" CPARENT="Emphasis"/><ITEXT CH=" stood by the gate."/>
      <MARK label="watch" type="4"/><para/>
      <ITEXT CH="A small boat drifted past."/><trail/>
    </StoryText>'''
    image = f'<PAGEOBJECT ItemID="303" OwnPage="1" PTYPE="2" PFILE="../art/harbor.png" AltText="{alt}"/>'
    return xml_document(f'''
      <PAGE NUM="0"/><PAGE NUM="1"/><PAGE NUM="2"/>
      <LAYERS NUMMER="2" SICHTBAR="0"/>
      <CHARSTYLE CNAME="Emphasis" FONT="Liberation Serif Italic"/>
      <STYLE NAME="Heading 1"/>
      <Notes><Note Master="watch" Text={note}/></Notes>
      {text_frame('201','',page=2,previous='200')}
      {text_frame('200',story,page=0,next_id='201',y=50)}
      {text_frame('101',chapter)}
      {text_frame('202','<StoryText><ITEXT CH="Template text."/></StoryText>',tag='MASTEROBJECT')}
      {text_frame('203','<StoryText><ITEXT CH="Hidden draft."/></StoryText>',layer='2')}
      {image}
    ''')


def make_zip(path,parts):
    with ZipFile(path,'w') as archive:
        for filename,value in parts.items():
            archive.writestr(filename,value)
    return path


def test_scribus_exact_scope_through_folder_zip_and_compressed_snapshot(tmp_path):
    root = tmp_path/'Book'
    target = root/'chapters'/'Harbor.sla.gz'
    target.parent.mkdir(parents=True)
    raw = compress(manuscript().encode(),mtime=0)
    target.write_bytes(raw)
    artwork = root/'art'/'harbor.png'
    artwork.parent.mkdir()
    image = BytesIO()
    Image.new('RGB',(2,2),'white').save(image,format='PNG')
    artwork.write_bytes(image.getvalue())
    archive = make_zip(tmp_path/'Book.zip',{'Publication/chapters/Harbor.sla.gz':raw,
                                          'Publication/art/harbor.png':image.getvalue()})
    first = None
    for source in (target,root,archive):
        doc = inspect_source(source,offline=True)
        assert doc.format=='scribus'
        result = plan(doc)
        assert not result['issues']
        said = [item['text'] for item in result['utterances']]
        assert said[0]=='Harbor Watch'
        assert 'The lantern stood by the gate. Note. The keeper returned at dawn. End note.' in ' '.join(said)
        assert 'The lamp shines by the water.' in ' '.join(said)
        assert 'Template text' not in ' '.join(said) and 'Hidden draft' not in ' '.join(said)
        assert ' '.join(said).count('A small boat drifted past.')==1
        assert next(block for block in doc.blocks if 'lantern' in block.source).locator['scribus_frames']==['200','201']
        spans = [span for block in doc.blocks for span in block.locator.get('inline_semantics',())]
        assert any(span['kind']=='emphasis' and span['source']=='lantern' for span in spans)
        if source!=target:
            assert any(link['kind']=='source_resource' for block in doc.blocks for link in block.links)
        first = said if first is None else first
        assert first==said
    directory = tmp_path/'state'
    directory.mkdir()
    captured,route = capture(root,directory,offline=True)
    assert route.kind=='zip-member'
    target.write_text('Changed draft')
    with ZipFile(captured) as stored:
        assert stored.read('chapters/Harbor.sla.gz')==raw and stored.read('art/harbor.png')==image.getvalue()
    assert plan(inspect_saved(captured,route))['issues']==[]
    assert original_name('source.sla.gz',{'route':'scribus','kind':'file'})=='original.sla.gz'


def test_scribus_layout_bounds_table_and_thread_errors_are_scoped(tmp_path):
    source = tmp_path/'Story.sla'
    # Positional predecessor values index the saved page object list.
    text = '<ITEXT CH="The lock closed."/><para/><ITEXT CH="The harbor fell quiet."/>'
    unicode = ('<StoryText><ITEXT CH="A"/><ITEXT Unicode="9"/><ITEXT CH="B. "/>'
               '<ITEXT Unicode="55357"/><ITEXT Unicode="56538"/></StoryText>')
    source.write_text(xml_document(f'''
       <PAGE NUM="0"/><PAGE NUM="1"/>
       {text_frame(None,text,page=1,next_id='1')}
       {text_frame(None,unicode,page=0,previous='0')}
       <PAGEOBJECT PTYPE="16" OwnPage="1" YPOS="14" Rows="2" Columns="2" CellAreas="0 0 1 2">
         <TableData>
           <Cell Row="0" Column="0"><StoryText><ITEXT CH="Harbor inventory"/></StoryText></Cell>
           <Cell Row="1" Column="0"><StoryText><ITEXT CH="Lamp"/></StoryText></Cell>
           <Cell Row="1" Column="1"><StoryText><ITEXT CH="Four"/></StoryText></Cell>
         </TableData></PAGEOBJECT>
       {text_frame(None,'<ITEXT CH="The chairs were empty."/>',page=1,y=20,next_id='7')}
    ''',version='1.3.5svn'))
    doc = inspect_source(source,offline=True)
    head = next(block for block in doc.blocks if 'lock closed' in block.source)
    assert head.locator['scribus_frames']==['0','1']
    assert [block.source for block in doc.blocks[:3]]==['The lock closed.','The harbor fell quiet.','A\tB. 📚']
    assert any(block.role=='table' and 'Lamp' in block.source and 'Four' in block.source for block in doc.blocks)
    assert any('NEXTITEM=7' in issue['reason'] for issue in plan(doc)['issues'])


def test_scribus_cycles_are_not_silently_replayed_and_false_gzip_is_reported(tmp_path):
    file = tmp_path/'Cycle.sla'
    file.write_text(xml_document(text_frame('100','<ITEXT CH="One."/>',next_id='200',previous='200')+
                                 text_frame('200','<ITEXT CH="Two."/>',next_id='100',previous='100')))
    content = inspect_source(file,offline=True)
    result = plan(content)
    assert [item.source for item in content.blocks if item.role=='body']==['One.','Two.']
    assert any('circles back' in item['reason'] for item in result['issues'])
    broken = tmp_path/'Damaged.sla.gz'
    broken.write_bytes(b'\x1f\x8b\x08\x00')
    assert describe(broken).route=='scribus'
    with pytest.raises(Error) as reason:
        inspect_source(broken,offline=True)
    assert reason.value.code=='source_scribus_compression'
