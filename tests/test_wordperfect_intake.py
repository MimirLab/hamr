import shutil
from zipfile import ZipFile

import pytest

from audiobook.core import Error
from audiobook.intake import inspect_source
from audiobook.planner import plan
from audiobook.source_bundle import probe
from audiobook.legacy_office_intake import resident


def test_wpc_identifies_supported_wordperfect_document_types():
    header = b'\xffWPC\x10\0\0\0\1'
    for version in (b'\x0a\0',b'\x0a\2',b'\x2c\2',b'\x2c\3',b'\x2c\4'):
        assert probe(header+version+b'\0\0\0','Story.doc')=='wordperfect'
    assert probe(header+b'\x0b\0\0\0\0','Other.doc')=='office-legacy'
    assert probe(header+b'\x0a\x7f\0\0\0','Other.doc')=='office-legacy'


@pytest.mark.skipif(resident() is None or not shutil.which('bwrap'),
                    reason='Needs the resident Linux WordPerfect reader and Bubblewrap')
def test_native_wordperfect_uses_shared_intake_and_reports_invalid_input(tmp_path):
    reading = tmp_path/'reading'
    reading.mkdir()
    source = reading/'Harbor.wp'
    source.write_bytes(b'The keeper watched the \x9dharbor\x9c.\x0aThe tide was rising.\x0a')
    archive = tmp_path/'book.zip'
    with ZipFile(archive,'w') as container:
        container.write(source,'edition/Harbor.wp')
    for path in (source,reading,archive):
        document = inspect_source(path)
        assert document.format=='wordperfect'
        assert document.metadata['converter']['intermediate']=='Flat ODF'
        assert all(item.locator['converter_intermediate'] for item in document.blocks)
        result = plan(document)
        assert not result['issues']
        assert [item['text'] for item in result['utterances']]==[
            'The keeper watched the harbor.', 'The tide was rising.']
    false_extension = tmp_path/'invalid.wpd'
    false_extension.write_bytes(b'Regular plain text.')
    with pytest.raises(Error,match='could not identify a supported WordPerfect') as failure:
        inspect_source(false_extension)
    assert failure.value.code=='unreadable_wordperfect'
