import io,zipfile
from pathlib import Path
import pytest
from audiobook.importers import markdown,epub,pdf
from audiobook.planner import plan,normalize,split
from audiobook.core import Error

@pytest.mark.parametrize('literal', ['2.4.10','0.08','-2.5','< 8 kPa','>= 12 kPa','03/04/2026','--no-cache','not 0.80','Do not restart','12 mg','+4.5 °C','https://example.test/a','/tmp/a b','0.0001','1,024','10–12','≤ 0.08','C++'])
def test_annotated_literals(literal):
    d=markdown(('# Test\n\nPreserve '+literal+' exactly.\n').encode()); p=plan(d)
    expected={'< 8 kPa':'less than 8 kilopascals','>= 12 kPa':'greater than or equal to 12 kilopascals','10–12':'10 to 12','≤ 0.08':'less than or equal to 0.08','03/04/2026':'zero three slash zero four slash two zero two six',
              'not 0.80':'not zero point eight zero','+4.5 °C':'plus four point five degrees Celsius'}.get(literal,literal)
    assert expected in ' '.join(u['text'] for u in p['utterances'])
    assert literal in d.blocks[1].source

def test_whitespace_emphasis_version():
    d=markdown(b'# Test\n\nUse **2**.4.10, not 0.80.\n'); assert d.blocks[1].source=='Use 2.4.10, not 0.80.'

def test_notes_table_commands():
    d=markdown(Path('fixtures/technical.md').read_bytes()); p=plan(d)
    speech=' '.join(u['text'] for u in p['utterances'])
    assert 'Do not restart until pressure is below 8 kilopascals.' in speech
    assert 'Condition: Pressure greater than or equal to 12 kilopascals; Required action: Stop and roll back' in speech
    code=next(b for b in d.blocks if b.role=='code')
    assert './upgrade --no-cache --version 2.4.10' in code.locator['exact']
    assert 'open parenthesis sigh close parenthesis' in speech
    assert len(p['decisions'])==len(d.blocks)

def make_epub(tmp_path=None):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        z.writestr('OEBPS/book.opf','<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Repeated book</dc:title><dc:language>en</dc:language></metadata><manifest><item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/><item id="n" href="notes.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/><itemref idref="a"/><itemref idref="n" linear="no"/></spine></package>')
        z.writestr('OEBPS/a.xhtml','<html xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 id="start">Alpha</h1><p id="claim">Do not restart.<a epub:type="noteref" href="notes.xhtml#n1">1</a></p></body></html>')
        z.writestr('OEBPS/b.xhtml','<html><body><h1>Beta</h1><p>Keep 0.08 mg.</p></body></html>')
        z.writestr('OEBPS/notes.xhtml','<html><body><aside id="n1">Only after cooling below 8 degrees.</aside></body></html>')
    return b.getvalue()

def test_repeated_epub_and_nonlinear_note():
    d=epub(make_epub()); assert [b.chapter for b in d.blocks if b.role=='heading']==['Alpha','Beta','Alpha']
    refs=[b for b in d.blocks if 'Do not restart' in b.source]
    assert len(refs)==2 and refs[0].id!=refs[1].id
    assert all('Only after cooling below 8 degrees.' in b.links[0]['text'] for b in refs)
    assert all('Only after cooling below 8 degrees.' not in b.source for b in refs)
    assert [b.locator['occurrence'] for b in refs]==[1,3]

def test_archive_traversal_and_expansion():
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z: z.writestr('../evil','no')
    with pytest.raises(Error,match='escapes'): epub(b.getvalue())

def test_merged_table_abstains():
    from audiobook.core import Block,Document
    d=Document('Table','epub','x',[Block('b1','threshold',{'rows':[['A','B'],['12']]},role='table')]); assert plan(d)['issues']

def test_split_preserves_tokens():
    text=' '.join(['Do not use version 2.4.10 at 0.08 mg.']*50)
    out=split(text); assert ' '.join(out)==text; assert all(len(p.split())<=65 for p in out)

def test_pdf_two_columns():
    from reportlab.pdfgen.canvas import Canvas
    b=io.BytesIO(); c=Canvas(b,pagesize=(600,800))
    for i in range(5):
        c.drawString(40,700-i*20,f'Left step {i+1}.'); c.drawString(330,700-i*20,f'Right step {i+1}.')
    c.showPage(); c.save(); d=pdf(b.getvalue())
    speech=' '.join(b.source for b in d.blocks)
    assert speech.index('Left step 5')<speech.index('Right step 1')
    assert d.inventory[0]['route']=='geometric_two_columns'

