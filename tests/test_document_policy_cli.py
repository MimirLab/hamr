import json
from audiobook.cli import main
from audiobook.core import Store,writejson
from audiobook.jobs import create


def test_real_cli_document_policy_repair_and_failed_update_are_atomic(tmp_path,capsys):
    state=tmp_path/'state';s=Store(state);source=tmp_path/'source.md'
    source.write_text('# Fixture\n\nFirst paragraph.\n\nSecond paragraph stays.\n')
    created=create(s,source);j=s.get(created['job']);first=next(b for b in j['document']['blocks'] if b['source']=='First paragraph.')
    policy={'source_sha256':j['document']['source_sha256'],'blocks':{first['id']:{'action':'companion','verified':True,'reason':'Verified source lookup retained in companion.'}}}
    path=tmp_path/'policy.json';writejson(path,policy)
    result=main(['--home',str(state),'repair',j['id'],'--document-policy',str(path),'--json'])
    assert result in (None,0)
    payload=json.loads(capsys.readouterr().out);assert payload['revision']==2
    current=s.get(j['id']);assert current['plan']['policy']['document']==policy
    assert 'Second paragraph stays.' in ' '.join(u['text'] for u in current['plan']['utterances'])
    before=s.db.execute('SELECT payload FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]
    policy['source_sha256']='wrong-source';writejson(path,policy)
    assert main(['--home',str(state),'repair',j['id'],'--document-policy',str(path),'--json'])==2
    assert json.loads(capsys.readouterr().out)['error']['code']=='document_policy_source'
    assert s.db.execute('SELECT payload FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]==before
