"""Bind saved categorical chart data to the shared table model."""

from dataclasses import dataclass
from decimal import Decimal
import re

from .core import Error
from . import drawing_text, table_grid


CHART = frozenset(('http://schemas.openxmlformats.org/drawingml/2006/chart',
                   'http://purl.oclc.org/ooxml/drawingml/chart'))
PLOTS = frozenset(('barChart','bar3DChart','lineChart','line3DChart','areaChart','area3DChart',
                   'pieChart','pie3DChart','doughnutChart','radarChart'))
NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]{1,3})?')
PERCENT = re.compile(r'[0#]+(?:\.[0#]+)?%')
PLAIN = re.compile(r'(?:#,##)?[0#]+(?:\.[0#]+)?')


def chart_name(node):
    if isinstance(node.tag,str) and node.tag.startswith('{'):
        namespace,local = node.tag[1:].split('}',1)
        if namespace in CHART:
            return local
    return ''


def nodes(parent,name):
    return [node for node in parent if chart_name(node)==name] if parent is not None else []


def one(parent,name):
    found = nodes(parent,name)
    if len(found)>1:
        problem('The saved chart has competing '+name+' entries.')
    return found[0] if found else None


def text(parent,name):
    node = one(parent,name)
    return (node.text or '').strip() if node is not None else ''


def problem(message):
    raise Error('source_chart',message)


@dataclass(frozen=True)
class Point:
    value: str
    path: str
    original: str


def number(value,code):
    if not NUMBER.fullmatch(value) or len(value)>50:
        problem('A saved chart point has an invalid numeric value.')
    amount = Decimal(value)
    if abs(amount.adjusted())>100:
        problem('A saved chart number exceeds the supported precision range.')
    if PERCENT.fullmatch(code):
        sign,digits,exponent = amount.as_tuple()
        return format(Decimal((sign,digits,exponent+2)),'f')+'%'
    if code not in ('','General') and not PLAIN.fullmatch(code):
        problem('A chart number uses a format that needs a verified reading.')
    return format(amount,'f')


def points(container,*,numeric=False):
    if container is None:
        problem('The chart has no saved point list for a series or category.')
    cache = [node for node in container.iter() if chart_name(node) in ('strCache','numCache','strLit','numLit')]
    if len(cache)!=1:
        problem('The chart has no unique saved point list for a series or category.')
    cache, = cache
    numeric = numeric or chart_name(cache) in ('numCache','numLit')
    declared = one(cache,'ptCount')
    if declared is None or declared.get('val') is None:
        problem('The saved chart point count is missing.')
    size = table_grid.count(declared.get('val'),maximum=table_grid.MAX_ROWS)
    values,seen = {},set()
    code = text(cache,'formatCode') if numeric else ''
    for point in nodes(cache,'pt'):
        position = point.get('idx')
        if position is None:
            problem('A saved chart point has no category position.')
        index = table_grid.count(position,minimum=0,maximum=size-1)
        if index in seen:
            problem('Two saved chart points claim the same category position.')
        seen.add(index)
        value = text(point,'v')
        if value:
            values[index] = Point(number(value,code) if numeric else value,
                                  point.getroottree().getpath(point),value)
    return size,values


def title(node):
    if node is None:
        return ''
    tx = one(node,'tx')
    rich = one(tx,'rich')
    if rich is not None:
        return ' '.join(value for _,value,_ in drawing_text.paragraphs(rich))
    if value:=text(tx,'v'):
        return value
    if tx is not None and one(tx,'strRef') is not None:
        _,values = points(tx)
        return ' '.join(point.value for _,point in sorted(values.items()))
    return ''


def series_label(series,index):
    return title(series) or f'Series {index+1}'


def chart_grid(root,location):
    if chart_name(root)!='chartSpace':
        problem('The slide chart relationship does not name a standard saved chart.')
    chart = one(root,'chart')
    area = one(chart,'plotArea')
    plots = [node for node in area if chart_name(node).endswith('Chart')] if area is not None else []
    if len(plots)!=1 or chart_name(plots[0]) not in PLOTS:
        problem('This chart layout needs a verified reading of its data series.')
    if nodes(area,'dateAx'):
        problem('The chart has a date axis that needs a verified date reading.')
    indexed = {}
    for series in nodes(plots[0],'ser'):
        order = one(series,'order')
        if order is None or order.get('val') is None:
            problem('A saved chart series has no authored display position.')
        index = table_grid.count(order.get('val'),minimum=0,maximum=table_grid.MAX_COLUMNS-2)
        if index in indexed:
            problem('Two chart series claim the same display position.')
        indexed[index] = series
    if not indexed:
        problem('The saved chart has no data series.')
    titles, data, category_points, labels = [],[],None,None
    for index,series in sorted(indexed.items()):
        label = series_label(series,index)
        count,categories = points(one(series,'cat'))
        size,values = points(one(series,'val'),numeric=True)
        if size!=count or len(categories)!=count:
            problem('Chart values do not match a complete list of named categories.')
        ordered = [categories[i].value for i in range(count)]
        if labels is not None and labels!=ordered:
            problem('The chart series use different categories.')
        if label in titles:
            problem('The chart series have the same saved name.')
        if not values:
            problem('A saved chart series has no readable values.')
        titles.append(label)
        data.append(values)
        if labels is None:
            labels,category_points = ordered,categories
    grid = table_grid.Grid()
    axes = [title(one(axis,'title')) for axis in nodes(area,'catAx')]
    category_label = next((value for value in axes if value),'Category')
    grid.begin_row()
    for label in (category_label,*titles):
        grid.place([table_grid.passage(label)],location,role='column')
    grid.end_row()
    for index,label in enumerate(labels):
        grid.begin_row()
        point = category_points[index]
        grid.place([table_grid.passage(label)],location|{'xml_path':point.path},role='row')
        for values in data:
            if point:=values.get(index):
                grid.place([table_grid.passage(point.value)],location|{
                    'xml_path':point.path,'cached_value':point.original
                })
            else:
                grid.column += 1
        grid.end_row()
    if caption:=title(one(chart,'title')):
        grid.caption = table_grid.passage(caption)
    return grid.finish()
