"""Read presentations, workbooks and authored outlines as source structures."""
from datetime import date,timedelta
from decimal import Decimal, InvalidOperation
import posixpath
import re
from urllib.parse import unquote, urlsplit

from .core import Error
from .document_intake import attr, name, plain_xml_text, structured_zip, xml
from .source_bundle import safe_name

OFFICE_RELATION = frozenset((
    'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'http://purl.oclc.org/ooxml/officeDocument/relationships',
))


def relation_id(node):
    return next((v for n,v in node.attrib.items() if n.endswith('}id') and n[1:-3] in OFFICE_RELATION),None)


def relations(read,part):
    dirname,base = posixpath.split(part)
    payload = read(posixpath.join(dirname,'_rels',base+'.rels'),required=False)
    result = {}
    if payload:
        for item in xml(payload):
            if attr(item,'TargetMode')=='External':
                continue
            uri = urlsplit(attr(item,'Target') or '')
            if uri.scheme or uri.netloc or uri.query or uri.fragment:
                raise Error('unsafe_source_member','An internal package relationship refers outside its package.')
            target = unquote(uri.path)
            target = (target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(dirname,target)))
            result[attr(item,'Id')] = (safe_name(target), (attr(item,'Type') or '').rsplit('/',1)[-1])
    return result


def cell_col(column):
    value = 0
    for letter in column:
        value = value*26+ord(letter)-ord('A')+1
    return value-1


def cells_text(node):
    return ''.join(n.text or '' for n in node.iter() if name(n)=='t')


