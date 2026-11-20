import pytest

from audiobook.core import Block,Document,Error,digest
from audiobook.pdf_layout import mark_folios,paragraphs
from audiobook.planner import plan


def source_document():
    return Document('PDF document','pdf','source',[Block('h','1 Clear Heading',{'format':'pdf','page':3,'rect':[10,600,100,650]}),Block('b','Only use 0.08 mg. Do not restart.',{'format':'pdf','page':3,'rect':[10,500,400,550]},issue='Low-confidence numeric OCR requires verification.')])


def test_source_bound_structure_and_exact_verified_reading_preserve_companion_source():
    d=source_document();policy={'source_sha256':'source','structure':{'title':{'text':'Clear Heading','source_blocks':['h'],'verified':True,'reason':'Visible source title.'},'chapters':[{'start_page':3,'title':'1 Clear Heading','source_block':'h','verified':True,'reason':'Visible heading and contents agree.'}]},'blocks':{'b':{'action':'speak','expected_source_sha256':digest(d.blocks[1].source.encode()),'verified':True,'reason':'Checked visible value and negation.'}}}
    p=plan(d,{'document':policy})
    assert p['document_title']=='Clear Heading' and p['chapters']==['1 Clear Heading']
    assert not p['issues'] and '0.08 milligrams. Do not restart.' in ' '.join(u['text'] for u in p['utterances'])
    assert p['decisions'][1]['source']==d.blocks[1].source
    policy['blocks']['b']['expected_source_sha256']='wrong'
    with pytest.raises(Error,match='exact extracted source'):plan(d,{'document':policy})


def test_invented_heading_is_rejected():
    d=source_document();policy={'source_sha256':'source','structure':{'chapters':[{'start_page':3,'title':'A different heading','source_block':'h','verified':True,'reason':'Claimed verification.'}]}}
    with pytest.raises(Error,match='does not match'):plan(d,{'document':policy})


def test_folio_geometry_and_recurrence_do_not_remove_body_number():
    blocks=[Block(str(n),str(n-1),{'format':'pdf','page':n,'paragraph':0,'rect':[250,20,270,30]}) for n in range(2,7)]
    body=Block('body','4',{'format':'pdf','page':5,'paragraph':1,'rect':[250,400,270,430]});blocks.append(body)
    mark_folios(blocks,[{'page':n,'size':[600,800]} for n in range(2,7)])
    assert all(b.role=='furniture' for b in blocks[:-1]) and body.role=='body'


def test_only_native_soft_wrap_evidence_joins_a_visual_line_break():
    def word(line,text):return ['5','1','1','1',str(line),'1','20',str(40*line),'100','20','95',text]
    rows=paragraphs([word(1,'under-'),word(2,'standing')],'under¬\nstanding',1,600,800)
    assert rows[0]['text']=='under-\nstanding'
    b=Block('b',rows[0]['text'],rows[0]['locator'])
    p=plan(Document('D','pdf','s',[b]));assert p['utterances'][0]['text']=='understanding'
    rows=paragraphs([word(1,'long-'),word(2,'term')],'long-term',1,600,800)
    assert not rows[0]['locator'].get('line_wrap_joins')