def test_pdf_blank_is_not_unreadable():
    from reportlab.pdfgen.canvas import Canvas
    b=io.BytesIO(); c=Canvas(b); c.showPage(); c.drawString(30,700,'Readable second page.'); c.showPage(); c.save(); d=pdf(b.getvalue())
    assert d.inventory[0]['route']=='verified_blank'; assert all(x.locator['page']!=1 for x in d.blocks)

def test_consequential_procedure_policy():
    d=markdown(b'# Procedure\n\nDo not proceed without a backup.\n\n```sh\nupgrade --no-cache\n```\n')
    speech=' '.join(u['text'] for u in plan(d,{'mode':'procedure'})['utterances'])
    assert 'upgrade --no-cache' in speech
    assert 'upgrade --no-cache' not in ' '.join(u['text'] for u in plan(d)['utterances'])

def test_epub_nav_fragment_chapter():
    original=zipfile.ZipFile(io.BytesIO(make_epub())); b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        for n in original.namelist():
            data=original.read(n)
            if n.endswith('book.opf'): data=data.replace(b'</manifest>',b'<item id="nav" href="nav.xhtml" properties="nav"/></manifest>')
            z.writestr(n,data)
        z.writestr('OEBPS/nav.xhtml','<html><body><nav><a href="a.xhtml#claim">First instruction</a></nav></body></html>')
    d=epub(b.getvalue()); assert all(x.chapter=='First instruction' for x in d.blocks if 'Do not restart' in x.source)

def test_qa_word_distance_and_decimal_tokens():
    from audiobook.qa import tokens,distance
    assert tokens('Keep 0.08, not 0.80.')==['keep','0.08','not','0.80']
    assert distance(['do','not','restart'],['do','restart'])==1

def test_content_bound_extraction_cache(tmp_path):
    from audiobook.importers import cached_inspect
    path=tmp_path/'source.md'; path.write_text('# One\n\nKeep 0.08.'); cache=tmp_path/'cache'; cache.mkdir()
    first=cached_inspect(path,cache); second=cached_inspect(path,cache); assert first.json()==second.json() and len(list(cache.iterdir()))==1
    path.write_text('# Two\n\nKeep 0.80.'); changed=cached_inspect(path,cache); assert first.source_sha256!=changed.source_sha256 and len(list(cache.iterdir()))==2

def test_markdown_figure_alt_and_unresolved_image():
    described=plan(markdown(b'# Figure\n\n![Pressure falls below 8 kPa before restart.](local.png)\n'))
    assert 'Pressure falls below 8 kilopascals before restart.' in ' '.join(u['text'] for u in described['utterances']) and not described['issues']
    assert plan(markdown(b'# Figure\n\n![](unexplained.png)\n'))['issues']

def test_mixed_section_policy_preserves_order_and_scopes_literals():
    d=markdown(b'# Narrative\n\nRead the explanation.\n\n```sh\nfirst --no-cache\n```\n\n# Methods\n\nDo not skip the backup.\n\n```sh\nsecond --no-cache\n```\n')
    p=plan(d,{'sections':{'Methods':{'mode':'procedure'}}}); speech=' '.join(u['text'] for u in p['utterances'])
    assert 'first --no-cache' not in speech and 'second --no-cache' in speech
    assert speech.index('Read the explanation.')<speech.index('Do not skip the backup.')

def test_epub_orphan_text_preserves_inline_literals_and_body_order():
    original=zipfile.ZipFile(io.BytesIO(make_epub())); data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        for n in original.namelist():
            content=original.read(n)
            if n.endswith('a.xhtml'): content=content.replace(b'</body>',b'<div id="procedure">Do not restart at 0.<b>08</b> mg. Version 2.<em>4</em>.10.<p>Middle condition.</p>Keep the final exception.</div></body>')
            z.writestr(n,content)
    d=epub(data.getvalue()); sources=[b.source for b in d.blocks]
    assert sources.count('Do not restart at 0.08 mg. Version 2.4.10.')==2
    i=sources.index('Do not restart at 0.08 mg. Version 2.4.10.'); assert sources[i+1:i+3]==['Middle condition.','Keep the final exception.']
    assert all(b.locator.get('text_node_rank') is not None for b in d.blocks if b.source.startswith('Do not restart at 0.08'))


