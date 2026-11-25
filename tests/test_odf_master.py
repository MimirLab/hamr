from pathlib import Path
from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import inspect_saved, inspect_source
from audiobook.planner import plan
from audiobook.source_bundle import capture


NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:xlink="http://www.w3.org/1999/xlink"')
MEDIA = 'application/vnd.oasis.opendocument.text'


def writer(path,body,kind=''):
    path.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(path,'w') as archive:
        archive.writestr('mimetype',MEDIA+kind)
        archive.writestr('content.xml',f'<office:document-content {NS}><office:body><office:text>'
                         f'{body}</office:text></office:body></office:document-content>')
        archive.writestr('Thumbnails/thumbnail.png',b'\x89PNG\r\n\x1a\n')
    return path


def p(text):
    return f'<text:p>{text}</text:p>'


def section(name,href,*,region='',saved=''):
    target = f' text:section-name="{region}"' if region else ''
    return (f'<text:section text:name="{name}"><text:section-source xlink:href="{href}"{target}/>'
            f'{saved}</text:section>')


def wrap(path,*members):
    with ZipFile(path,'w') as archive:
        for name,member in members:
            archive.write(member,name)
    return path


def speech(document):
    return [unit['text'] for unit in plan(document)['utterances']]


def test_writer_template_reads_native_direct_and_in_zip(tmp_path):
    template = writer(tmp_path/'Story.ott',p('The tide was turning.'),'-template')
    renamed = tmp_path/'without-an-extension'
    renamed.write_bytes(template.read_bytes())
    zipped = wrap(tmp_path/'Story.zip',('folder/Story.ott',template))
    for source in (template,renamed,zipped):
        doc = inspect_source(source,offline=True)
        assert doc.format=='odt'
        assert speech(doc)==['The tide was turning.']
        assert not plan(doc)['issues']


def test_master_owns_nested_chapters_and_freezes_each_original(tmp_path):
    root = tmp_path/'Book folder'
    lead = p('The keeper checked the light.')
    tail = p('She closed the shutters.')
    master = writer(root/'book.odm',lead+section('First','../chapters/Chapter%20one.odt',region='Open water')
                    +section('Second','../chapters/Seaward.odm')+tail,'-master')
    first = writer(root/'chapters/Chapter one.odt',
                   p('Extra material before the chosen passage.')+
                   '<text:section text:name="Open water">'+p('The lantern flickered.')+'</text:section>'+
                   p('Extra material after the chosen passage.'))
    nested = writer(root/'chapters/Seaward.odm',p('Wind rose before dark.')+
                    section('Last','../Last chapter.odt'),'-master')
    last = writer(root/'chapters/Last chapter.odt',p('The tide was turning.'))
    # Archive entries deliberately disagree with the authored order.
    zipped = wrap(tmp_path/'Book.zip',*((name.relative_to(root).as_posix(),name)
                                        for name in (last,nested,first,master)))
    expected = ['The keeper checked the light.','The lantern flickered.','Wind rose before dark.',
                'The tide was turning.','She closed the shutters.']
    for path in (master,root,zipped):
        doc = inspect_source(path,offline=True)
        assert [block.source for block in doc.blocks]==expected
        assert not plan(doc)['issues']
        assert [block.locator['member'] for block in doc.blocks]==[
            'book.odm','chapters/Chapter one.odt','chapters/Seaward.odm','chapters/Last chapter.odt','book.odm']
        assert [item['status'] for item in doc.inventory if item['kind']=='odf_linked_section']==['included']*3
    area = tmp_path/'job'
    area.mkdir()
    snapshot,source = capture(root,area,offline=True)
    with ZipFile(snapshot) as archive:
        saved = {name:archive.read(name) for name in archive.namelist()}
    assert saved=={path.relative_to(root).as_posix():path.read_bytes() for path in (master,first,nested,last)}
    writer(last,p('This is a later edition.'))
    assert [block.source for block in inspect_saved(snapshot,source).blocks]==expected


def test_master_saved_content_and_localized_missing_and_cyclic_links(tmp_path):
    master = writer(tmp_path/'Main.odm',p('A clean start.')
                    +section('Saved','../Later.odt',saved=p('A sentence from the saved edition.'))
                    +section('Missing','../Missing.odt')
                    +section('Cycle','../Main.odm')
                    +section('Missing region','../Later.odt',region='No such section')
                    +p('A clean finish.'),'-master')
    writer(tmp_path/'Later.odt',p('A different sentence from a later edition.'))
    doc = inspect_source(master,offline=True)
    assert [block.source for block in doc.blocks if not block.issue]==[
        'A clean start.','A sentence from the saved edition.','A clean finish.']
    assert ['missing' in block.issue or 'cycle' in block.issue for block in doc.blocks if block.issue]==[True]*3
    assert [entry['status'] for entry in doc.inventory if entry['kind']=='odf_linked_section']==[
        'saved','unresolved','unresolved','unresolved']
    assert all(block.locator['member']=='Main.odm' for block in doc.blocks)


