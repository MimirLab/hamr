import bz2
import gzip
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
import pytest

from audiobook.core import Error, loadjson
from audiobook.intake import extract_location, inspect_source
from audiobook.jobs import render
from audiobook.planner import plan
from audiobook.source_bundle import describe


def cell(row,column,content):
    return (f'<cell props="top-attach:{row}; left-attach:{column}; '
            f'bot-attach:{row+1}; right-attach:{column+1}"><p>{content}</p></cell>')


def native_edition():
    table = ''.join(cell(row,column,text) for row,column,text in
                    [(1,1,'Six'),(0,1,'Count'),(1,0,'North'),(0,0,'Gate')])
    return ('''<?xml version="1.0"?>
<abiword xmlns="http://www.abisource.com/awml.dtd">
<metadata><m key="dc.title">A Harbor Record</m><m key="dc.creator">Julia Case</m>
<m key="dc.language">en-US</m></metadata>
<styles><s name="Harbor chapter" basedon="Heading 1"/></styles>
<lists><l id="7" type="0" parentid="0" start-value="1"/>
<l id="8" type="0" parentid="7" start-value="1"/></lists>
<section footer="one">
<p style="Harbor chapter">Before dawn</p>
<p>The keeper crossed at dawn<field type="footnote_ref" footnote-id="4"/>
<foot footnote-id="4"><p><field type="footnote_anchor" footnote-id="4"/>The weather was clear.</p></foot>.</p>
<p listid="7" level="1"><field type="list_label"/><c type="list_label">\tSecure the gate.</c></p>
<p listid="8" level="2">Check the lamp.<c props="display:none">Printer control.</c></p>
<table>'''+table+'''</table>
<frame props="frame-type:textbox"><p>The harbor log is open.</p></frame>
<p><image dataid="lantern" alt="A lamp hangs at the gate."/></p>
<p style="Heading 1">At noon</p>
<p>The ferry left.<br/>The fog lifted.</p>
</section>
<section type="footer" id="one"><p>Repeating footer <field type="page_count"/>.</p></section>
<data><d name="lantern" mime-type="image/png" base64="yes">AAEE</d></data>
</abiword>''').encode()


@pytest.mark.parametrize('filename,encode',[('record.abw',bytes),('record.awt',bytes),
                                            ('record.zabw',gzip.compress),('record.abw.gz',gzip.compress),
                                            ('record.bzabw',bz2.compress),('record.abw.bz2',bz2.compress)])
def test_native_and_compressed_abiword_share_semantic_reading(tmp_path,filename,encode):
    source = tmp_path/filename
    source.write_bytes(encode(native_edition()))
    doc = inspect_source(source,offline=True)
    book = plan(doc)
    assert doc.title=='A Harbor Record' and doc.language=='en-US'
    assert doc.metadata['authors']==['Julia Case']
    assert book['chapters']==['Before dawn','At noon'] and not book['issues']
    utterances = '\n'.join(item['text'] for item in book['utterances'])
    assert utterances.count('The weather was clear.')==1 and 'Note.' in utterances
    assert 'Printer control' not in utterances and 'Repeating footer' not in utterances
    assert 'Secure the gate.' in utterances
    assert 'The ferry left.\nThe fog lifted.' in utterances
    assert 'A lamp hangs at the gate.' in utterances
    table, = [block for block in doc.blocks if block.role=='table']
    assert table.locator['rows']==[['Gate','Count'],['North','Six']]
    items = [block.locator for block in doc.blocks if block.locator.get('list_id')]
    assert [(item['list_id'],item['list_parent'],item['list_level']) for item in items]==[
        ('7','0','1'),('8','7','2')]
    asset, = [item for item in doc.inventory if item['kind']=='abiword_image']
    original, = etree.fromstring(native_edition()).xpath(asset['asset_location']['xml_path'])
    assert original.get('name')==asset['data_id']=='lantern'


def test_native_abiword_compound_original_and_archive_member_are_recoverable(tmp_path):
    from test_media import prepared

    source = tmp_path/'harbor.abw.gz'
    raw = gzip.compress(native_edition())
    source.write_bytes(raw)
    archive = tmp_path/'edition.zip'
    with ZipFile(archive,'w') as z:
        z.writestr('docs/harbor.ABW.GZ',raw)
    directory = tmp_path/'independent'
    directory.mkdir()
    (directory/source.name).write_bytes(raw)
    for item in (archive,directory):
        assert describe(item).route=='abiword'
        assert plan(inspect_source(item))['chapters']==['Before dawn','At noon']
    for i,item in enumerate((source,archive)):
        stage = tmp_path/f'job-{i}'
        stage.mkdir()
        store, job = prepared(stage,item)
        moved = Path(render(store,job['id'],allow_synthesis=False)['output']).rename(stage/'moved')
        companion = loadjson(moved/'companion.json')
        assert (moved/companion['original_source']).read_bytes()==item.read_bytes()
        source_fragment = next(loc for loc in companion['source_locations'].values() if loc.get('xml_path'))
        assert source_fragment['source_file']==(source.name if item==source else 'docs/harbor.ABW.GZ')
        extracted = stage/'exact'
        extract_location(moved,source_fragment['location_id'],extracted)
        assert extracted.read_bytes()==raw
        if item==source:
            assert companion['original_source']=='original.abw.gz'


def test_abiword_reports_exact_missing_field_and_truncated_compression(tmp_path):
    edition = tmp_path/'fields.abw'
    edition.write_text('''<abiword><section><p>As of <field type="date"/>, the harbor is open.</p>
<p>We can leave now.</p></section></abiword>''')
    result = plan(inspect_source(edition))
    issue, = result['issues']
    assert 'date' in issue['reason'] and issue['location']['xml_path']=='/abiword/section/p[1]'
    assert [u['text'] for u in result['utterances']]==['We can leave now.']
    damaged = tmp_path/'damaged.zabw'
    damaged.write_bytes(gzip.compress(edition.read_bytes())[:-8])
    with pytest.raises(Error) as caught:
        inspect_source(damaged)
    assert caught.value.code=='source_abiword_compression'
