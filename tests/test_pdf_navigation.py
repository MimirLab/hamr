import io
from reportlab.pdfgen.canvas import Canvas
from audiobook.importers import pdf
from audiobook.planner import plan
from audiobook.core import Block
from audiobook.pdf_navigation import apply


def test_actual_pdf_outline_coordinates_keep_prefix_and_two_same_page_chapters():
    data=io.BytesIO();c=Canvas(data,pagesize=(600,800));c.drawString(40,700,'FOREWORD_CANARY.');c.showPage()
    c.bookmarkPage('one',fit='XYZ',left=40,top=700,zoom=0);c.addOutlineEntry('Chapter One','one',level=0)
    c.drawString(40,700,'Chapter One');c.drawString(40,670,'ALPHA_CANARY.')
    c.bookmarkPage('two',fit='XYZ',left=40,top=400,zoom=0);c.addOutlineEntry('Chapter Two','two',level=0)
    c.drawString(40,400,'Chapter Two');c.drawString(40,370,'BETA_CANARY.');c.save()
    d=pdf(data.getvalue());p=plan(d)
    assert p['chapters']==['Opening','Chapter One','Chapter Two']
    assert next(u for u in p['utterances'] if 'ALPHA_CANARY' in u['text'])['chapter']=='Chapter One'
    assert next(u for u in p['utterances'] if 'BETA_CANARY' in u['text'])['chapter']=='Chapter Two'
    assert all(e['status']=='source_matched' for e in d.inventory[0]['source_outlines'])


def test_ambiguous_heading_and_unqualified_geometry_do_not_choose_a_chapter():
    blocks=[Block('a','Same',{'page':1,'rect':[10,600,100,620]}),Block('b','Same',{'page':1,'rect':[10,300,100,320]})]
    for entry,page in [({'entry':0,'title':'Same','page':1}, {'page':1}),({'entry':0,'title':'Same','page':1,'top':610},{'page':1,'native_geometry_qualified':False})]:
        result=apply(blocks,[page],[entry]);assert result[0]['status']=='unresolved'
    assert all(b.role=='body' for b in blocks)


def test_page_breaks_are_not_invented_as_audiobook_chapters():
    from audiobook.core import Document
    d=Document('Book','pdf','source',[Block('a','The sentence continues',{'format':'pdf','page':1},chapter='Page 1'),Block('b','across the printed page boundary.',{'format':'pdf','page':2},chapter='Page 2')])
    p=plan(d);assert p['chapters']==['Opening']
    assert len(p['utterances'])==1 and p['utterances'][0]['text']=='The sentence continues across the printed page boundary.'