def read_xlsx(path,builder):
    with structured_zip(path) as (read,_):
        book = xml(read('xl/workbook.xml'))
        rels = relations(read,'xl/workbook.xml')
        strings = read('xl/sharedStrings.xml',required=False)
        shared = [cells_text(s) for s in xml(strings) if name(s)=='si'] if strings else []
        styles_xml = read('xl/styles.xml',required=False)
        styles = []
        if styles_xml:
            style_data = xml(styles_xml)
            custom = {int(attr(n,'numFmtId')):attr(n,'formatCode') for n in style_data.iter() if name(n)=='numFmt'}
            cell_formats = next((n for n in style_data if name(n)=='cellXfs'),())
            styles = [(index,custom.get(index)) for x in cell_formats for index in (int(attr(x,'numFmtId') or 0),)]
        date_1904 = any(name(n)=='workbookPr' and attr(n,'date1904') in ('1','true') for n in book)
        for sheet in (x for x in book.iter() if name(x)=='sheet'):
            label = attr(sheet,'name') or 'Worksheet'
            binding = rels.get(relation_id(sheet))
            if not binding or binding[1]!='worksheet':
                builder.issue(f'Worksheet {label} is missing a supported package relationship.',label)
                continue
            part = binding[0]
            if attr(sheet,'state') in ('hidden','veryHidden'):
                builder.doc.inventory.append({'sheet':label,'part':part,'status':'hidden by source'})
                continue
            node = xml(read(part))
            builder.add(label,'heading',part)
            rows = []
            stored = []
            issues = set()
            if any(name(n)=='mergeCell' for n in node.iter()):
                issues.add('Merged worksheet cells need a verified reading.')
            if any(name(n) in ('drawing','legacyDrawing','oleObjects') for n in node.iter()):
                issues.add('Worksheet graphics or linked objects need a verified reading.')
            hidden_columns = set()
            for column in (n for n in node.iter() if name(n)=='col' and attr(n,'hidden') in ('1','true')):
                a,b = (int(attr(column,field)) for field in ('min','max'))
                if not 1<=a<=b<=16384:
                    raise Error('source_structure_size','A worksheet column range is invalid.')
                hidden_columns.update(range(a-1,b))
            for row in (part for part in node.iter() if name(part)=='row'):
                if attr(row,'hidden') in ('1','true'):
                    builder.doc.inventory.append({'part':part,'row':attr(row,'r'),'status':'hidden by source'})
                    continue
                values = {}
                for cell in (n for n in row if name(n)=='c'):
                    address = attr(cell,'r') or ''
                    match = re.fullmatch(r'([A-Z]{1,3})[1-9][0-9]*',address)
                    if not match:
                        issues.add('A worksheet cell has an invalid source address.')
                        continue
                    column = cell_col(match[1])
                    if column in hidden_columns:
                        continue
                    cell_type = attr(cell,'t')
                    value = next((part.text for part in cell if name(part)=='v'),None)
                    formula = next((part.text or '' for part in cell if name(part)=='f'),None)
                    if formula is not None:
                        stored.append({'cell':address,'formula':formula,'cached_value':value})
                        if value is None:
                            issues.add('A worksheet formula lacks a saved result.')
                    if cell_type=='inlineStr':
                        value = cells_text(cell)
                    elif cell_type=='s' and value is not None:
                        try:
                            index = int(value)
                            if not 0<=index<len(shared):
                                raise IndexError
                            value = shared[index]
                        except (IndexError,ValueError):
                            issues.add('A worksheet cell refers to a missing shared string.')
                            continue
                    elif cell_type=='b' and value in ('0','1'):
                        value = 'true' if value=='1' else 'false'
                    elif cell_type=='e':
                        issues.add('A worksheet contains a saved cell error.')
                    elif value is not None and cell_type in (None,'n') and attr(cell,'s'):
                        try:
                            style = int(attr(cell,'s'))
                            if not 0<=style<len(styles):
                                raise IndexError
                            kind,custom = styles[style]
                            # Time and ambiguous custom date/number formats are
                            # retained verbatim until a safe speech rule exists.
                            if kind in (14,15,16,17):
                                serial = Decimal(value)
                                if (serial!=int(serial) or serial<0 or serial==60 and not date_1904):
                                    raise ValueError
                                base = date(1904,1,1) if date_1904 else date(1899,12,31)
                                adjustment = int(not date_1904 and serial>60)
                                value = (base+timedelta(days=int(serial)-adjustment)).isoformat()
                            elif kind not in (0,1,2,3,4,11,48,49) or custom:
                                issues.add('A worksheet number uses a display format requiring a verified reading.')
                        except (ValueError,OverflowError,IndexError,InvalidOperation):
                            issues.add('A worksheet value could not be interpreted using its saved number format.')
                    if value is not None and value.strip():
                        values[column] = value
                if values:
                    rows.append(values)
            columns = sorted({column for row in rows for column in row})
            table = [[row.get(column,'') for column in columns] for row in rows]
            if table:
                builder.table(table,part,headers=[],issue=' '.join(sorted(issues)) or None)
            elif issues:
                builder.issue(' '.join(sorted(issues)),part)
            builder.doc.inventory.append({'sheet':label,'part':part,'status':'selected',
                                          'rows':len(rows),'columns':columns,'cached_formulas':stored})


def read_outline(data,builder):
    root = xml(data)
    tag = name(root)
    if tag=='opml':
        title = next((plain_xml_text(n) for n in root.iter() if name(n)=='title'),None)
        if title:
            builder.doc.title = title
        for node in (n for n in root.iter() if name(n)=='outline'):
            title = attr(node,'text') or attr(node,'title')
            if title:
                depth = sum(name(parent)=='outline' for parent in node.iterancestors())
                builder.add(title,'heading' if depth==0 else 'body',node.getroottree().getpath(node),
                            loc={'outline_depth':depth})
        return
    if tag=='svg':
        for node in (n for n in root.iter() if name(n) in ('title','desc','text') and
                     not any(name(parent)=='defs' for parent in n.iterancestors())):
            builder.add(plain_xml_text(node),'heading' if name(node)=='title' else 'body',
                        node.getroottree().getpath(node))
        if not any(name(node)=='desc' for node in root.iter()) and any(
                name(node) in ('path','image','polygon','rect','circle') for node in root.iter()):
            builder.issue('The SVG has unlabeled visual content requiring source review.','svg')
        return
    raise Error('unsupported_format',f'Unsupported outline type: {tag}.')
