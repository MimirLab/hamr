import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import extract_location, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.source_bundle import describe
from test_media import prepared


UUID = '12345678-1234-4000-8000-123456789A'


def item(type_,number,title,*,flags='',children=''):
    return (f'<BinderItem Type="{type_}" ID="{number}" UUID="{UUID}{number:02X}">'
            f'<Title>{title}</Title><MetaData>{flags}</MetaData>'
            f'<Children>{children}</Children></BinderItem>')


def document(entries,version='2.0',outside=''):
    return (f'<?xml version="1.0" encoding="UTF-8"?><ScrivenerProject Version="{version}"><Binder>'
            +item('DraftFolder',0,'Manuscript',children=entries)+outside+
            '</Binder><ProjectSettings><ProjectTitle>Harbor Book</ProjectTitle></ProjectSettings></ScrivenerProject>')


def write(root,name,data):
    path = root/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data if isinstance(data,bytes) else data.encode())
    return path


def body(number,*,legacy=False):
    return f'Files/Docs/{number}.rtf' if legacy else f'Files/Data/{UUID}{number:02X}/content.rtf'


def rtf(text):
    return b'{\\rtf1\\ansi\\ansicpg1252\\uc0 '+text.encode('ascii')+b'\\par}'


def wrapper(root,output,*,prefix=None,others=None):
    with ZipFile(output,'w') as archive:
        for path in sorted(root.rglob('*'),reverse=True):
            if path.is_file():
                parent = prefix if prefix is not None else root.name+'/'
                archive.write(path,parent+path.relative_to(root).as_posix())
        for name,data in (others or {}).items():
            archive.writestr(name,data)
    return output


def make_project(root,legacy):
    included = '<IncludeInCompile>Yes</IncludeInCompile>'
    excluded = '<IncludeInCompile>No</IncludeInCompile>'
    chapters = item('Folder',8,'The harbor',children=
                    item('Text',9,'A ledger entry',flags=included)+
                    item('Text',2,'A deleted passage',flags=excluded,children=item('Text',1,'A saved scene')))
    chapters += item('Text',3,'The shore')
    outside = item('ResearchFolder',5,'Research',children=item('Text',6,'Notes',flags=included))
    outside += item('Text',7,'Other matter',flags=included)
    manifest = write(root,'Guide.scrivx',document(chapters,'1.5' if legacy else '2.0',outside))
    write(root,body(9,legacy=legacy),rtf(r'The harbour caf\u233  opened at noon.'))
    write(root,body(1,legacy=legacy),rtf('The tide had turned.'))
    write(root,body(3,legacy=legacy),rtf('A fog covered the western shore.'))
    for i in (2,6,7):
        write(root,body(i,legacy=legacy),rtf('These private words should stay in the companion.'))
    write(root,'Files/Old.scrivx',document(item('Text',10,'Outdated backup')))
    return manifest


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('kind',['folder','binder','zip'])
def test_reads_authored_draft_order_and_groups_reused_rtf(tmp_path,legacy,kind):
    folder = tmp_path/('Project' if legacy else 'Project.scriv')
    manifest = make_project(folder,legacy)
    source = {'folder':folder,'binder':manifest}.get(kind)
    if source is None:
        source = wrapper(folder,tmp_path/'Backup.zip',prefix='' if legacy else 'Publisher/Project.scriv/')
    doc = inspect_source(source,offline=True)
    reading = plan(doc)
    assert doc.title=='Harbor Book' and reading['intake']['route']=='scrivener'
    assert not reading['issues']
    assert [unit['text'] for unit in reading['utterances']]==[
        'The harbour café opened at noon.','The tide had turned.','A fog covered the western shore.']
    assert reading['chapters']==['The harbor','The shore']
    rows = doc.inventory
    assert rows[0]['status']=='binder group'
    assert rows[2]['status']=='excluded from compile' and rows[3]['parents']==['The harbor','A deleted passage']
    assert any(location['member'].endswith('Files/Old.scrivx')
               for location in doc.metadata['source_resources'])


def test_archive_claims_project_files_but_keeps_other_publications(tmp_path):
    root=tmp_path/'Guide.scriv'
    make_project(root,False)
    write(root,'Files/Data/private-scan.png',b'image stays scoped to Research')
    source=wrapper(root,tmp_path/'Collection.zip',others={'Other book.md':'A separate publication.'})
    with pytest.raises(Error) as caught:
        describe(source)
    choices = {entry['name']:entry['id'] for entry in caught.value.candidates}
    assert set(choices)=={'Guide','Other book.md'}
    doc=inspect_source(source,entry=choices['Guide'])
    assert all('Other book' not in resource['member'] for resource in doc.metadata['source_resources'])
    assert describe(source,entry=choices['Other book.md']).route=='markdown'


