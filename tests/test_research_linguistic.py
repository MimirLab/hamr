import json
import subprocess
import sys
from audiobook.importers import markdown
from audiobook.planner import plan


def test_actual_cli_preserves_opposite_task_status_and_companion_source(tmp_path):
    source=tmp_path/'tasks.md';source.write_text('- [x] Snapshot made\n- [ ] Rollback tested\n\nThe label [x] is printed literally.\n')
    result=subprocess.run([sys.executable,'-m','audiobook','--home',str(tmp_path/'state'),'plan',str(source),'--json'],capture_output=True,text=True,check=True)
    p=json.loads(result.stdout);spoken=' '.join(u['text'] for u in p['utterances'])
    assert 'Checked. Snapshot made Unchecked. Rollback tested' in spoken
    assert 'The label [x] is printed literally.' in spoken
    assert any(d['source']=='[x] Snapshot made' for d in p['decisions'])
    assert any(d['source']=='[ ] Rollback tested' for d in p['decisions'])
    assert not p['issues']


def test_task_marker_requires_a_list_and_does_not_consume_coordinates_or_intervals():
    doc=markdown(b'[ ] A printed label. The coordinate (2, 3) lies in [0, 1]. F1 and H2O remain. End at 0.08.')
    spoken=' '.join(u['text'] for u in plan(doc)['utterances'])
    assert spoken=='[ ] A printed label. The coordinate (2, 3) lies in [0, 1]. F1 and H2O remain. End at 0.08.'
