from io import BytesIO
from zipfile import ZipFile

from PIL import Image
import pytest

from audiobook.core import Error
from audiobook.intake import inspect_saved, inspect_source
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe

NS = 'http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging'


def package_xml(kind,text):
    return f'<p:{kind} xmlns:p="{NS}">{text}</p:{kind}>'


def story(ident,content):
    return package_xml('Story',f'<Story Self="{ident}">{content}</Story>')


def paragraph(text,style='Body'):
    return f'<ParagraphStyleRange AppliedParagraphStyle="{style}"><CharacterStyleRange>{text}</CharacterStyleRange></ParagraphStyleRange>'


def archive(path,parts,prefix=''):
    with ZipFile(path,'w') as output:
        for name,value in parts.items():
            output.writestr(prefix+name,value)
    return path


def edition():
    image = BytesIO()
    Image.new('RGB',(2,2),'white').save(image,format='PNG')
    manifest = f'''<Document xmlns:p="{NS}" DOMVersion="15.0" Name="Harbor Watch">
      <Layer Self="private" Visible="false"/>
      <p:Spread src="Spreads/early.xml"/><p:Spread src="Spreads/late.xml"/>
      <p:MasterSpread src="MasterSpreads/template.xml"/>
      <p:Styles src="Resources/Styles.xml"/>
      <p:Story src="Stories/late.xml"/><p:Story src="Stories/read.xml"/>
      <p:Story src="Stories/heading.xml"/><p:Story src="Stories/path.xml"/>
      <p:Story src="Stories/master.xml"/><p:Story src="Stories/hidden.xml"/>
      <Article ArticleExportStatus="true"><ArticleMember ItemRef="heading"/>
        <ArticleMember ItemRef="second-frame"/><ArticleMember ItemRef="caption"/></Article>
    </Document>'''
    early = '''
      <Page Name="1" GeometricBounds="0 0 100 100"/>
      <TextFrame Self="first-frame" ParentStory="reader" NextTextFrame="second-frame"/>
      <TextFrame Self="heading" ParentStory="opening"/>
      <TextFrame Self="hidden" ParentStory="private" ItemLayer="private"/>
      <Polygon><Properties><PathGeometry><PathPointType Anchor="10 50"/>
        <PathPointType Anchor="70 50"/></PathGeometry></Properties>
        <TextPath Self="path" ParentStory="path"/></Polygon>
      <Rectangle Self="caption"><ObjectExportOption CustomAltText="A lamp stands by the harbor."/>
        <Image><Link LinkResourceURI="file:Links/harbor.png"/></Image></Rectangle>'''
    late = '''
      <Page Name="2" GeometricBounds="0 0 100 100"/>
      <TextFrame Self="second-frame" ParentStory="reader" PreviousTextFrame="first-frame"/>
      <TextFrame Self="finish" ParentStory="closing"/>'''
    styles = '''
      <RootParagraphStyleGroup><ParagraphStyle Self="TitleId"><StyleExportTagMaps>
        <StyleExportTagMap ExportTag="h1"/></StyleExportTagMaps></ParagraphStyle></RootParagraphStyleGroup>
      <RootCharacterStyleGroup><CharacterStyle Self="EmphasisId" FontStyle="Italic"/></RootCharacterStyleGroup>'''
    reading = '''<ParagraphStyleRange AppliedParagraphStyle="Body">
       <CharacterStyleRange><Content>The gate </Content></CharacterStyleRange>
       <CharacterStyleRange AppliedCharacterStyle="EmphasisId"><Content>opened</Content></CharacterStyleRange>
       <CharacterStyleRange><Content> at dawn.</Content>
         <Footnote><ParagraphStyleRange><CharacterStyleRange><Content>The keeper was there.</Content></CharacterStyleRange></ParagraphStyleRange></Footnote><Br/>
       </CharacterStyleRange></ParagraphStyleRange>'''
    return {'mimetype':'application/vnd.adobe.indesign-idml-package',
            'META-INF/container.xml':'<container><rootfiles><rootfile full-path="designmap.xml"/></rootfiles></container>',
            'designmap.xml':manifest,'Spreads/late.xml':package_xml('Spread',f'<Spread>{late}</Spread>'),
            'Spreads/early.xml':package_xml('Spread',f'<Spread>{early}</Spread>'),
            'MasterSpreads/template.xml':package_xml('MasterSpread','<MasterSpread><TextFrame ParentStory="master"/></MasterSpread>'),
            'Stories/late.xml':story('closing',paragraph('<Content>At sunset the harbor went quiet.</Content>')),
            'Stories/read.xml':story('reader',reading),
            'Stories/heading.xml':story('opening',paragraph('<Content>The Harbor Watch</Content>','TitleId')),
            'Stories/path.xml':story('path',paragraph('<Content>Keep to the marked channel.</Content>')),
            'Stories/master.xml':story('master',paragraph('<Content>Master page words.</Content>')),
            'Stories/hidden.xml':story('private',paragraph('<Content>Hidden draft words.</Content>')),
            'Resources/Styles.xml':package_xml('Styles',styles),'Links/harbor.png':image.getvalue()}