def test_nested_binder_attachment_does_not_split_textbundle(tmp_path):
    root=tmp_path/'Guide.textbundle'
    write(root,'info.json','{"version":2,"type":"net.daringfireball.markdown"}')
    write(root,'text.md','The narrator reads the guide.')
    write(root,'assets/Draft.scrivx',document(item('Text',1,'Unused example')))
    source=wrapper(root,tmp_path/'Guide.zip')
    assert describe(source).route=='textbundle'


def test_direct_descriptor_capture_scopes_renamed_folders(tmp_path):
    project=tmp_path/'Downloads'
    manifest=make_project(project,False)
    write(project,'private.txt',b'Unrelated private file.')
    write(project,'Files/Data/annotations.txt',b'Private notes within this project.')
    doc=inspect_source(manifest)
    names={resource['member'] for resource in doc.metadata['source_resources']}
    assert 'Downloads/private.txt' not in names
    assert 'Downloads/Files/Data/annotations.txt' in names
    assert inspect_source(project).source_sha256==doc.source_sha256


def test_a_missing_body_preserves_source_locations_and_healthy_chapters(tmp_path):
    project=tmp_path/'Guide.scriv'
    manifest=make_project(project,False)
    (project/body(1)).unlink()
    doc=inspect_source(project)
    hold, = plan(doc)['issues']
    location=hold['location']
    assert location['member']=='Guide.scriv/'+manifest.name
    assert location['path']=='Guide.scriv/'+body(1)
    assert location['binder_item']==UUID+'01'
    assert '/Children/BinderItem' in location['xpath']
    assert len(plan(doc)['utterances'])==2


def test_moved_publication_recovers_note_synopsis_and_case_preserving_body(tmp_path):
    project=tmp_path/'Guide.scriv'
    make_project(project,False)
    original=project/body(9)
    lower=original.parent.relative_to(project).as_posix().lower().replace('files/data/','Files/Data/')
    raw_note=rtf('The precise note.')
    expected={'content.rtf':original.read_bytes(),'notes.rtf':raw_note,'synopsis.txt':b'The precise synopsis.'}
    original.unlink()
    source=wrapper(project,tmp_path/'Wrapped.zip',others={f'Guide.scriv/{lower}/{name}':data
                                                          for name,data in expected.items()})
    store,job=prepared(tmp_path,source)
    output=Path(render(store,job['id'],allow_synthesis=False)['output'])
    destination=tmp_path/'Moved book'
    output.rename(destination)
    companion=json.loads((destination/'companion.json').read_text())
    for filename,data in expected.items():
        relative=lower+'/'+filename
        location=next(key for key,value in companion['source_locations'].items()
                      if value.get('member')=='Guide.scriv/'+relative)
        recovered=tmp_path/filename
        extract_location(destination,location,recovered)
        assert recovered.read_bytes()==data


def test_competing_uuid_resource_names_are_held(tmp_path):
    project=tmp_path/'Guide.scriv'
    make_project(project,False)
    original=project/body(9)
    substitute='Guide.scriv/Files/Data/'+original.parent.name.lower()+'/content.rtf'
    source=wrapper(project,tmp_path/'Collision.zip',others={substitute:rtf('A competing reading.')})
    hold, = plan(inspect_source(source))['issues']
    assert 'ambiguous' in hold['reason']
    assert hold['location']['binder_item']==UUID+'09'


def test_rtf_escaped_line_endings_are_paragraphs_for_both_entry_points(tmp_path):
    root=tmp_path/'Escaped.scriv'
    write(root,'Guide.scrivx',document(item('Text',1,'First scene')))
    source=write(root,body(1),b'{\\rtf1 The bell rang at dawn.\\\nThe ferry left.\\\r\n'
                 b'The cold tide \nwas turning.\\\rWe walked ashore.}')
    expected=['The bell rang at dawn.','The ferry left.','The cold tide was turning.','We walked ashore.']
    for path in (source,root):
        doc=inspect_source(path)
        assert [block.source for block in doc.blocks]==expected
        assert [unit['text'] for unit in plan(doc)['utterances']]==expected


def test_paired_styles_keep_text_and_table_cells_without_speaking_controls(tmp_path):
    root=tmp_path/'Styled.scriv'
    write(root,'Guide.scrivx',document(item('Text',1,'First scene')))
    raw=(b'{\\rtf1 <$Scr_Ps::1>The bell rang at dawn.\\\n'
         b'The <!$Scr_Ps::1>ferry left.\\par '
         b'\\trowd <$Scr_Cs::2>Coast<!$Scr_Cs::2>\\cell west\\cell\\row}')
    write(root,body(1),raw)
    doc=inspect_source(root)
    assert '<$Scr_Ps::1>' in doc.blocks[0].source
    spoken=[unit['text'] for unit in plan(doc)['utterances']]
    assert spoken[:2]==['The bell rang at dawn.','The ferry left.']
    assert '$Scr_' not in ' '.join(spoken)
    assert doc.blocks[-1].locator['table_cell_semantics'][0]['source']=='<$Scr_Cs::2>Coast<!$Scr_Cs::2>'
    assert 'Coast' in ' '.join(spoken) and 'west' in ' '.join(spoken)
