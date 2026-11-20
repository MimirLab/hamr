import json
import subprocess
import sys
import pytest
from pathlib import Path
from test_media import prepared
from audiobook import jobs
from audiobook.core import Error
from audiobook.selection import parse


def canaries(plan):return [u['text'] for u in plan['utterances'] if 'CANARY_' in u['text']]


def test_real_cli_selection_reselection_keeps_source_order_cache_and_companion(tmp_path):
    source=tmp_path/'chapters.md';source.write_text('\n\n'.join('# Same title\n\nCANARY_'+str(i)+'.' for i in range(1,6)))
    s,j=prepared(tmp_path,source);before={jobs.clip_key(u,j):jobs.valid_clip(s,jobs.clip_key(u,j)) for u in j['plan']['utterances']}
    result=subprocess.run([sys.executable,'-m','audiobook','--home',str(s.root),'repair',j['id'],'--chapters','5,2-3,2','--json'],capture_output=True,text=True,check=True)
    current=s.get(j['id']);assert canaries(current['plan'])==['CANARY_2.','CANARY_3.','CANARY_5.']
    assert current['plan']['policy']['chapters']==[2,3,5]
    assert len(current['plan']['chapter_catalog'])==5
    assert all(jobs.valid_clip(s,k)==receipt for k,receipt in before.items())
    result=jobs.render(s,j['id'],allow_synthesis=False);out=Path(result['output']);companion=json.loads((out/'companion.json').read_text())
    assert [r['text'] for r in companion['segments'] if 'CANARY_' in r['text']]==canaries(current['plan'])
    assert any('CANARY_1.' in r['source'] and r['action']=='companion' for r in companion['decisions'])
    assert result['metrics']['clips_rendered']==0
    saved=s.db.execute('select payload from jobs where id=?',(j['id'],)).fetchone()[0]
    with pytest.raises(Error):jobs.repair(s,j['id'],chapters='99')
    assert s.db.execute('select payload from jobs where id=?',(j['id'],)).fetchone()[0]==saved
    assert jobs.verify_publication(out,s.get(j['id']))
    jobs.repair(s,j['id'],chapters='all');restored=s.get(j['id'])
    assert canaries(restored['plan'])==['CANARY_'+str(i)+'.' for i in range(1,6)]
    assert jobs.render(s,j['id'],allow_synthesis=False)['metrics']['clips_rendered']==0


@pytest.mark.parametrize('value',['0','3-2','1,,2','1-999999','two',''])
def test_invalid_or_unbounded_selection_is_rejected(value):
    with pytest.raises(Error):parse(value)


def test_same_page_pdf_chapters_use_source_order_instead_of_collapsing_to_one_page():
    from audiobook.core import Block,Document
    from audiobook.planner import plan
    blocks=[Block('prefix','FOREWORD_CANARY.',{'format':'pdf','page':1}),Block('a','Chapter One',{'format':'pdf','page':2}),Block('x','ALPHA_CANARY.',{'format':'pdf','page':2}),Block('b','Chapter Two',{'format':'pdf','page':2}),Block('y','BETA_CANARY.',{'format':'pdf','page':2})]
    doc=Document('Book','pdf','source',blocks)
    policy={'source_sha256':'source','blocks':{},'structure':{'chapters':[{'title':'Chapter One','source_block':'a','start_page':2,'verified':True,'reason':'Original heading at top of page.'},{'title':'Chapter Two','source_block':'b','start_page':2,'verified':True,'reason':'Original later heading on same page.'}]}}
    p=plan(doc,{'document':policy})
    assert not p['issues'] and p['chapters']==['Opening','Chapter One','Chapter Two']
    assert next(u for u in p['utterances'] if 'ALPHA' in u['text'])['chapter']=='Chapter One'
    assert next(u for u in p['utterances'] if 'BETA' in u['text'])['chapter']=='Chapter Two'
