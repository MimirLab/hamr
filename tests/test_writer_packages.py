from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import inspect_source
from audiobook.planner import plan


MODERN = 'urn:oasis:names:tc:opendocument:xmlns:'
TEXT_MIME = 'application/vnd.oasis.opendocument.text-template'
LEGACY = 'http://openoffice.org/2000/'
LEGACY_MIME = 'application/vnd.sun.xml.writer'


def wrap(path,parts):
    with ZipFile(path,'w') as archive:
        for name,contents in parts.items():
            archive.writestr(name,contents)
    return path


def test_writer_package_scope_owns_thumbnail_and_returns_exact_parts(tmp_path):
    content = f'''<office:document-content xmlns:office="{MODERN}office:1.0"
      xmlns:text="{MODERN}text:1.0"><office:body><office:text>
      <text:p>The keeper opened the gate.</text:p></office:text></office:body></office:document-content>'''
    parts = {'mimetype':TEXT_MIME,'content.xml':content,
             'styles.xml':f'<office:document-styles xmlns:office="{MODERN}office:1.0"/>',
             'Thumbnails/thumbnail.png':b'\x89PNG\r\n\x1a\n'}
    # An embedded package sits below the manuscript, even if its marker sorts earlier.
    parts.update({'Embedded/mimetype':'application/vnd.oasis.opendocument.presentation','Embedded/content.xml':'<embed/>'})
    loose = tmp_path/'loose'
    loose.mkdir()
    for name,data in parts.items():
        target = loose/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data.encode() if isinstance(data,str) else data)
    root = wrap(tmp_path/'root.zip',parts)
    nested = wrap(tmp_path/'nested.zip',{'Work/'+name:data for name,data in parts.items()})
    for source,prefix in ((loose,''),(root,''),(nested,'Work/')):
        doc = inspect_source(source,offline=True)
        assert [item['text'] for item in plan(doc)['utterances']]==['The keeper opened the gate.']
        assert doc.blocks[0].locator['member']==prefix+'content.xml'
        assert {item['member'] for item in doc.metadata['source_resources']}=={prefix+name for name in parts}
    mixed = wrap(tmp_path/'mixed.zip',{'Preview/scan.png':parts['Thumbnails/thumbnail.png'],
                                     **{'Manuscript/'+name:data for name,data in parts.items()}})
    with pytest.raises(Error) as failed:
        inspect_source(mixed,offline=True)
    assert failed.value.code=='source_selection'
    pages,writer = failed.value.candidates
    assert pages=={'id':'pages','name':'Image sequence (1 files)'}
    assert writer['name']=='Manuscript'
    assert inspect_source(mixed,offline=True,entry=writer['id']).blocks[0].source=='The keeper opened the gate.'


def test_openoffice_one_projects_lists_notes_and_frames_at_original_locations(tmp_path):
    ns = ' '.join(f'xmlns:{prefix}="{LEGACY+kind}"' for prefix,kind in
                  (('office','office'),('text','text'),('style','style'),('draw','drawing'),('table','table')))
    styles = f'''<office:document-styles {ns} xmlns:fo="http://www.w3.org/1999/XSL/Format">
      <office:styles><style:style style:family="text" style:name="Marked">
        <style:properties fo:font-weight="bold"/></style:style>
      <text:list-style style:name="Numbers"><text:list-level-style-number text:level="1" style:num-format="1"/>
      </text:list-style><text:list-style style:name="Bullets"><text:list-level-style-bullet text:level="1"/>
      </text:list-style></office:styles></office:document-styles>'''
    document = f'''<office:document-content {ns}><office:body>
      <text:h text:level="1">First watch</text:h>
      <text:ordered-list text:style-name="Numbers"><text:list-item><text:p>The watch began.</text:p></text:list-item>
      </text:ordered-list>
      <text:unordered-list text:style-name="Bullets"><text:list-item><text:p>The gate was clear.</text:p></text:list-item>
      </text:unordered-list>
      <text:ordered-list text:style-name="Numbers" text:continue-numbering="true">
        <text:list-item><text:p>They checked the gate.</text:p></text:list-item></text:ordered-list>
      <text:p>They <text:span text:style-name="Marked">waited</text:span> for a signal.<text:tab-stop/>
      <text:footnote><text:footnote-citation>1</text:footnote-citation><text:footnote-body>
      <text:p>The bell was broken.</text:p></text:footnote-body></text:footnote></text:p>
      <text:p><draw:text-box draw:name="message" text:anchor-type="as-char"><text:p>The wind rose.</text:p>
      </draw:text-box></text:p></office:body></office:document-content>'''
    members = {'mimetype':LEGACY_MIME,'styles.xml':styles,'content.xml':document}
    source = wrap(tmp_path/'archive.sxw',members)
    wrapped = wrap(tmp_path/'loose.zip',{'book/archive.sxw':source.read_bytes()})
    for path in (source,wrapped):
        doc = inspect_source(path,offline=True)
        assert doc.metadata['writer_dialect']=='OpenOffice.org 1.x XML'
        assert [block.source for block in doc.blocks]==[
            'First watch','The watch began.','The gate was clear.','They checked the gate.',
            'They waited for a signal.','The wind rose.']
        assert '/text:ordered-list[1]/text:list-item/' in doc.blocks[1].locator['element']
        assert doc.blocks[1].locator['ordered_marker']['reading']=='Number one, '
        assert 'ordered_marker' not in doc.blocks[2].locator
        assert doc.blocks[3].locator['ordered_marker']['reading']=='Number two, '
        text = doc.blocks[4]
        assert text.links[0]['text']=='The bell was broken.'
        assert text.links[0]['source_locator']['xml_path'].endswith('/text:footnote')
        mark = next(span for span in text.locator['inline_semantics'] if span['kind']=='emphasis')
        assert mark['source']=='waited'
        assert mark['style_definition']['xml_path'].endswith('/style:style')
        assert '/draw:text-box/text:p' in doc.blocks[5].locator['element']


def test_damaged_writer_package_reports_its_missing_part(tmp_path):
    source = wrap(tmp_path/'book.zip',{'Manuscript/mimetype':TEXT_MIME,
                                      'Manuscript/Thumbnails/thumbnail.png':b'\x89PNG\r\n\x1a\n'})
    with pytest.raises(Error) as failed:
        inspect_source(source,offline=True)
    assert failed.value.code=='source_member_missing'
    assert 'content.xml' in failed.value.message


def test_openoffice_writer_can_declare_its_media_type_in_the_manifest(tmp_path):
    content = f'''<office:document-content xmlns:office="{LEGACY}office" xmlns:text="{LEGACY}text">
      <office:body><text:p>The keeper went home.</text:p></office:body></office:document-content>'''
    manifest = f'''<manifest:manifest xmlns:manifest="http://openoffice.org/2001/manifest">
      <manifest:file-entry manifest:full-path="/" manifest:media-type="{LEGACY_MIME}.template"/>
      </manifest:manifest>'''
    members = {'content.xml':content,'META-INF/manifest.xml':manifest}
    direct = wrap(tmp_path/'template.stw',members)
    nested = wrap(tmp_path/'writer.zip',{'Template/'+name:data for name,data in members.items()})
    for path in (direct,nested):
        doc = inspect_source(path,offline=True)
        assert doc.metadata['writer_dialect']=='OpenOffice.org 1.x XML'
        assert doc.blocks[0].source=='The keeper went home.'
        assert not plan(doc)['issues']
