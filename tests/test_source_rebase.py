from copy import deepcopy
import pytest
from audiobook.core import Block,Document,Error,digest
from audiobook.source_rebase import rebase
from audiobook import jobs


def document(rows):
    return Document('Book','markdown','bytes',[Block(bid,text,{'format':'markdown','start':i}) for i,(bid,text) in enumerate(rows)])


def test_changed_ids_rebase_policy_and_multi_edit_chain_using_original_context():
    previous=document([('a','First.'),('b','Corrected again.'),('c','Last.')])
    fresh=document([('x','First.'),('y','Original.'),('z','Last.')])
    edits=[{'block':'b','original':'Original.','replacement':'Corrected.'},{'block':'b','original':'Corrected.','replacement':'Corrected again.'}]
    policy={'source_sha256':'bytes','blocks':{'b':{'action':'speak','expected_source_sha256':digest(b'Corrected again.')}}}
    history,p,m=rebase(previous,fresh,edits,policy)
    assert m=={'b':'y'} and p['blocks']=={'y':policy['blocks']['b']}
    assert fresh.blocks[1].source=='Corrected again.'
    assert all(r['block']=='y' for r in history) and edits[0]['block']=='b'


def test_repeated_text_needs_unique_context_and_survives_a_second_reimport():
    previous=document([('a','First.'),('b','Duplicate.'),('c','Middle.'),('d','Duplicate.'),('e','Last.')])
    fresh=document([('x','First.'),('y','Duplicate.'),('z','Middle.'),('q','Duplicate.'),('r','Last.')])
    p={'blocks':{'d':{'action':'companion'}}}
    _,new,m=rebase(previous,fresh,[],p)
    assert m=={'d':'q'} and new['blocks']=={'q':{'action':'companion'}}


@pytest.mark.parametrize('alteration',['deleted_context','different_source','two_matches'])
def test_ambiguous_or_changed_source_never_transfers_an_override(alteration):
    previous=document([('a','Same.'),('b','Same.')]);fresh=document([('x','Same.')])
    if alteration=='different_source':fresh.source_sha256='other'
    if alteration=='two_matches':fresh=document([('x','Same.'),('y','Same.'),('z','Same.')])
    with pytest.raises(Error,match='unique matching source'):rebase(previous,fresh,[],{'blocks':{'a':{'action':'companion'}}})


def test_real_repair_keeps_policy_when_reimport_changes_ids_and_conflict_is_atomic(tmp_path,monkeypatch):
    source=tmp_path/'source.md';source.write_text('Before.\n\nTarget.\n\nAfter.')
    from audiobook.core import Store
    s=Store(tmp_path/'state');jid=jobs.create(s,source)['job'];j=s.get(jid);target=j['document']['blocks'][1]
    policy={'source_sha256':j['document']['source_sha256'],'blocks':{target['id']:{'action':'companion','verified':True,'reason':'Verified lookup.'}}}
    jobs.repair(s,jid,document_policy=policy)
    fresh=jobs.document(s.get(jid))
    for b in fresh.blocks:b.id+='new'
    monkeypatch.setattr(jobs,'cached_inspect',lambda *a:deepcopy(fresh))
    jobs.repair(s,jid,reimport=True)
    saved=s.get(jid);assert target['id']+'new' in saved['plan']['policy']['document']['blocks']
    before=s.db.execute('select payload from jobs where id=?',(jid,)).fetchone()[0]
    fresh.blocks[1].source='Different source text.'
    with pytest.raises(Error):jobs.repair(s,jid,reimport=True)
    assert s.db.execute('select payload from jobs where id=?',(jid,)).fetchone()[0]==before