def test_idml_reads_article_threads_and_keeps_physical_source(tmp_path):
    parts = edition()
    original = archive(tmp_path/'Harbor.idml',parts)
    loose = tmp_path/'Layout'
    for filename,data in parts.items():
        destination = loose/filename
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data if isinstance(data,bytes) else data.encode())
    raw_zip = archive(tmp_path/'Backup.zip',parts,'Published/')
    packed_zip = archive(tmp_path/'Nested.zip',{'Manuscripts/Harbor.idml':original.read_bytes()})
    for path in (original,loose,raw_zip,packed_zip):
        assert describe(path).route=='idml'
        document = inspect_source(path,offline=True)
        planned = plan(document)
        assert not planned['issues']
        reading = ' '.join(unit['text'] for unit in planned['utterances'])
        assert reading.startswith('The Harbor Watch The gate opened at dawn.')
        assert 'Note. The keeper was there. End note.' in reading
        assert 'A lamp stands by the harbor.' in reading
        assert 'Keep to the marked channel.' in reading
        assert reading.endswith('At sunset the harbor went quiet.')
        assert reading.count('The gate opened')==1
        assert 'Hidden draft' not in reading and 'Master page' not in reading
        gate, = [block for block in document.blocks if block.source=='The gate opened at dawn.']
        assert gate.locator['member'].endswith('Harbor.idml' if path==packed_zip else 'Stories/read.xml')
        assert [place['frame'] for place in gate.locator['idml_frames']]==['first-frame','second-frame']
        assert any(span['kind']=='emphasis' and span['source']=='opened' for span in gate.locator['inline_semantics'])
        assert any(link['kind']=='source_resource' for block in document.blocks for link in block.links)
    save = tmp_path/'state'
    save.mkdir()
    snapshot,source = capture(loose,save,offline=True)
    (loose/'Stories/read.xml').write_text('Changed original')
    assert plan(inspect_saved(snapshot,source))['issues']==[]
    with ZipFile(snapshot) as copied:
        assert copied.read('Stories/read.xml')==parts['Stories/read.xml'].encode()


def test_icml_breaks_cross_style_runs_and_ignores_deleted_text(tmp_path):
    file = tmp_path/'Watch.icml'
    file.write_text('''<Document DOMVersion="8.0"><Story Self="s">
      <ParagraphStyleRange AppliedParagraphStyle="ParagraphStyle/Title"><CharacterStyleRange>
        <Content>The Watch</Content><Br/></CharacterStyleRange></ParagraphStyleRange>
      <ParagraphStyleRange><CharacterStyleRange><Content>The bell rang at </Content></CharacterStyleRange></ParagraphStyleRange>
      <ParagraphStyleRange><CharacterStyleRange><Content>dawn.</Content><Br/>
        <Change ChangeType="DeletedText"><Content>The draft was longer. </Content></Change>
        <Change ChangeType="InsertedText"><Content>It rang again at sunset.</Content></Change>
      </CharacterStyleRange></ParagraphStyleRange></Story></Document>''')
    outer = archive(tmp_path/'Watch.zip',{'story/Watch.icml':file.read_bytes()})
    for source in (file,outer):
        doc = inspect_source(source,offline=True)
        assert doc.format=='icml'
        assert [(block.role,block.source) for block in doc.blocks]==[
            ('heading','The Watch'),('body','The bell rang at dawn.'),('body','It rang again at sunset.')]
        assert all('xml_path' in item.locator and item.locator['source_file'].endswith('Watch.icml')
                   for item in doc.blocks)


