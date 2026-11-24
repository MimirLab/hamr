import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import extract_location, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe
from test_media import prepared


NAVIGATION = '''# Table of contents

## Arrival

* [The ferry](ferry.md "Ferry link")
  * [Later on](later.md)
* [Our website](https://example.org/site)
* [Home](intro.md)
* [Repeated navigation](ferry.md#details)
'''


def create(root, files):
    for name, data in files.items():
        file = root/name
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_bytes(data if isinstance(data,bytes) else data.encode())
    return root


def package(root, target, prefix=''):
    with ZipFile(target,'w') as archive:
        for path in sorted(root.rglob('*'),reverse=True):
            if path.is_file():
                archive.write(path,prefix+path.relative_to(root).as_posix())
    return target


def configured(root):
    return create(root,{
        '.gitbook.yaml':'root: ./content\nstructure:\n  readme: intro.md\n  summary: book-index.md\n',
        'content/book-index.md':NAVIGATION,
        'content/intro.md':'The harbor was quiet.',
        'content/ferry.md':'A ferry arrived at noon.',
        'content/later.md':'The passengers walked ashore.',
        'content/draft.md':'Unlisted last page.',
        'private/note.txt':'A file outside the content root.',
    })


@pytest.mark.parametrize('wrapped',[False,True])
def test_contained_authored_order_groups_and_external_entries(tmp_path,wrapped):
    root = configured(tmp_path/'Book')
    if wrapped:
        root = package(root,tmp_path/'Download.zip','Book/')
        with pytest.raises(Error) as found:
            describe(root)
        candidates = found.value.candidates
        assert {item['name'] for item in candidates}=={'Book','Book/private/note.txt'}
        selected = next(item['id'] for item in candidates if item['name']=='Book')
    else:
        selected = None
    document = inspect_source(root,entry=selected,offline=True)
    reading = plan(document)
    assert reading['intake']['route']=='gitbook'
    assert reading['chapters']==['Home','The ferry','Later on']
    assert not reading['issues']
    assert [unit['text'] for unit in reading['utterances']]==[
        'The harbor was quiet.','A ferry arrived at noon.','The passengers walked ashore.']
    rows = document.inventory
    assert rows[1]['navigation_label']=='Ferry link'
    assert rows[2]['parents']==['The ferry'] and rows[2]['part']=='Arrival'
    assert rows[3]['status']=='external' and rows[4]['status']=='duplicate navigation'
    assert any(row['member'].endswith('draft.md') and row['status']=='unlisted'
               for row in document.metadata['source_resources'])


@pytest.mark.parametrize('wrapped',[False,True])
def test_site_keeps_local_spaces_distinct_with_nested_sections_and_absolute_paths(tmp_path,wrapped):
    repo = create(tmp_path/'Repo',{
        'gitbook-docs.yaml':'''site:
  structure:
    - type: section-group
      children:
        - type: section
          children:
            - type: space
              key: north
              title: Northern port
              content: {directory: ./north, language: en}
            - type: space
              key: south
              title: Southern port
              content: {directory: /south, language: en}
    - type: space
      key: unavailable
      content: {directory: null}
''',
        'north/README.md':'The north harbor.',
        'north/SUMMARY.md':'* [North](README.md)',
        'south/README.md':'The south harbor.',
        'south/SUMMARY.md':'* [South](README.md)',
        'south/.git/config':b'unrelated test marker',
    })
    source = package(repo,tmp_path/'Site.zip','Repo/') if wrapped else repo
    with pytest.raises(Error) as found:
        describe(source)
    choices = found.value.candidates
    assert {row['name'] for row in choices}=={'Northern port','Southern port'}
    entry = next(row['id'] for row in choices if row['name']=='Southern port')
    report = inspect_source(source,entry=entry,offline=True)
    assert report.title=='Southern port' and report.metadata['gitbook']['space']=='south'
    assert [unit['text'] for unit in plan(report)['utterances']]==['The south harbor.']
    if not wrapped:
        stage=tmp_path/'stage'
        stage.mkdir()
        snapshot,_=capture(source,stage,entry=entry)
        with ZipFile(snapshot) as archive:
            assert 'Repo/gitbook-docs.yaml' in archive.namelist()
            assert not any('north/' in name or '/.git/config' in name for name in archive.namelist())
        collection = create(tmp_path/'Collection',{'other.txt':'Another publication.'})
        source.rename(collection/'Repo')
        with pytest.raises(Error) as found:
            describe(collection)
        assert {row['id'] for row in found.value.candidates}=={'Repo/north','Repo/south','other.txt'}
        collected = inspect_source(collection,entry='Repo/south',offline=True)
        assert collected.source_sha256==report.source_sha256


@pytest.mark.parametrize('wrapped',[False,True])
def test_mapped_space_has_same_boundary_when_another_space_is_inside_it(tmp_path,wrapped):
    repo = create(tmp_path/'Repo',{
        'gitbook-docs.yaml':'''site:
  structure:
    - {type: space, key: main, title: Main, content: {directory: .}}
    - {type: space, key: separate, title: Separate, content: {directory: ./nested}}
''',
        'README.md':'The main harbor.',
        'nested/README.md':'The separate harbor.',
    })
    source = package(repo,tmp_path/'Site.zip','Repo/') if wrapped else repo
    with pytest.raises(Error) as found:
        describe(source)
    choices = found.value.candidates
    assert {row['name'] for row in choices}=={'Main','Separate'}
    for choice in choices:
        doc = inspect_source(source,entry=choice['id'],offline=True)
        assert [unit['text'] for unit in plan(doc)['utterances']]==[f"The {choice['name'].lower()} harbor."]
        if choice['name']=='Main':
            assert not any('nested/' in row['member'] for row in doc.metadata['source_resources'])


