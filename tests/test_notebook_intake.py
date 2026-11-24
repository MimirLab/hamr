from base64 import b64encode
from io import BytesIO
import json
from zipfile import ZipFile

from PIL import Image
import pytest

from audiobook.core import Error
from audiobook.intake import extract_location, inspect_source
from audiobook.planner import plan


def save(path,cells):
    path.write_text(json.dumps({'nbformat':4,'nbformat_minor':5,'metadata':{},'cells':cells}),encoding='utf-8')


def png(color):
    with Image.new('RGB',(3,2),color) as image,BytesIO() as result:
        image.save(result,format='PNG')
        return result.getvalue()


def markdown(text,**kwargs):
    return {'cell_type':'markdown','source':text,**kwargs}


def reading(result):
    return ' '.join(row['text'] for row in plan(result)['utterances'])


def test_saved_results_use_one_reading_and_keep_cell_order(tmp_path):
    source = tmp_path/'Outpost.ipynb'
    marker = tmp_path/'would-execute'
    save(source,[
        markdown(['# Observatory\n','\n','The team checked the instruments.']),
        {'cell_type':'code','id':'cell-12','source':f'open({str(marker)!r}, "w").write("executed")',
         'outputs':[
             {'output_type':'stream','name':'stdout','text':['Counting\rReady for winter.\n']},
             {'output_type':'execute_result','data':{
                 'text/html':['<h2>Results</h2><p>The harbor was clear.</p>'],
                 'text/plain':['This alternate representation must stay unused.']}},
             {'output_type':'display_data','data':{'text/html':'<script>skip</script>',
                                                   'text/plain':'All readings were stable.'}},
             {'output_type':'display_data','data':{
                 'text/html':'<img src="remote-image.png">','text/plain':'The light stayed on.'}},
         ]},
        markdown('The team <em>returned</em><br>home. <script>Skip the script.</script>'),
    ])
    wrapper = tmp_path/'Archive.zip'
    with ZipFile(wrapper,'w') as archive:
        archive.write(source,'Logs/Outpost.ipynb')
    direct,packed = (inspect_source(path) for path in (source,wrapper))
    output = reading(direct)
    assert reading(packed)==output
    assert not marker.exists()
    assert 'Ready for winter.' in output and 'Counting' not in output
    assert 'The harbor was clear.' in output and 'alternate representation' not in output
    assert 'All readings were stable.' in output and 'skip' not in output
    assert 'The light stayed on.' in output and not plan(direct)['issues']
    assert output.index('winter')<output.index('harbor')<output.index('returned')
    assert 'Skip the script' not in output and '<em>' not in output
    assert [row['selected_mime'] for row in direct.inventory if row['kind']=='notebook_output']==[
        'text/plain','text/html','text/plain','text/plain']
    assert plan(direct)['chapters']==['Observatory']
    assert next(b.locator for b in packed.blocks if b.role=='code')['member']=='Logs/Outpost.ipynb'
    assert {b.locator.get('output') for b in direct.blocks if b.source.startswith('The harbor')}=={2}


def test_notes_and_equations_survive_cell_and_table_projection(tmp_path):
    source = tmp_path/'Notebook.ipynb'
    save(source,[
        markdown('# The first pass\n\nKeep $x^2 z$ in view.[^a]\n\n[^a]: The marker is red.'),
        markdown('# The second pass\n\nTwo tickets cost $5 and $10.\n\n'
                 '| Measure | Value |\n|---|---|\n| Initial | $y_0^2$[^a] |\n\n[^a]: The marker is green.'),
        {'cell_type':'code','source':'observe()', 'outputs':[
            {'output_type':'execute_result','data':{'text/plain':'Alternate', 'text/latex':r'$$\frac{1}{2}$$'}},
        ]},
    ])
    doc = inspect_source(source)
    result = plan(doc)
    assert not result['issues']
    text = reading(doc)
    assert 'x to the power of 2 times z' in text
    assert 'y subscript 0 to the power of 2' in text
    assert 'the fraction with numerator 1 and denominator 2' in text and 'Alternate' not in text
    assert not any(span['kind']=='formula' for b in doc.blocks if 'tickets' in b.source
                   for span in b.locator.get('inline_semantics',[]))
    notes = {b.source:'source.html#'+b.id for b in doc.blocks if b.role=='note'}
    linked = [link for b in doc.blocks for link in b.links if link['kind']=='note']
    assert {link['destination'] for link in linked}==set(notes.values())
    table = next(b for b in doc.blocks if b.role=='table')
    assert table.locator['table_cell_semantics'][0]['notes'][0]['destination']==notes['The marker is green.']


@pytest.mark.parametrize('packed',(False,True))
def test_attachment_ids_recover_the_original_encoded_values(tmp_path,packed):
    asset_bytes = [png('red'),png('green')]
    source = tmp_path/'Notebook.ipynb'
    save(source,[markdown('![An authored diagram.](attachment:chart.png)',attachments={
        'chart.png':{'image/png':b64encode(data).decode()}}) for data in asset_bytes])
    original = source
    if packed:
        original = tmp_path/'Archive.zip'
        with ZipFile(original,'w') as archive:
            archive.write(source,'Published/Notebook.ipynb')
    doc = inspect_source(original)
    records = doc.metadata['source_resources']
    ids = {record['location_id'] for record in records}
    assert len(ids)==2 and all(record['status']=='referenced' for record in records)
    assert {b.links[0]['destinations'][0].split('#resource-')[1] for b in doc.blocks}==ids
    (tmp_path/'companion.json').write_text(json.dumps({
        'original_source':original.name,'source_sha256':doc.source_sha256,'source_kind':'zip' if packed else 'file',
        'source_locations':{record['location_id']:record for record in records}}))
    for index,(record,expected) in enumerate(zip(records,asset_bytes)):
        result = tmp_path/f'recovered-{index}.png'
        extract_location(tmp_path,record['location_id'],result)
        assert result.read_bytes()==expected


def test_legacy_worksheet_order_and_localized_output_errors(tmp_path):
    path = tmp_path/'Legacy.ipynb'
    path.write_text(json.dumps({
        'nbformat':3,'nbformat_minor':0,'metadata':{},'worksheets':[
            {'cells':[{'cell_type':'heading','source':'Original title','level':1},
                      {'cell_type':'code','input':['check()\n'],'outputs':[
                          {'output_type':'pyout','html':'<p>Seen once.</p>','text':'Alternate copy.'},
                          {'output_type':'stream','stream':'stdout','text':{'not':'text'}},
                      ]}]},
            {'cells':[{'cell_type':'raw','source':'Weather kept improving.'}]}]}))
    doc = inspect_source(path)
    result = plan(doc)
    issue, = result['issues']
    loc = issue['location']
    assert loc['worksheet']==1 and loc['output']==2
    speech = reading(doc)
    assert 'Seen once.' in speech and 'Alternate copy.' not in speech
    assert speech.index('Seen once')<speech.index('Weather kept improving')
    assert doc.blocks[-1].locator['worksheet']==2


def test_duplicate_json_keys_are_rejected_before_choosing_a_source(tmp_path):
    path = tmp_path/'Ambiguous.ipynb'
    path.write_text('{"nbformat":4,"cells":[],"cells":[{"source":"ambiguous"}]}')
    with pytest.raises(Error,match='repeated object key'):
        inspect_source(path)
