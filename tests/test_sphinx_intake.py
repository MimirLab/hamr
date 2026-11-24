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


def write_sources(root,files):
    for name,data in files.items():
        path = root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data if isinstance(data,bytes) else data.encode())
    return root


def wrapped(root,path):
    with ZipFile(path,'w') as out:
        for item in sorted(root.rglob('*'),reverse=True):
            if item.is_file():
                out.write(item,root.name+'/'+item.relative_to(root).as_posix())
    return path


def lines(document):
    return [unit['text'] for unit in plan(document)['utterances']]


@pytest.mark.parametrize('archive',[False,True])
def test_sphinx_reads_placed_toctrees_including_hidden_and_globbed_chapters(tmp_path,archive):
    root = write_sources(tmp_path/'Manual',{
        'conf.py':"project = 'Harbor Manual'\nroot_doc = 'contents'\nexclude_patterns = ['pages/draft.rst']\n",
        'contents.rst':'''Harbor Manual
=============

The harbor was quiet at dawn.

.. toctree::
   :glob:
   :hidden:
   :reversed:

   pages/*

The narrator returned to the opening page.

.. toctree::

   Repeated chapter <pages/z-last>
   Website <https://example.org/harbor>
   self
''',
        'pages/z-last.rst':'A ferry arrived at noon.',
        'pages/a-first.rst':'The passengers walked ashore.',
        'pages/draft.rst':'Unlisted prose lives here.',
    })
    path = wrapped(root,tmp_path/'Manual.zip') if archive else root
    doc = inspect_source(path,offline=True)
    assert describe(path).route=='sphinx'
    assert doc.title=='Harbor Manual'
    assert lines(doc)==['Harbor Manual','The harbor was quiet at dawn.','A ferry arrived at noon.',
                        'The passengers walked ashore.','The narrator returned to the opening page.']
    assert {row['status'] for row in doc.inventory}=={'represented','duplicate navigation','external','self'}
    assert any(row['member'].endswith('/draft.rst') and row['status']=='unlisted'
               for row in doc.metadata['source_resources'])


def test_native_rst_footnotes_inline_math_and_literal_code_use_common_preparation(tmp_path):
    file = write_sources(tmp_path,{'note.rest':'''Harbor account
==============

The harbor was quiet. [#west]_

The area is :math:`r^2`.

.. [#west] The tide had turned.

.. code:: python

   internal_keyword = 5
'''})/'note.rest'
    document = inspect_source(file,offline=True)
    reading = plan(document)
    assert not reading['issues']
    assert document.title=='Harbor account'
    assert lines(document)==['Harbor account','The harbor was quiet. Note. The tide had turned. End note.',
                             'The area is r to the power of 2.','A literal block is available in the companion.']
    assert any(item.role=='code' and 'internal_keyword' in item.source for item in document.blocks)


def test_sphinx_include_uses_exact_member_and_original_line_through_publication(tmp_path):
    project = write_sources(tmp_path/'Book',{
        'conf.py':"rst_prolog = '.. |coast| replace:: western coast'\n",
        'index.rst':'''Harbor Guide
============

Before we approached the |coast|.

.. include:: /parts/current.inc
    :start-line: 2
    :end-line: 3

After we reached the harbor.
''',
        'parts/current.inc':b'Exclude this first line.\r\nExclude this second line.\r\nA ferry arrived at noon.\r\nIgnore this text.\r\n',
    })
    doc = inspect_source(project)
    assert lines(doc)==['Harbor Guide','Before we approached the western coast.','A ferry arrived at noon.',
                        'After we reached the harbor.']
    prepared_block = next(block for block in doc.blocks if block.source=='A ferry arrived at noon.')
    assert prepared_block.locator['lines']==[3,3]
    assert prepared_block.locator['member']=='Book/parts/current.inc'
    store,job = prepared(tmp_path,project)
    target = Path(render(store,job['id'],allow_synthesis=False)['output'])
    companion = json.loads((target/'companion.json').read_text())
    locate = next(key for key,loc in companion['source_locations'].items()
                  if loc.get('member')=='Book/parts/current.inc' and loc.get('lines')==[3,3])
    extracted = tmp_path/'verified.inc'
    extract_location(target,locate,extracted)
    assert extracted.read_bytes()==(project/'parts/current.inc').read_bytes()


def test_sphinx_never_executes_config_and_scopes_missing_unsafe_directives(tmp_path):
    marker=tmp_path/'unexpected-output'
    project = write_sources(tmp_path/'Manual',{
        'conf.py':f"from pathlib import Path\nPath({str(marker)!r}).touch()\nroot_doc = 'index'\n",
        'index.rst':'''Before the missing chapter.

.. toctree::

   absent
   present

.. include:: ../note.rst

.. raw:: html
   :file: ../note.rst

After the reviewers arrived.
''',
        'present.rst':'The existing chapter stays in order.',
    })
    (tmp_path/'note.rst').write_text('This text must stay outside the publication.')
    document = inspect_source(project)
    assert not marker.exists()
    assert lines(document)==['Before the missing chapter.','The existing chapter stays in order.',
                             'After the reviewers arrived.']
    issues=plan(document)['issues']
    assert len(issues)==3
    assert issues[0]['location']['lines']==[5,5]
    assert issues[0]['location']['path']=='Manual/absent'
    assert 'source project' in issues[1]['reason']
    assert 'disabled' in issues[2]['reason']


def test_sphinx_nested_condition_does_not_change_surrounding_heading_style(tmp_path):
    project=write_sources(tmp_path/'Manual',{
        'conf.py':'',
        'index.rst':'''Manual
======

.. only:: html and not epub

   Weather
   -------

   The weather was clear.

Local harbor
------------

.. only:: latex

   This belongs to a different rendering.

The ferry left before dusk.
''',
    })
    doc=inspect_source(project)
    assert not plan(doc)['issues']
    assert lines(doc)==['Manual','Weather','The weather was clear.','Local harbor','The ferry left before dusk.']


def test_unrelated_python_folder_with_rst_selections_is_not_a_publication(tmp_path):
    project=write_sources(tmp_path/'Sources',{
        'conf.py':'port = 8080',
        'first.rst':'The first source file.',
        'second.rst':'The second source file.',
    })
    with pytest.raises(Error) as error:
        describe(project)
    assert {row['id'] for row in error.value.candidates}=={'first.rst','second.rst'}


def test_rendered_sphinx_heading_and_chapter_label_exclude_generated_navigation(tmp_path):
    html = write_sources(tmp_path,{'book.html':'''<!doctype html>
<head><script src="_static/documentation_options.js"></script><title>Harbor</title></head>
<body><div class="body" role="main">
<h1><span class="section-number">1. </span>Harbor<a class="headerlink" href="#harbor">¶</a></h1>
<p>The ship arrived at noon.</p>
</div><aside><p>Navigation panel</p></aside></body>'''})/'book.html'
    doc = inspect_source(html)
    reading = plan(doc)
    assert reading['chapters']==['Harbor']
    assert lines(doc)==['Harbor','The ship arrived at noon.']
    assert '¶' in doc.blocks[0].source
