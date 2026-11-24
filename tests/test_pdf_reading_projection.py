from dataclasses import replace

from audiobook.core import Block, Document
from audiobook.planner import plan
from audiobook.source_passages import SourceReadings


def raised_script():
    source = 'We saw a dis-\ncretionary rise of x2.'
    split = 'dis-\ncretionary'
    wrap_start = source.index(split)
    formula_start = source.index('x2')
    location = {
        'page':1,
        'line_wrap_joins':[{'original':split,'spoken':'discretionary','kind':'discretionary',
                            'source_span':[wrap_start,wrap_start+len(split)],
                            'native_region_span':[wrap_start,wrap_start+len(split)],
                            'evidence':'Verified native line wrap.'}],
        'pdf_inline_formula':{
            'source':source,
            'reading':source.replace('x2','x to the power of 2'),
            'formulas':[{'span':[formula_start,formula_start+2],'script':[formula_start+1,formula_start+2],
                         'source':'x2','reading':'x to the power of 2'}],
        },
    }
    return Block('body',source,location)


def test_printed_wrap_and_script_use_the_same_exact_projection():
    block = raised_script()
    text,protected = SourceReadings(block).paragraph('after_paragraph',modern=True,contextual=True,
                                                     unified=True,projected=True)
    assert text=='We saw a discretionary rise of x to the power of 2.'
    assert [text[a:b] for a,b in protected]==['x to the power of 2']
    default = plan(Document('Geometric script','pdf','source-digest',[block]),{'mode':'study'})
    utterance, = default['utterances']
    assert utterance['text']==text
    assert utterance['performance_candidate']['recipe']=='technical'
    observation = next(e for e in default['decisions'][0]['context_structure'] if e['kind']=='superscript')
    start,end = observation['source_span']
    assert block.source[start:end]=='x2'
    assert block.source[slice(*observation['fields']['script'])]=='2'
    assert 'dis-\ncretionary' in block.source


def test_printed_script_with_stale_coordinates_does_not_fall_back_to_raw_digits():
    block = raised_script()
    stale = replace(block,source='First. '+block.source)
    result = plan(Document('Shifted source','pdf','source-digest',[stale]))
    assert not result['utterances']
    assert result['issues'][0]['block']==stale.id
    assert 'no longer matches its source span' in result['issues'][0]['reason']


def test_typed_table_cell_must_own_its_original_row_and_column():
    rows = [['Quantity','Value'],['Example','2026-11-23']]
    location = {
        'page':1,'rows':rows,'panel_row':1,
        'pdf_table_cells':[{'native_ranges':[[4,11]]},{'native_ranges':[[12,22]]}],
        'pdf_cell_readings':[{'row':1,'column':1,'source':'2026-11-23','reading':'the serial number',
                              'event_id':'CR-N035','kind':'formula_notation','fields':{}}],
    }
    block = Block('table','Example | 2026-11-23',location,'table')
    good = plan(Document('Serial number','pdf','source-digest',[block]))
    assert good['utterances'][0]['text']=='Table. Quantity: Example; Value: the serial number.'
    evidence = next(e for e in good['decisions'][0]['context_structure'] if e['kind']=='typed_cell')
    assert evidence['source']=='2026-11-23' and evidence['fields']['native_ranges']==[[12,22]]
    location['pdf_cell_readings'][0]['column'] = 0
    moved = plan(Document('Shifted source','pdf','source-digest',[block]))
    assert not moved['utterances']
    assert 'no longer matches its source span or table cell' in moved['issues'][0]['reason']
