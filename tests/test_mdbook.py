import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from audiobook.intake import extract_location, inspect_saved, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.scan_intake import sequence
from audiobook.source_bundle import capture, describe
from test_media import prepared

SUMMARY = '''# Summary

[Preface](z-preface.md)

# The garden

- [Opening](b-first.md)
  - [The return](root/nested.md)
- [A draft]()
- [Opening reprise](b-first.md)

[Afterword](a-end.md)
'''

CONTENTS = {
    'book.toml': '[book]\ntitle = "The Western Gate"\nsrc = "chapters"\n',
    'chapters/SUMMARY.md': SUMMARY,
    'chapters/z-preface.md': 'Before sunset, a lantern stood by the western gate.\n',
    'chapters/b-first.md': 'The lantern still burned in the dark.[^one]\n\n[^one]: The watchman had filled it.\n',
    'chapters/root/nested.md': 'The gate opened.\n\n{{#include ../inc/story.md:story}}\n',
    'chapters/inc/story.md': 'Omit the editorial note.\n<!-- ANCHOR: story -->\nThe watchman smiled.\n<!-- ANCHOR_END: story -->\n',
    'chapters/a-end.md': 'At dawn they found the gate open.\n',
    'chapters/unlisted.md': 'This file was never in the summary.\n',
    'chapters/lantern.png': 'asset bytes',
}


def folder(root):
    for name, value in CONTENTS.items():
        target = root/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value)
    return root


def archived(root, *, prefix='', extra=None):
    target = root/'Collected.zip'
    with ZipFile(target,'w') as archive:
        for name,value in sorted(CONTENTS.items(),reverse=True):
            archive.writestr(prefix+name,value)
        if extra:
            archive.writestr(*extra)
    return target


@pytest.mark.parametrize('kind',['folder','root ZIP','wrapped ZIP'])
def test_mdbook_author_order_structure_notes_and_exact_includes(tmp_path,kind):
    source = (folder(tmp_path/'Book') if kind=='folder'
              else archived(tmp_path,prefix='Book/' if kind=='wrapped ZIP' else ''))
    document = inspect_source(source,offline=True)
    result = plan(document)
    assert document.title=='The Western Gate'
    assert result['intake']['route']=='mdbook'
    assert result['chapters']==['Preface','Opening','The return','Opening reprise','Afterword']
    assert result['issues']==[]
    rows = [row for row in document.inventory if 'occurrence' in row]
    assert [(row['label'],row['depth'],row['status']) for row in rows]==[
        ('Preface',0,'represented'),('Opening',1,'represented'),('The return',2,'represented'),
        ('A draft',1,'draft'),('Opening reprise',1,'represented'),('Afterword',0,'represented')]
    assert rows[2]['parents']==['Opening'] and rows[2]['part']=='The garden'
    assert rows[1]['member']==rows[4]['member'] and set(rows[1]['blocks']).isdisjoint(rows[4]['blocks'])
    by_chapter = {block.chapter:[item for item in document.blocks if item.chapter==block.chapter]
                  for block in document.blocks}
    for chapter in ('Opening','Opening reprise'):
        body,note = by_chapter[chapter]
        assert body.links[0]['destinations']==['source.html#'+note.id]
    included = next(block for block in document.blocks if block.source=='The watchman smiled.')
    origin = next(span for span in included.locator['source_spans'] if span['member'].endswith('story.md'))
    assert CONTENTS['chapters/inc/story.md'][origin['start']:origin['end']]=='The watchman smiled.'
    assert all(not any(word in unit['text'] for word in ('never','Omit','ANCHOR')) for unit in result['utterances'])


def test_mixed_zip_keeps_one_book_and_does_not_treat_book_art_as_pages(tmp_path):
    source = archived(tmp_path,prefix='Book/',extra=('page1.png',b'loose scan'))
    from audiobook.core import Error

    with pytest.raises(Error) as outcome:
        describe(source)
    candidates = outcome.value.candidates
    assert {item['name'] for item in candidates}=={'Book','Image sequence (1 files)'}
    scan = describe(source,entry='pages')
    with sequence(source,scan) as (_,pages,_,__):
        assert [page['path'] for page in pages]==['page1.png']
    book = next(item['id'] for item in candidates if item['name']=='Book')
    assert inspect_source(source,entry=book).title=='The Western Gate'


def test_folder_capture_preserves_only_book_and_local_dependency_from_selected_container(tmp_path):
    library = tmp_path/'Library'
    source = folder(library/'Book')
    (source/'chapters/root/nested.md').write_text('The gate opened.\n\n{{#include ../../../extras/line.inc}}\n')
    extra = library/'extras/line.inc'
    extra.parent.mkdir()
    extra.write_text('The watchman smiled.\n')
    (library/'extras/private.ini').write_text('unrelated')
    dest = tmp_path/'snapshot'
    dest.mkdir()
    snapshot, selected = capture(library,dest)
    with ZipFile(snapshot) as archive:
        assert 'Library/extras/line.inc' in archive.namelist()
        assert 'Library/extras/private.ini' not in archive.namelist()
    extra.write_text('This replacement must not enter the book.')
    saved = inspect_saved(snapshot,selected)
    assert any(block.source=='The watchman smiled.' for block in saved.blocks)


def test_missing_chapter_is_visible_and_neighbours_survive(tmp_path):
    root = folder(tmp_path/'Book')
    (root/'chapters/root/nested.md').unlink()
    document = inspect_source(root)
    result = plan(document)
    assert len(result['issues'])==1
    assert 'chapters/root/nested.md' in result['issues'][0]['reason']
    assert result['chapters']==['Preface','Opening','Opening reprise','Afterword']
    assert (root/'chapters/root/nested.md').exists() is False


def test_moved_publication_recovers_exact_chapter_and_include(tmp_path):
    source = archived(tmp_path,prefix='Book/')
    store,job = prepared(tmp_path,source)
    published = Path(render(store,job['id'],allow_synthesis=False)['output'])
    moved = published.rename(tmp_path/'moved')
    source.unlink()
    companion = json.loads((moved/'companion.json').read_text())
    locations = companion['source_locations']
    block_id = next(row['block'] for row in companion['decisions'] if row['source']=='The watchman smiled.')
    relative = 'chapters/inc/story.md'
    include = next(location for location in locations.values() if location['member'].endswith(relative))
    target = tmp_path/'recovered.md'
    extract_location(moved,include['location_id'],target)
    assert target.read_text()==CONTENTS[relative]
    target = tmp_path/'recovered-chapter.md'
    extract_location(moved,block_id,target)
    assert target.read_text()==CONTENTS['chapters/root/nested.md']
