from zipfile import ZipFile

from audiobook.intake import inspect_source
from audiobook.planner import plan
from audiobook.source_bundle import capture, describe


def speech(document):
    return ' '.join(row['text'] for row in plan(document)['utterances'])


def test_asciidoc_preserves_legacy_asc_probe_without_claiming_plain_text(tmp_path):
    plain = tmp_path/'Plain.asc'
    plain.write_text('This is a plain text note.\n')
    book = tmp_path/'Book.asc'
    book.write_text('[[anchor]]\n== The harbor\n\nThe lamp stayed lit.\n')
    assert describe(plain).route=='text'
    assert describe(book).route=='asciidoc'
    archive = tmp_path/'Books.zip'
    with ZipFile(archive,'w') as package:
        package.write(plain,plain.name)
        package.write(book,book.name)
    assert describe(archive,entry='1').route=='text'
    assert describe(archive,entry='2').route=='asciidoc'
    assert speech(inspect_source(archive,entry='2'))==speech(inspect_source(book))
    assert speech(inspect_source(tmp_path,entry='Book.asc'))==speech(inspect_source(book))


def test_asciidoc_local_include_attributes_and_sparse_lines_survive_capture(tmp_path):
    source = tmp_path/'Main.adoc'
    source.write_text('= Harbor watch\n:parts: watch\n\ninclude::{parts}/night.adoc[lines="2..3;6..7"]\n')
    sub = tmp_path/'watch'
    sub.mkdir()
    included = sub/'night.adoc'
    original = b'Omit the opening.\r\nThe keeper lit a lamp.\r\n\r\nOmit this too.\r\n\r\nShe shut the gate.\r\n\r\n'
    included.write_bytes(original)
    saved = tmp_path/'saved'
    saved.mkdir()
    snapshot,choice = capture(source,saved)
    assert (choice.route,choice.kind)==('asciidoc','zip-member')
    with ZipFile(snapshot) as archive:
        assert archive.read('watch/night.adoc')==original
    doc = inspect_source(source)
    assert [(b.source,b.locator['source_file'],b.locator['lines']) for b in doc.blocks]==[
        ('The keeper lit a lamp.','watch/night.adoc',[2,2]),
        ('She shut the gate.','watch/night.adoc',[6,6]),
    ]
    assert not plan(doc)['issues']


def test_asciidoc_native_tables_captions_notes_and_callouts_use_shared_reading(tmp_path):
    source = tmp_path/'Watch.adoc'
    source.write_text(
        '= Harbor watch\n:keeper: Nora\n\n== At the harbor\n\n'
        '{keeper} closed the gate.footnote:tide[The tide was already low.]\n\n'
        '.Harbor register\n[cols="2*",options="header"]\n|===\n'
        '|Station |Status\n|North |Safe\n|===\n\n'
        '[source,sh]\n----\necho hello # <1>\n----\n'
        '<1> Say hello to the keeper.\n'
    )
    doc = inspect_source(source)
    assert not plan(doc)['issues']
    body, = [b for b in doc.blocks if b.source.startswith('Nora')]
    assert body.links[0]['reading']=='The tide was already low.'
    assert 'Nora closed the gate. Note. The tide was already low. End note.' in speech(doc)
    table, = [b for b in doc.blocks if b.role=='table']
    assert table.locator['rows']==[['Station','Status'],['North','Safe']]
    caption, = [b for b in doc.blocks if b.role=='caption']
    assert caption.locator['lines']==[8,8] and caption.locator['source_file']=='Watch.adoc'
    assert 'Number one, Say hello to the keeper.' in speech(doc)
    assert 'echo hello' not in speech(doc)


def test_asciidoc_admonition_layout_does_not_turn_prose_into_a_table(tmp_path):
    source = tmp_path/'Watch.adoc'
    source.write_text('= Watch\n\n[NOTE]\n====\nThe keeper closed the gate.\n\nHe walked home.\n====\n\n'
                      '1. The first watch started.\n2. The second watch waited.\n')
    doc = inspect_source(source)
    assert [b.source for b in doc.blocks[:3]]==['Note.','The keeper closed the gate.','He walked home.']
    assert all(b.role!='table' for b in doc.blocks)
    assert [b.locator['lines'] for b in doc.blocks]==[[3,3],[5,5],[7,7],[10,10],[11,11]]


def test_asciidoc_code_include_and_escaped_marker_keep_separate_source_evidence(tmp_path):
    source = tmp_path/'Watch.adoc'
    (tmp_path/'sample.txt').write_text('open the gate\n')
    source.write_text('= Watch\n\n----\ninclude::sample.txt[]\n\\include::literal.txt[]\n----\n\n'
                      'The keeper left.\n')
    doc = inspect_source(source)
    code, = [b for b in doc.blocks if b.role=='code']
    assert 'open the gate' in code.source and 'include::literal.txt[]' in code.source
    assert [row['member'] for row in doc.metadata['source_resources'] if row['status']=='included']==['sample.txt']
    assert not plan(doc)['issues']


def test_asciidoc_unavailable_include_stays_located_and_does_not_remove_neighboring_prose(tmp_path):
    source = tmp_path/'Watch.adoc'
    source.write_text('The lamp was lit.\n\ninclude::https://example.invalid/other.adoc[]\n\nThe keeper left.\n')
    doc = inspect_source(source)
    hold, = plan(doc)['issues']
    assert hold['location']['lines']==[3,3]
    assert hold['location']['source_file']=='Watch.adoc'
    assert 'The lamp was lit.' in speech(doc)
    assert 'The keeper left.' in speech(doc)
    assert 'example.invalid' not in speech(doc)
