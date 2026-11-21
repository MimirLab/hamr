import pytest

from audiobook.core import Block,Document,Error
from audiobook.pdf_formula import formula_suffix,read_formula
from audiobook.pdf_table_readings import typed_equation
from audiobook.planner import plan


def test_displaced_bar_stays_on_its_geometric_owner():
    text='Measurement packet h/e ¯2'
    start=text.index('h/e')
    end=len(text)
    cell={'text':text,'scripts':[{'kind':'superscript','span':[end-1,end]}],
          'math_font_spans':[[start,start+3]],
          'math_marks':[{'kind':'bar','span':[end-2,end-1],'owner':[start,start+1]}]}
    assert formula_suffix(cell)==(start,'H bar slash E squared')
    assert formula_suffix({**cell,'math_marks':[]}) is None
    assert read_formula({'text':'(2(x + 1)','scripts':[]}) is None


def test_equation_units_use_their_own_position_after_a_raised_exponent():
    source='(1 Hz)h = 4.135 667 696 . . . × 10−15eV'
    start=source.index('−15')
    cell={'text':source,'scripts':[{'kind':'superscript','span':[start,start+3]}]}
    event=typed_equation(cell,None,{})
    assert event['fields']['left']=='(1 Hz)h'
    assert event['fields']['value']['coefficient']=='4.135667696'
    assert event['fields']['value']['exponent']=='−15'
    assert event['fields']['value']['continuing_digits']
    assert event['fields']['unit']['units']==[{'symbol':'eV','exponent':None}]
    assert event['reading'].endswith('electron volt.')
    assert typed_equation({'text':source,'scripts':[]},None,{}) is None
    assert event['reading'].startswith('open parenthesis one hertz close parenthesis H equals')


def test_script_ends_before_an_adjacent_factor_but_isotope_prefix_stays_attached():
    cell={'text':'mec','scripts':[{'kind':'subscript','span':[1,2]}],'math_font_spans':[[0,1],[2,3]]}
    assert read_formula(cell)=='M subscript E times C'
    isotope={'text':'(12C)','scripts':[{'kind':'superscript','span':[1,3]}]}
    assert read_formula(isotope)=='open parenthesis superscript twelve capital C close parenthesis'


def test_verified_cell_glyph_alias_recompiles_only_the_named_symbol():
    headers=['Quantity','Symbol','Numerical value','Unit']
    values=['Sample','ǫ','1.23','m']
    source=' | '.join(values)
    block=Block('row',source,{'format':'pdf','page':1,'rows':[headers,values],'panel_row':1,
                'pdf_table_cells':[{'text':value,'scripts':[],'native_ranges':[]} for value in values],
                'pdf_conventions':{'uncertainty':None,'units':{}}},'table','Test',[],
                'Scientific notation in this table row requires a verified reading.')
    doc=Document('Glyph control','pdf','source-digest',[block])
    fix={'column':1,'offset':0,'source':'ǫ','reading':'ε','reason':'Verified glyph encoding discrepancy','verified':True}
    policy={'document':{'source_sha256':doc.source_sha256,'pdf_glyph_aliases':{block.id:[fix]}}}
    assert len(plan(doc)['issues'])==1
    result=plan(doc,policy)
    assert not result['issues'] and 'Symbol: epsilon' in result['utterances'][0]['text']
    assert block.source==source and block.locator['rows'][1][1]=='ǫ' and block.issue
    symbol=next(item for item in result['decisions'][0]['context_structure'] if item.get('event_id')=='CR-L030')
    assert symbol['source']=='ǫ' and symbol['fields']['glyph_aliases']==[fix]
    fix['offset']=1
    with pytest.raises(Error,match='one exact source letter'):
        plan(doc,policy)
    fix['offset']=0
    block.issue='A meaningful glyph crosses the visible CropBox edge.'
    with pytest.raises(Error,match='unresolved scientific table row'):
        plan(doc,policy)
