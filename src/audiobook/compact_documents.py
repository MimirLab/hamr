"""Small local readers for older formats with explicit structural boundaries."""
import re
import shlex

from .core import Error

LINK = re.compile(r'@\{"([^"]*)"\s+([^}]+)}')
PRESENTATION = re.compile(r'@\{(?:b|ub|i|ui|u|uu|plain|code|lindent\s+[0-9]+)}',re.I)


def read_tcr(data,builder):
    if not data.startswith(b'!!8-Bit!!'):
        raise Error('unreadable_tcr','The selected TCR book lacks its format signature.')
    index = 9
    dictionary = []
    for _ in range(256):
        if index>=len(data) or index+1+data[index]>len(data):
            raise Error('unreadable_tcr','The TCR substitution table is incomplete.')
        length = data[index]
        dictionary.append(data[index+1:index+1+length])
        index += 1+length
    output = bytearray()
    for code in memoryview(data)[index:]:
        value = dictionary[code]
        if len(output)+len(value)>16*1024**2:
            raise Error('source_structure_size','The expanded TCR text exceeds the bounded reading limit.')
        output.extend(value)
    # TCR carries no encoding marker. Prefer UTF-8 when it is valid.
    try:
        text = output.decode('utf-8-sig')
        encoding = 'utf-8'
    except UnicodeDecodeError:
        text = output.decode('cp1252')
        encoding = 'cp1252'
    builder.doc.metadata['source_encoding'] = encoding
    for n,part in enumerate(re.split(r'\n[ \t\r]*\n',text),1):
        builder.add(part,where=f'tcr:{n}')


def read_guide(data,builder):
    try:
        lines = data.decode('utf-8-sig').splitlines()
    except UnicodeDecodeError:
        lines = data.decode('latin-1').splitlines()
    if not lines or not lines[0].lower().startswith('@database '):
        raise Error('unreadable_guide','The file does not start with an AmigaGuide database declaration.')
    sections = {}
    current = None
    for index,line in enumerate(lines,1):
        if line.lstrip().startswith('@') and not line.lstrip().startswith('@{'):
            try:
                fields = shlex.split(line,comments=False)
            except ValueError as exc:
                raise Error('unreadable_guide',f'Incomplete AmigaGuide command at line {index}.') from exc
            command = fields[0].lower()
            if command=='@node':
                if current or len(fields)<2 or fields[1].casefold() in sections:
                    raise Error('unreadable_guide',f'Invalid or repeated AmigaGuide node at line {index}.')
                current = fields[1].casefold()
                sections[current] = {'title':' '.join(fields[2:]) or fields[1],
                                     'line':index,'lines':[],'next':None}
            elif command=='@endnode':
                current = None
            elif current and command=='@next' and len(fields)>=2:
                sections[current]['next'] = fields[1].casefold()
            elif current and command=='@title' and len(fields)>=2:
                sections[current]['title'] = ' '.join(fields[1:])
            elif current and fields[0].startswith('@'):
                sections[current].setdefault('directives',[]).append({'line':index,'source':line})
        elif current is not None:
            sections[current]['lines'].append(line)
    if current or 'main' not in sections:
        raise Error('unreadable_guide','The guide needs a completed MAIN node.')
    authored = list(sections)
    seen = set()
    cursor = 'main'
    while cursor and cursor in sections and cursor not in seen:
        seen.add(cursor)
        selected = sections[cursor]
        links = []

        def label(match):
            try:
                command = shlex.split(match[2])
            except ValueError:
                command = []
            links.append({'label':match[1],'command':command})
            return match[1]

        text = LINK.sub(label,'\n'.join(selected['lines']))
        text = PRESENTATION.sub('',text)
        text = re.sub(r'@\{par}',r'\n\n',text,flags=re.I)
        builder.add(selected['title'],'heading',f"line:{selected['line']}",loc={'node':cursor})
        if '@{' in text:
            builder.issue('Guide contains an unresolved inline directive.',f'node:{cursor}')
        else:
            for n,paragraph in enumerate(re.split(r'\n\s*\n',text),1):
                builder.add(paragraph,where=f'node:{cursor}:{n}',loc={'node':cursor})
        builder.doc.inventory.append({'node':cursor,'line':selected['line'],'status':'selected',
                                       'logical_links':links,'retained_directives':selected.get('directives',[])})
        ordinal = authored.index(cursor)
        cursor = selected['next'] or (authored[ordinal+1] if ordinal+1<len(authored) else None)
    if cursor is not None:
        builder.issue('The AmigaGuide browse order names an external, missing, or repeated node.',
                      f'node:{cursor}')
    if len(seen)!=len(sections):
        pending = sorted(sections.keys()-seen)
        builder.issue('The AmigaGuide browse order omits nodes: '+', '.join(pending),'guide')