def test_packaged_iri_must_resolve_within_the_selected_publication(tmp_path):
    parent = tmp_path/'book'
    master = writer(parent/'sources/Main.odm',p('Introduction.')
                    +section('Adjacent chapter','../../Close.odt')
                    +section('Remote','https://example.invalid/remote.odt')
                    +section('Beyond publication','../../../Outside.odt')
                    +section('Wrong base','Close.odt')
                    +p('Closing words.'),'-master')
    child = writer(parent/'Close.odt',p('The chapter within the folder.'))
    writer(tmp_path/'Outside.odt',p('This material lies outside the selected folder.'))
    zipped = wrap(tmp_path/'book.zip',('sources/Main.odm',master),('Close.odt',child))
    for path in (parent,zipped):
        doc = inspect_source(path,offline=True)
        assert [block.source for block in doc.blocks if not block.issue]==[
            'Introduction.','The chapter within the folder.','Closing words.']
        assert len([block for block in doc.blocks if block.issue])==3


def test_unreadable_local_chapter_leaves_a_review_item_in_the_saved_job(tmp_path,monkeypatch):
    master = writer(tmp_path/'Main.odm',p('Introduction.')+section('Part','../Restricted.odt')
                    +p('Closing words.'),'-master')
    restricted = writer(tmp_path/'Restricted.odt',p('The full chapter belongs here.'))
    open_path = Path.open

    def deny_chapter(path,*args,**kwargs):
        if path==restricted:
            raise PermissionError(13,'Permission denied')
        return open_path(path,*args,**kwargs)

    monkeypatch.setattr(Path,'open',deny_chapter)
    doc = inspect_source(master,offline=True)
    assert [block.source for block in doc.blocks if not block.issue]==['Introduction.','Closing words.']
    assert [entry['status'] for entry in doc.inventory if entry['kind']=='odf_linked_section']==['unresolved']
    assert 'Restricted.odt' in plan(doc)['issues'][0]['reason']


def unpack(path,directory):
    with ZipFile(path) as archive:
        archive.extractall(directory)
    return directory


def test_unpacked_master_owns_its_linked_package_in_folder_and_zip(tmp_path):
    root = tmp_path/'Edition'
    master = writer(tmp_path/'Main.odm',p('The keeper checked the light.')+
                    section('Part','../Chapter%20one',region='Selected'),'-master')
    child = writer(tmp_path/'Part.odt',p('An unused introduction.')+
                   '<text:section text:name="Selected">'+p('The lantern flickered.')+'</text:section>')
    directory = unpack(master,root/'Reader')
    unpack(child,root/'Chapter one')
    parts = {file.relative_to(root).as_posix():file.read_bytes()
             for file in root.rglob('*') if file.is_file()}
    zipped = wrap(tmp_path/'Edition.zip',*((f'Shelf/{name}',root/name) for name in parts))

    expected = ['The keeper checked the light.','The lantern flickered.']
    for path,prefix in ((root,''),(zipped,'Shelf/')):
        doc = inspect_source(path,offline=True)
        assert speech(doc)==expected
        assert not plan(doc)['issues']
        assert [block.locator['member'] for block in doc.blocks]==[
            prefix+'Reader/content.xml',prefix+'Chapter one/content.xml']
        assert {item['member'] for item in doc.metadata['source_resources']}=={prefix+name for name in parts}

    scoped = inspect_source(directory,offline=True)
    assert speech(scoped)==expected[:1]
    assert 'leaves the supplied source folder' in plan(scoped)['issues'][0]['reason']

    area = tmp_path/'job'
    area.mkdir()
    snapshot,source = capture(root,area,offline=True)
    with ZipFile(snapshot) as archive:
        assert {name:archive.read(name) for name in archive.namelist()}==parts
    (root/'Chapter one/content.xml').write_text('A different edition.')
    assert speech(inspect_saved(snapshot,source))==expected


def test_unpacked_master_and_unrelated_scan_remain_two_choices(tmp_path):
    root = tmp_path/'Edition'
    master = writer(tmp_path/'Main.odm',p('The keeper checked the light.')+
                    section('Part','../A chapter.odt'),'-master')
    unpack(master,root/'Reader')
    child = writer(root/'A chapter.odt',p('The lantern flickered.'))
    scan = root/'independent.jpg'
    scan.write_bytes(b'\xff\xd8\xff')
    zipped = wrap(tmp_path/'Edition.zip',*((file.relative_to(root).as_posix(),file)
                                         for file in root.rglob('*') if file.is_file()))
    for path in (root,zipped):
        with pytest.raises(Error) as err:
            inspect_source(path,offline=True)
        assert err.value.code=='source_selection'
        pages,publication = err.value.candidates
        assert pages=={'id':'pages','name':'Image sequence (1 files)'}
        assert publication['name']=='Reader'
        selected = inspect_source(path,offline=True,entry=publication['id'])
        assert speech(selected)==['The keeper checked the light.','The lantern flickered.']
