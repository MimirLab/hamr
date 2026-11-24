from zipfile import ZipFile

from audiobook.intake import inspect_source
from audiobook.planner import plan


def pdf_objects(objects):
    document = bytearray(b'%PDF-1.7\n')
    offsets = ['0000000000 65535 f ']
    for number,body in enumerate(objects,1):
        offsets.append(f'{len(document):010d} 00000 n ')
        document.extend(f'{number} 0 obj\n{body}\nendobj\n'.encode())
    index = len(document)
    document.extend((f'xref\n0 {len(offsets)}\n'+'\n'.join(offsets)+
                     f'\ntrailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{index}\n%%EOF\n').encode())
    return document


def table_pdf(*,missing_owner=False,invalid_scope=False,form_collision=False):
    objects = ['', '', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>','', '']

    def obj(value=''):
        objects.append(value)
        return len(objects)

    def text(value,x,y,ident=None):
        mark = f'/Span <</MCID {ident}>> BDC ' if type(ident) is int else '/Artifact BMC ' if ident else ''
        return mark+f'BT /F1 12 Tf {x} {y} Td ({value}) Tj ET'+(' EMC' if mark else '')

    def stream(parts,attributes=''):
        data = '\n'.join(parts)
        return obj(f'<< {attributes} /Length {len(data)} >>\nstream\n{data}\nendstream')

    first, next_page = obj(),obj()
    children = [text('The harbor report follows.',55,775),text('Harbor',55,730,0),
                text('Condition',245,730,1),text('Alder',55,700,2),text('Clear',245,700,3)]
    xobjects = ''
    if form_collision:
        form = stream([text('Form-owned words survive.',55,630,0)],
                      '/Type /XObject /Subtype /Form /BBox [0 0 600 800] /Resources << /Font << /F1 3 0 R >> >>')
        xobjects = f'/XObject << /X {form} 0 R >>'
        children.append('q /X Do Q')
    first_stream = stream(children)
    next_stream = stream([text('Harbor',55,750,'artifact'),text('Condition',245,750,'artifact'),
                          text('Birch',55,715,0),text('Choppy',245,715,7 if missing_owner else 1),
                          text('These readings are provisional.',55,680,2),text('The watch ended.',55,640)])
    for number,glyphs,sequence in ((first,first_stream,0),(next_page,next_stream,1)):
        objects[number-1] = (f'<< /Type /Page /Parent 2 0 R /StructParents {sequence} /MediaBox [0 0 595 842]'
                             f' /Contents {glyphs} 0 R /Resources << /Font << /F1 3 0 R >> {xobjects} >> >>')
    objects[0] = '<< /Type /Catalog /Pages 2 0 R /MarkInfo << /Marked true >> /StructTreeRoot 4 0 R >>'
    objects[1] = f'<< /Type /Pages /Count 2 /Kids [{first} 0 R {next_page} 0 R] >>'
    table = obj()
    objects[3] = f'<< /Type /StructTreeRoot /K {table} 0 R /ParentTree 5 0 R >>'

    def row(page,cells):
        node = obj()
        gen = []
        for kind,ident,attr in cells:
            gen.append(obj(f'<< /Type /StructElem /S /{kind} /P {node} 0 R /Pg {page} 0 R /K {ident} {attr} >>'))
        objects[node-1] = f'<< /Type /StructElem /S /TR /P {table} 0 R /K [{refs(gen)}] >>'
        return node,gen

    def refs(n):
        return ' '.join(f'{index} 0 R' for index in n)

    scope = '/A << /O /Table /Scope /Column >>'
    faulty = '/A << /O /Table /Scope /Unknown >>' if invalid_scope else scope
    head,headers = row(first,[('TH',0,faulty),('TH',1,scope)])
    a,one = row(first,[('TD',2,''),('TD',3,'')])
    b,two = row(next_page,[('TD',0,''),('TD',1,'')])
    caption = obj(f'<< /Type /StructElem /S /Caption /P {table} 0 R /Pg {next_page} 0 R /K 2 >>')
    objects[table-1] = f'<< /Type /StructElem /S /Table /P 4 0 R /K [{refs((head,a,b,caption))}] >>'
    objects[4] = f'<< /Nums [0 [{refs(headers+one)}] 1 [{refs(two+[caption])}]] >>'
    return pdf_objects(objects)


def test_pdf_table_rejoins_pages_and_keeps_caption_and_header_artifacts(tmp_path):
    path = tmp_path/'report.pdf'
    path.write_bytes(table_pdf())
    archive_path = tmp_path/'report.zip'
    with ZipFile(archive_path,'w') as archive:
        archive.write(path,'reports/source.pdf')
    for source in (path,archive_path):
        doc = inspect_source(source)
        table, = [b for b in doc.blocks if b.role=='table']
        grid = table.locator['table_grid']
        assert (grid['width'],grid['height'])==(2,3)
        assert table.locator['tagged_table']['pages']==[1,2]
        assert grid['cells'][-1]['parts'][0]['location']['page']==2
        assert [b.source for b in doc.blocks if b.role=='furniture']==['Harbor','Condition']
        assert grid['caption']['after']
        result = plan(doc)
        assert not result['issues']
        assert ' '.join(u['text'] for u in result['utterances'])==(
            'The harbor report follows. Table. Harbor: Alder. Condition: Clear. '
            'Harbor: Birch. Condition: Choppy. These readings are provisional. The watch ended.')


def test_a_pdf_table_with_bound_words_but_conflicting_scope_can_be_repaired(tmp_path):
    source = tmp_path/'bad-scope.pdf'
    source.write_bytes(table_pdf(invalid_scope=True))
    doc = inspect_source(source)
    block, = [b for b in doc.blocks if b.role=='table']
    assert 'Alder' in block.source and 'Choppy' in block.source
    assert block.locator['tagged_table']['pages']==[1,2]
    issue, = plan(doc)['issues']
    assert issue['block']==block.id and 'heading lacks a verified' in issue['reason']


def test_an_incomplete_or_colliding_pdf_tag_leaves_all_visible_text(tmp_path):
    for options,extra in (({'missing_owner':True},''),({'form_collision':True},'Form-owned words survive.')):
        source = tmp_path/'fallback.pdf'
        source.write_bytes(table_pdf(**options))
        doc = inspect_source(source)
        assert all(block.role!='table' for block in doc.blocks)
        assert doc.inventory[0]['tagged_table_observations'][0]['status']=='geometry_fallback'
        visible = ' '.join(b.source for b in doc.blocks)
        assert all(word in visible for word in ('Alder','Birch','Clear','Choppy','The watch ended.',extra))
        assert any('could not be bound' in message for message in doc.warnings)