@pytest.mark.parametrize('wrapped',[False,True])
def test_inferred_chapters_use_home_and_natural_file_order(tmp_path,wrapped):
    root = create(tmp_path/'Book',{
        '.gitbook.yaml':'structure: {readme: home.md}',
        'home.md':'The harbor guide.',
        'chapter10.md':'Lighthouse.',
        'chapter2.md':'Ferry service.',
        '.gitbook/includes/draft.md':'An include is not an independent chapter.',
    })
    source = package(root,tmp_path/'Inferred.zip','Book/') if wrapped else root
    document = inspect_source(source)
    assert document.metadata['gitbook']['navigation']=='inferred'
    assert [unit['text'] for unit in plan(document)['utterances']]==[
        'The harbor guide.','Ferry service.','Lighthouse.']


def test_bare_summary_without_a_configuration_is_an_ordered_book(tmp_path):
    root = create(tmp_path/'Guide',{
        'SUMMARY.md':'* [Second name](z.md)\n* [First name](a.md)\n',
        'a.md':'A later chapter.',
        'z.md':'An opening chapter.',
    })
    for source in (root,package(root,tmp_path/'Bare.zip')):
        doc = inspect_source(source)
        assert [unit['text'] for unit in plan(doc)['utterances']]==['An opening chapter.','A later chapter.']


def test_bare_summary_of_non_markdown_files_does_not_claim_an_archive(tmp_path):
    root = create(tmp_path/'File collection',{
        'SUMMARY.md':'* [Plain text](notes.txt)',
        'notes.txt':'Notes on the harbor.',
    })
    with pytest.raises(Error) as found:
        describe(package(root,tmp_path/'Files.zip'))
    assert {row['name'] for row in found.value.candidates}=={'SUMMARY.md','notes.txt'}


def test_visible_gitbook_structures_and_original_attachment_survive(tmp_path):
    root = create(tmp_path/'Book',{
        '.gitbook.yaml':'',
        'SUMMARY.md':'* [Guide](README.md)',
        'README.md':'''---
description: Hidden card metadata
---

{% hint style="info" %}
The harbor opens at dawn.
{% endhint %}

{% hint style="info" %} The west pier opens at noon. {% endhint %}

{% tabs %}
{% tab title="By water" %}
The ferry leaves at noon.
{% endtab %}
{% endtabs %}

<table data-view="cards"><tbody><tr><td><h4>Visitor center</h4></td><td>Open every morning.</td><td data-hidden>Invisible card data.</td></tr></tbody></table>

{% file src=".gitbook/assets/sample.txt" %}
A copy of the guide.
{% endfile %}
''',
        '.gitbook/assets/sample.txt':b'The exact attached guide.',
    })
    source = package(root,tmp_path/'Publication.zip','Book/')
    reading = plan(inspect_source(source))
    assert not reading['issues']
    text = ' '.join(unit['text'] for unit in reading['utterances'])
    for sentence in ('The harbor opens at dawn.','The west pier opens at noon.',
                     'By water','The ferry leaves at noon.','Visitor center','Open every morning.',
                     'A copy of the guide.'):
        assert sentence in text
    assert all(fragment not in text for fragment in ('{%', '<table', 'Hidden card', 'Invisible card'))
    store,job = prepared(tmp_path,source)
    output = Path(render(store,job['id'],allow_synthesis=False)['output'])
    indexes = json.loads((output/'companion.json').read_text())['source_locations']
    asset = next(key for key,item in indexes.items() if item.get('member','').endswith('/sample.txt'))
    result = tmp_path/'restored.txt'
    extract_location(output,asset,result)
    assert result.read_bytes()==b'The exact attached guide.'


def test_generated_navigation_is_visible_and_healthy_chapters_stay_planned(tmp_path):
    root = create(tmp_path/'Book',{
        '.gitbook.yaml':'',
        'README.md':'Before the generated resource.',
        'after.md':'After the generated resource.',
        'SUMMARY.md':'''* [Beginning](README.md)
  * ```yaml
    type: builtin:openapi
    ```
* [Afterwards](after.md)
''',
    })
    result = plan(inspect_source(root))
    assert [unit['text'] for unit in result['utterances']]==[
        'Before the generated resource.','After the generated resource.']
    assert 'builtin:openapi' in result['issues'][0]['reason']


@pytest.mark.parametrize('bad',['root: ../elsewhere','root: docs\nroot: hidden'])
def test_manifest_does_not_select_escaping_or_competing_roots(tmp_path,bad):
    root = create(tmp_path/'Book',{'.gitbook.yaml':bad,'README.md':'The harbor.'})
    with pytest.raises(Error) as failure:
        inspect_source(root)
    assert failure.value.code in ('source_gitbook_config','unsafe_source_member')
