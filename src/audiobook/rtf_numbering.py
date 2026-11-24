"""Interpret the visible marker saved for an RTF list paragraph."""

import re

from .source_numbering import marker_component
from .spoken_forms import roman


FORMATS = {0:'decimal',1:'upperRoman',2:'lowerRoman',3:'upperLetter',4:'lowerLetter'}
MARKER = re.compile(r'\(?([+-]?[0-9]{1,9}|[A-Za-z]{1,15})[.)]?')
ISSUE = 'This RTF list has an automatic marker that needs a verified reading.'


def list_formats(wire):
    """Read the list/override mapping before document text begins."""
    stack = []
    definitions, instances = {},{}
    for kind,value,_,_ in wire:
        if kind=='open':
            stack.append({})
        elif kind=='close' and stack:
            record = stack.pop()
            if not stack:
                break
            name = record.get('name')
            if name=='listlevel':
                stack[-1].setdefault('levels',[]).append(record.get('levelnfcn',record.get('levelnfc')))
            elif name=='list' and (ident:=record.get('listid')) is not None:
                definitions[ident] = None if ident in definitions else record.get('levels')
            elif name=='listoverride' and (ident:=record.get('ls')) is not None:
                # Count overrides can start a fresh series. A format override
                # replaces the definition and needs a separate source reading.
                instances[ident] = None if ident in instances or record.get('format_override') else record.get('listid')
            elif name=='lfolevel' and record.get('listoverrideformat'):
                stack[-1]['format_override'] = True
        elif kind=='control' and stack:
            control,number = value
            record = stack[-1]
            parent = stack[-2].get('name') if len(stack)>1 else None
            if len(stack)==1 and control in ('pard','sectd','trowd','par'):
                break
            if ('name' not in record and (control in ('listtable','listoverridetable') and len(stack)==2
                    or (parent,control) in (('listtable','list'),('list','listlevel'),
                                            ('listoverridetable','listoverride'),('listoverride','lfolevel')))):
                record['name'] = control
            elif record.get('name') and control in ('listid','ls','levelnfc','levelnfcn','listoverrideformat'):
                record[control] = 1 if control=='listoverrideformat' and number is None else number
    return {instance:definitions.get(ident) for instance,ident in instances.items()}


def cached_marker(cached, instance, level, formats):
    if instance in (None,0) and cached is None:
        return None
    levels = formats.get(instance) or ()
    style = levels[level] if 0<=level<len(levels) else None
    if style in (23,255):
        return None
    text, start, end, destination = cached if cached else ('',None,None,None)
    location = {'byte_range':[start,end],'control':destination,'instance':instance,'level':level}
    if text and not any(char.isalnum() for char in text):
        return None
    match = MARKER.fullmatch(text)
    if match:
        symbol = match[1]
        if style==0 or style is None and destination=='pntext':
            number = int(symbol) if re.fullmatch(r'[+-]?[0-9]+',symbol) else None
            form = 'decimal'
        elif style in (1,2):
            number = roman(symbol.upper())
            form = FORMATS[style]
        elif style in (3,4) and len(set(symbol.lower()))==1:
            number = ord(symbol[0].upper())-64+26*(len(symbol)-1)
            form = FORMATS[style]
        else:
            number,form = None,None
        component = marker_component(number,form,word_letters=True) if number is not None else None
        if component and component[0]==symbol:
            label = 'Letter' if form.endswith('Letter') else 'Number'
            return number,{'notation':text,'reading':f'{label} {component[1]}, ',
                           'values':[number],'location':location}
    return None,{'issue':ISSUE,'notation':text,'location':location}
