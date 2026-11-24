from pathlib import Path
import shutil
import sys
from zipfile import ZipFile

import pytest

from audiobook.chm_intake import local_target
from audiobook.core import Error, filehash
from audiobook.intake import capabilities, inspect_source
from audiobook.planner import plan
from audiobook.source_bundle import describe


FIXTURE = Path(__file__).resolve().parents[1]/'fixtures'/'chm_ordered.chm'
SPOKEN = ['Meet beside the lantern at dusk.','Read the guide note.',
          'Follow the stone path uphill.','Return before the rain begins.']
ORDER = ['pages/z-start.htm','pages/b-middle.htm','pages/a-last.htm']


@pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('bwrap'), reason='Linux CHM worker')
@pytest.mark.parametrize('wrapped',[False,True])
def test_chm_reads_the_authored_order_and_preserves_linked_material(tmp_path,wrapped):
    if not capabilities()['specialist']['chm']['ready']:
        pytest.skip('Resident CHM worker unavailable on this architecture')
    source = FIXTURE
    if wrapped:
        source = tmp_path/'download.zip'
        with ZipFile(source,'w') as archive:
            archive.writestr('manual/guide.chm',FIXTURE.read_bytes())
    doc = inspect_source(source,offline=True)
    assert doc.source_sha256==filehash(source) and doc.format=='chm'
    assert doc.title=='The Lantern Guide'
    spoken = plan(doc)
    assert not spoken['issues'] and [unit['text'] for unit in spoken['utterances']]==SPOKEN
    represented = [row for row in doc.inventory if row['status']=='represented']
    assert [row['member'] for row in represented]==ORDER
    assert [row['occurrence'] for row in represented]==[1,2,3]
    assert [row['depth'] for row in represented]==[1,1,2]
    assert [row['member'] for row in doc.inventory if row['status']=='unlisted']==['pages/unlisted.htm']
    popup, = [block for block in doc.blocks if block.locator.get('auxiliary')]
    assert popup.source=='The lantern marks the western gate.'
    assert popup.locator['path']=='pages/popup.htm'
    assert popup.locator['anchor']=='meaning'
    caller = next(block for block in doc.blocks if block.source=='Read the guide note.')
    assert caller.links[0]['destinations']==['source.html#'+popup.id]
    if wrapped:
        assert all(block.locator['member']=='manual/guide.chm' for block in doc.blocks)


def test_chm_probe_requires_the_real_container_signature(tmp_path):
    stub = tmp_path/'manual.chm'
    stub.write_bytes(b'Not a compiled HTML Help file.')
    with pytest.raises(Error,match='no reader'):
        describe(stub)
    nosuffix = tmp_path/'manual'
    nosuffix.write_bytes(FIXTURE.read_bytes())
    assert describe(nosuffix).route=='chm'


@pytest.mark.parametrize('reference',['../external.htm','https://example.invalid/a.htm',
                                       'other.chm::/entry.htm','%2f..%2fa.htm'])
def test_chm_rejects_an_unbound_external_or_parent_target(reference):
    assert local_target(reference,'guide.hhc') is None
