"""Bind qualified PDF readings to the shared paragraph and table spans."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    provider: str
    kind: str
    event: str
    reading_kind: str
    fields: dict
    related: tuple = ()

    def selection(self, **fields):
        return self.event,self.reading_kind,{**self.fields,**fields},self.related


def span(source, start, end, reading, *, kind='formula'):
    return {'kind':kind,'source_start':start,'source_end':end,'source':source[start:end],'reading':reading}


def paragraph(block):
    original = block.source
    loc = block.locator
    if (equation := loc.get('pdf_equation')) and equation['fields']['notation']==original:
        fields = {**equation['fields'],'native_ranges':loc['native_ranges'],
                  'panel_row':loc['panel_row'],'panel_column':loc['panel_column']}
        related = ('CR-N051',) if fields['value'].get('exponent') else ()
        yield (span(original,0,len(original),equation['reading']),
               Observation('pdf_equation_cell','source_equation','CR-N035','equation',fields,related))
    if (inline := loc.get('pdf_inline_formula')) and inline['source']==original:
        previous_end = 0
        for item in inline['formulas']:
            start,end = item['span']
            script_start,script_end = item['script']
            if not (previous_end<=start<=script_start<script_end<=end<=len(original)
                    and original[start:end]==item['source']):
                continue
            fields = {'script':item['script'],'reading':item['reading']}
            yield (span(original,start,end,item['reading']),
                   Observation('pdf_inline','superscript','CR-L030','mathematical_superscript',fields))
            previous_end = end


def cells(block):
    loc = block.locator
    rows = loc.get('rows',())
    geometry = loc.get('pdf_table_cells',())
    seen = set()
    for cell in loc.get('pdf_cell_readings',()):
        row,column = cell['row'],cell['column']
        if (row,column) in seen or not (0<=row<len(rows) and 0<=column<min(len(rows[row]),len(geometry))):
            continue
        original = rows[row][column]
        if cell['source']!=original:
            continue
        fields = {**cell['fields'],'panel_row':loc['panel_row'],'native_ranges':geometry[column]['native_ranges']}
        related = ('CR-N051',) if cell['event_id']=='CR-N034' and fields.get('exponent') else ()
        annotation = {'row':row,'column':column,'source':original,'notes':[],
                      'spans':[span(original,0,len(original),cell['reading'],kind='typed_reading')]}
        yield annotation,Observation('pdf_table_cell','typed_cell',cell['event_id'],cell['kind'],fields,related)
        seen.add((row,column))