def test_icml_reads_saved_variables_and_authored_table_headers(tmp_path):
    file = tmp_path/'Inventory.icml'
    file.write_text('''<Document DOMVersion="8.0"><Story Self="s">
      <ParagraphStyleRange BulletsAndNumberingListType="BulletList">
        <CharacterStyleRange><Content>Bring the lamp.</Content><Br/><Content>Close the gate.</Content><Br/></CharacterStyleRange>
      </ParagraphStyleRange>
      <Table HeaderRowCount="1">
        <Cell Name="0:0"><Content>Tool</Content></Cell>
        <Cell Name="1:0"><Content>Count</Content></Cell>
        <Cell Name="0:1"><Content>Lanterns</Content></Cell>
        <Cell Name="1:1"><TextVariableInstance ResultText="Four"/></Cell>
      </Table>
      <HiddenText><ParagraphStyleRange><Content>Private draft.</Content></ParagraphStyleRange></HiddenText>
    </Story></Document>''')
    doc = inspect_source(file,offline=True)
    assert [b.role for b in doc.blocks]==['body','body','table','hidden']
    text = ' '.join(request['text'] for request in plan(doc)['utterances'])
    assert 'Tool: Lanterns; Count: Four.' in text
    assert 'Private draft' not in text


def test_icml_writer_controls_share_heading_notes_and_list_numbering(tmp_path):
    file = tmp_path/'Writer.icml'
    file.write_text('''<Document DOMVersion="8.0">
      <ParagraphStyle Self="ParagraphStyle/NumList" BulletsAndNumberingListType="NumberedList"/>
      <Story Self="s">
        <ParagraphStyleRange AppliedParagraphStyle="ParagraphStyle/Header1"><Content>The Watch</Content><Br/></ParagraphStyleRange>
        <ParagraphStyleRange><Content>The gate closed.</Content><Footnote>
          <ParagraphStyleRange><Content><?ACE 4?></Content></ParagraphStyleRange>
          <ParagraphStyleRange><Content>The keeper held the key.</Content></ParagraphStyleRange>
        </Footnote><Br/></ParagraphStyleRange>
        <ParagraphStyleRange AppliedParagraphStyle="ParagraphStyle/NumList" NumberingStartAt="3" NumberingContinue="false">
          <Content>Open the gate.</Content><Br/><Content>Watch for the boat.</Content></ParagraphStyleRange>
      </Story></Document>''')
    doc = inspect_source(file,offline=True)
    result = plan(doc)
    assert doc.blocks[0].role=='heading'
    assert not result['issues']
    assert [item['text'] for item in result['utterances']]==[
        'The Watch','The gate closed. Note. The keeper held the key. End note.',
        'Number three, Open the gate.','Number four, Watch for the boat.']


def test_idml_missing_declared_story_is_scoped_and_unrelated_file_remains_selectable(tmp_path):
    parts = edition()
    del parts['Stories/late.xml']
    mixed = archive(tmp_path/'Mixed.zip',{**{'Book/'+name:data for name,data in parts.items()},
                                        'other.txt':'Another publication.'})
    with pytest.raises(Error) as selection:
        describe(mixed)
    assert selection.value.code=='source_selection'
    candidates = selection.value.candidates
    assert {item['name'] for item in candidates}=={'Harbor Watch','other.txt'}
    selected, = [item['id'] for item in candidates if item['name']=='Harbor Watch']
    doc = inspect_source(mixed,entry=selected,offline=True)
    problems = [issue['reason'] for issue in plan(doc)['issues']]
    assert any('Stories/late.xml' in reason for reason in problems)
    assert any('story closing' in reason for reason in problems)


def test_idml_floating_story_and_figure_keep_the_anchor_sentence_intact(tmp_path):
    parts = edition()
    parts['Stories/read.xml'] = story('reader',paragraph('''
        <Content>The tide </Content><TextFrame Self="sidebar-frame" ParentStory="sidebar">
          <Properties><AnchoredObjectSetting AnchoredPosition="Anchored"/></Properties></TextFrame>
        <Content>turned </Content><Rectangle><Properties><AnchoredObjectSetting AnchoredPosition="Anchored"/></Properties>
          <ObjectExportOption CustomAltText="A diagram shows the harbor."/><Image/></Rectangle>
        <Content>at dawn.</Content><Br/>'''))
    parts['Stories/sidebar.xml'] = story('sidebar',paragraph('<Content>The lookout was waiting.</Content>'))
    parts['designmap.xml'] = parts['designmap.xml'].replace('</Document>','<p:Story src="Stories/sidebar.xml"/></Document>')
    doc = inspect_source(archive(tmp_path/'Example.idml',parts),offline=True)
    values = [item.source for item in doc.blocks]
    assert values.index('The tide turned at dawn.') < values.index('The lookout was waiting.')
    narrated = ' '.join(item['text'] for item in plan(doc)['utterances'])
    assert 'The tide turned at dawn.' in narrated and 'A diagram shows the harbor.' in narrated
    sidebar, = [item for item in doc.blocks if item.source=='The lookout was waiting.']
    assert sidebar.locator['member']=='Stories/sidebar.xml'