def test_epub_publisher_lookup_is_preserved_in_companion():
    data=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(make_epub())) as original, zipfile.ZipFile(data,'w') as output:
        for path in original.namelist():
            content=original.read(path)
            if path=='OEBPS/b.xhtml':
                content=content.replace(b'</body>',b'<p>THE END</p><section id="pg-footer">'
                                        b'<p>PLEASE READ THIS publisher notice.</p>'
                                        b'<p>Distribution varies outside the United States.</p></section></body>')
            output.writestr(path,content)
    d=epub(data.getvalue()); p=plan(d)
    references=[b for b in d.blocks if b.role=='reference']
    assert any('PLEASE READ THIS' in b.source for b in references)
    assert any('United States' in b.source for b in references)
    assert all(next(x for x in p['decisions'] if x['block']==b.id)['action']=='companion' for b in references)
    assert any(b.source=='THE END' for b in d.blocks)

def test_epub_paragraph_inline_literals_do_not_gain_spaces():
    original=zipfile.ZipFile(io.BytesIO(make_epub())); data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        for n in original.namelist():
            content=original.read(n)
            if n.endswith('a.xhtml'): content=content.replace(b'</body>',b'<p>Keep 0.<b>08</b> mg in version 2.<em>4</em>.10.<br/>Do not restart.</p></body>')
            z.writestr(n,content)
    d=epub(data.getvalue()); speech=' '.join(u['text'] for u in plan(d)['utterances']); assert '0.08 mg in version 2.4.10.' in speech and '. Do not restart.' in ' '.join(speech.split())
    assert '.\nDo not restart.' in speech

def test_critical_spoken_forms_are_versioned_and_source_is_unchanged():
    d=markdown(b'# Threshold\n\nKeep pressure < 8 kPa, not >= 12 kPa, on 03/04/2026.\n')
    old=plan(d,rule_revision='faithful-1'); new=plan(d)
    assert '< 8 kPa' in ' '.join(u['text'] for u in old['utterances'])
    speech=' '.join(u['text'] for u in new['utterances']); assert 'less than 8 kilopascals' in speech and 'greater than or equal to 12 kilopascals' in speech
    assert 'zero three slash zero four slash two zero two six' in speech and '03/04/2026' in d.blocks[1].source

def illustrated_epub():
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        z.writestr('book.opf','<package><metadata><title>Images</title><meta name="cover" content="cover"/></metadata><manifest><item id="cover" href="cover.jpg"/><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
        z.writestr('a.xhtml','<html xmlns:xlink="http://www.w3.org/1999/xlink"><body><svg><image xlink:href="cover.jpg"/></svg><h1>ONE</h1><p>Keep the warning.</p><svg><image xlink:href="plot.jpg"/></svg><h1>TWO</h1><p>Keep the exception.</p><h1>ONE</h1><p>A distinct repeated title.</p></body></html>')
        z.writestr('cover.jpg',b'fixture');z.writestr('plot.jpg',b'fixture')
    return epub(b.getvalue())

def test_svg_cover_is_distinct_from_unknown_diagram_and_repeated_chapters():
    d=illustrated_epub();p=plan(d)
    cover=next(b for b in d.blocks if b.role=='cover'); diagram=next(b for b in d.blocks if b.role=='figure')
    assert cover.locator['image_resource']=='cover.jpg' and not cover.issue
    assert diagram.locator['image_resource']=='plot.jpg' and diagram.issue
    assert next(a for a in p['decisions'] if a['block']==cover.id)['action']=='companion'
    assert [i['block'] for i in p['issues']]==[diagram.id]
    assert p['chapters']==['ONE','TWO','ONE']

def test_document_policy_requires_source_identity_verified_reason_and_known_block():
    d=illustrated_epub();diagram=next(b for b in d.blocks if b.role=='figure')
    rule={'action':'companion','verified':True,'reason':'Inspected the fixture asset: decorative mark; carries no narrative relationship.'}
    bound={'source_sha256':d.source_sha256,'blocks':{diagram.id:rule}}
    p=plan(d,{'document':bound})
    assert not p['issues']
    assert next(a for a in p['decisions'] if a['block']==diagram.id)['source']==diagram.source
    assert 'Keep the warning.' in ' '.join(u['text'] for u in p['utterances'])
    assert all('document' not in a['section_policy'] for a in p['decisions'])
    for bad in ({**bound,'source_sha256':'different'},{**bound,'blocks':{'unknown':rule}},{**bound,'blocks':{diagram.id:{**rule,'verified':False}}},{**bound,'blocks':{diagram.id:{**rule,'reason':''}}}):
        with pytest.raises(Error): plan(d,{'document':bad})
