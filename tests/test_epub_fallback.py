import io
import json
import subprocess
import sys
import zipfile
import pytest
from audiobook.importers import epub
from audiobook.planner import plan


def package(foreign):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        z.writestr('book.opf','<package><metadata><title>Fallback control</title></metadata><manifest><item id="pre" href="pre.xhtml" media-type="application/xhtml+xml"/>'+foreign+'<item id="good" href="good.xhtml" media-type="application/xhtml+xml"/><item id="tail" href="tail.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="pre"/><itemref idref="foreign"/><itemref idref="tail"/></spine></package>')
        for name,text in [('pre','PREFIX_CANARY.'),('good','FALLBACK_CANARY.'),('tail','TAIL_CANARY.')]:z.writestr(name+'.xhtml','<html><body><p>'+text+'</p></body></html>')
        z.writestr('foreign.dat','UNSUPPORTED_CANARY_MUST_NOT_BE_PARSED_AS_PROSE')
    return data.getvalue()


def test_installed_entry_path_uses_supported_fallback_in_occurrence_order(tmp_path):
    path=tmp_path/'control.epub';path.write_bytes(package('<item id="foreign" href="foreign.dat" media-type="application/x-foreign" fallback="good"/>'))
    result=subprocess.run([sys.executable,'-m','audiobook','--home',str(tmp_path/'state'),'plan',str(path),'--json'],capture_output=True,text=True,check=True)
    p=json.loads(result.stdout)
    assert ' '.join(u['text'] for u in p['utterances'])=='PREFIX_CANARY. FALLBACK_CANARY. TAIL_CANARY.'
    assert not p['issues']
    doc=epub(path.read_bytes());occurrence=next(x for x in doc.inventory if x.get('occurrence')==2)
    assert [i['idref'] for i in occurrence['fallback_chain']]==['foreign','good']


@pytest.mark.parametrize('declaration',[
    '<item id="foreign" href="foreign.dat" media-type="application/x-foreign"/>',
    '<item id="foreign" href="foreign.dat" media-type="application/x-foreign" fallback="foreign"/>',
    '<item id="foreign" href="foreign.dat" media-type="application/x-foreign" fallback="absent"/>',
])
def test_unresolved_foreign_occurrence_remains_visible_while_clear_prose_continues(declaration):
    doc=epub(package(declaration));p=plan(doc)
    assert p['issues']
    assert ' '.join(u['text'] for u in p['utterances'])=='PREFIX_CANARY. TAIL_CANARY.'
    bad=next(b for b in doc.blocks if b.issue)
    assert bad.locator['occurrence']==2 and bad.locator['fallback_chain']
