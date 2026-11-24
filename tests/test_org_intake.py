from zipfile import ZipFile

from audiobook.intake import inspect_source
from audiobook.planner import plan
from audiobook.source_bundle import capture


def spoken(document):
    return ' '.join(item['text'] for item in plan(document)['utterances'])


def test_org_exported_tree_uses_shared_notes_ordered_items_and_tables(tmp_path):
    source = tmp_path/'Guide.org'
    source.write_text(
        '#+title: Harbor Watch\n'
        'This opening is outside the selected tree.\n'
        '* The harbor\n'
        '** First watch :export:\n'
        'The watch was /uneventful/[fn:start].\n\n'
        '3. The keeper checked the lamp.\n'
        '4. The gate was closed.\n\n'
        '| Station | Condition |\n'
        '|---------+-----------|\n'
        '| North   | All clear |\n\n'
        '*** A private observation :noexport:\n'
        'Keep this private.\n'
        '** Another night\n'
        'This is outside the selected tree.\n'
        '* Footnotes :export:\n'
        '[fn:start] The tide was low.\n'
    )
    saved = inspect_source(source)
    assert saved.title=='Harbor Watch'
    assert [row.source for row in saved.blocks if row.role=='heading']==['The harbor','First watch']
    assert [row.source for row in saved.blocks if row.role=='note']==['The tide was low.']
    assert 'The watch was uneventful. Note. The tide was low. End note.' in spoken(saved)
    assert 'Number three, The keeper checked the lamp.' in spoken(saved)
    assert 'outside the selected tree' not in spoken(saved)
    assert 'Keep this private.' not in spoken(saved)
    assert any(row.role=='table' and row.locator['rows']==[['Station','Condition'],['North','All clear']]
               for row in saved.blocks)
    assert not plan(saved)['issues']


def test_org_local_setup_and_line_slice_preserve_exact_member(tmp_path):
    source = tmp_path/'guide.org'
    part = tmp_path/'watch.org'
    setup = tmp_path/'setup.org'
    source.write_text('#+SETUPFILE: "setup.org"\n* Harbor\n#+INCLUDE: "watch.org" :lines "2-5"\n')
    part.write_bytes(b'Exclude the first line.\r\nA {{{marker(clear)}}} lamp[fn:state].\r\n\r\n[fn:state] The tide was low.\r\n')
    setup.write_text('#+MACRO: marker $1\n')
    working = tmp_path/'capture'
    working.mkdir()
    snapshot,selection = capture(source,working)
    assert (selection.route,selection.kind)==('org','zip-member')
    with ZipFile(snapshot) as archive:
        assert archive.read('watch.org')==part.read_bytes()
        assert archive.read('setup.org')==setup.read_bytes()
    document = inspect_source(source)
    block = next(row for row in document.blocks if row.source=='A clear lamp[fn:state].')
    assert block.locator['member']=='watch.org'
    assert block.locator['lines']==[2,2]
    assert block.links[0]['text']=='The tide was low.'
    assert [node.source for node in document.blocks if node.role=='note']==['The tide was low.']
    assert 'A clear lamp.' in spoken(document)
    assert not plan(document)['issues']


def test_org_code_and_hidden_includes_are_never_followed(tmp_path):
    source = tmp_path/'guide.org'
    source.write_text(
        '#+BEGIN_SRC text\n'
        '#+INCLUDE: "outside.org"\n'
        '#+END_SRC\n'
        '* COMMENT Private\n'
        '#+INCLUDE: "secret.org"\n'
        '* The harbor\n'
        'The gate closed.\n'
    )
    working = tmp_path/'capture'
    working.mkdir()
    snapshot,selection = capture(source,working)
    assert selection.kind=='file'
    assert snapshot.read_bytes()==source.read_bytes()
    document = inspect_source(source)
    assert [row.role for row in document.blocks]==['code','heading','body']
    assert not plan(document)['issues']
    assert 'closed.' in spoken(document)
    assert 'outside.org' not in spoken(document)


def test_org_missing_include_and_macro_are_located_without_spoken_control_tokens(tmp_path):
    source = tmp_path/'guide.org'
    source.write_text(
        '* The harbor\n'
        '#+INCLUDE: "missing.org"\n'
        'The gate closed.\n\n'
        'A {{{unknown(detail)}}} follows.\n'
    )
    document = inspect_source(source)
    findings = [row for row in document.blocks if row.issue]
    assert [row.locator['lines'] for row in findings]==[[2,2],[5,5]]
    assert all(row.locator['member']=='guide.org' for row in findings)
    assert 'missing.org' not in spoken(document)
    assert 'unknown' not in spoken(document)


def test_org_custom_id_selects_one_subtree_and_uses_original_caption_lines(tmp_path):
    source = tmp_path/'guide.org'
    part = tmp_path/'watch.org'
    source.write_text('* The harbor\n#+INCLUDE: "watch.org::#night" :only-contents t\n')
    part.write_text(
        '* An earlier observation\nThe red light failed.\n'
        '* Night watch\n:PROPERTIES:\n:CUSTOM_ID: night\n:END:\n'
        '#+CAPTION: The harbor register.\n'
        '| First | Last |\n|-------+------|\n| Entry | Exit |\n'
        '* The next day\nThe lamp was reset.\n'
    )
    document = inspect_source(source)
    assert [row.source for row in document.blocks if row.role=='heading']==['The harbor']
    caption, = [row for row in document.blocks if row.role=='caption']
    assert caption.source=='The harbor register.'
    assert caption.locator['member']=='watch.org' and caption.locator['lines']==[7,7]
    assert 'Entry' in spoken(document) and 'light failed' not in spoken(document)
    assert 'was reset' not in spoken(document)
