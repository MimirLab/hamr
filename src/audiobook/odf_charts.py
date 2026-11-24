"""Read authored ranges in an OpenDocument chart's embedded data table."""

import re

from .core import Error
from .odf_content import OFFICE, TABLE, TEXT
from .presentation_charts import number
from .table_grid import MAX_ROWS, Grid, passage


CHART = '{urn:oasis:names:tc:opendocument:xmlns:chart:1.0}'
CELL = re.compile(r'\$?([A-Z]{1,3})\$?([1-9][0-9]{0,4})')
PLOTS = frozenset(('bar','line','area','circle','ring','radar','filled-radar'))


def error(message):
    raise Error('source_chart',message)


def vertical(address,table):
    def cell(value):
        sheet,dot,coordinates = value.rpartition('.')
        match = CELL.fullmatch(coordinates)
        sheet = sheet.removeprefix('$')
        if sheet.startswith("'") and sheet.endswith("'"):
            sheet = sheet[1:-1].replace("''","'")
        if not dot or sheet not in ('',table) or not match:
            error('The chart range does not resolve to a cell in its saved data table.')
        row = int(match[2])-1
        column = 0
        for letter in match[1]:
            column = column*26+ord(letter)-ord('A')+1
        return row,column-1

    first,separator,last = (address or '').partition(':')
    start,column = cell(first)
    end,other = cell(last) if separator else (start,column)
    if other!=column or not start<=end<MAX_ROWS-1:
        error('The chart range does not describe one ordered series or category column.')
    return [(row,column) for row in range(start,end+1)]


def authored_title(node,content):
    return ' '.join(text for part in node.iter(TEXT+'p') if (text:=content.paragraph(part)[0])) if node is not None else ''


def chart_grid(root,content,location):
    nodes = list(root.iter(CHART+'chart'))
    if len(nodes)!=1:
        error('The embedded OpenDocument part has no unique chart.')
    chart, = nodes
    plot_type = chart.get(CHART+'class','').rsplit(':',1)[-1]
    plot = chart.find(CHART+'plot-area')
    if plot is None or plot_type not in PLOTS:
        error('The embedded chart needs a verified reading of its plotted data.')
    tables = chart.findall(TABLE+'table')
    if len(tables)!=1:
        error('The OpenDocument chart has no unique saved data table.')
    table, = tables
    name = table.get(TABLE+'name')
    grid = content.table(table,typed=True)
    if not name or grid['issue']:
        error(grid['issue'] or 'The chart data table has no authored name.')
    cells = {(cell['row'],cell['column']):cell for cell in grid['cells']
             if cell['rows']==cell['columns']==1}

    def column(address,*,numeric=False):
        result = []
        for coordinate in vertical(address,name):
            cell = cells.get(coordinate)
            if cell is None:
                error('A saved chart range extends outside its data table.')
            place = cell['location']
            if numeric and place.get('office_type'):
                raw = place.get('office_value','')
                if place['office_type'] not in ('float','percentage'):
                    error('A saved chart value has an unsupported numeric type.')
                value = number(raw,'0%' if place['office_type']=='percentage' else 'General')
                place = place|{'cached_value':raw}
            else:
                value = ' '.join(part.get('source','') for part in cell['parts']).strip()
                if numeric and value:
                    value = number(value,'General')
            result.append((value,location|place))
        return result

    xaxes = [axis for axis in plot.findall(CHART+'axis') if axis.get(CHART+'dimension')=='x']
    if len(xaxes)!=1 or xaxes[0].get(CHART+'axis-type')=='date':
        error('The OpenDocument chart has no single categorical axis.')
    categories = xaxes[0].find(CHART+'categories')
    if categories is None:
        error('The OpenDocument chart has no authored category range.')
    labels = column(categories.get(TABLE+'cell-range-address'))
    series = plot.findall(CHART+'series')
    if not series or any(not value for value,_ in labels):
        error('The chart lacks a complete category or series definition.')
    names,data = [],[]
    for item in series:
        if item.get(CHART+'class',plot_type).rsplit(':',1)[-1]!=plot_type:
            error('The chart contains more than one plotted data type.')
        label = column(item.get(CHART+'label-cell-address'))
        values = column(item.get(CHART+'values-cell-range-address'),numeric=True)
        if len(label)!=1 or not label[0][0] or any(name[0]==label[0][0] for name in names) or len(values)!=len(labels):
            error('The chart series do not match the saved categories and labels.')
        if not any(value for value,_ in values):
            error('A saved chart series has no readable values.')
        names.append(label[0])
        data.append(values)
    result = Grid()
    label = authored_title(xaxes[0].find(CHART+'title'),content) or 'Category'
    result.begin_row()
    for value,place in [(label,location),*names]:
        result.place([passage(value)],place,role='column')
    result.end_row()
    for row,(label,place) in enumerate(labels):
        result.begin_row()
        result.place([passage(label)],place,role='row')
        for values in data:
            value,place = values[row]
            if value:
                result.place([passage(value)],place)
            else:
                result.column += 1
        result.end_row()
    if caption:=authored_title(chart.find(CHART+'title'),content):
        result.caption = passage(caption)
    return result.finish()
