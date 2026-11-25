"""Rebase saved decisions only onto an identified immutable source occurrence."""
from copy import deepcopy
from collections import Counter
from .core import Error


def conflict():
    raise Error('repair_rebase_conflict','A saved decision has no unique matching source occurrence after reimport.','Keep the existing job and inspect its source context; supply an explicit policy for the new import.')


def rebase(previous,fresh,repairs,policy=None,*,retain_sources=(),optional_sources=()):
    if previous.source_sha256!=fresh.source_sha256:conflict()
    original=deepcopy(previous.blocks);by_id={b.id:b for b in original}
    edits=[r for r in repairs if 'block' in r]
    # Context must describe the imported source, not the corrected wording.
    for edit in reversed(edits):
        b=by_id.get(edit['block'])
        if b is None or b.source!=edit['replacement']:conflict()
        b.source=edit['original']
    targets={r['block'] for r in edits}|set(retain_sources)
    if policy:
        targets.update(policy.get('blocks',{}))
        targets.update(policy.get('pdf_glyph_aliases',{}))
        performance=policy.get('performance',{})
        targets.update(performance.get('disabled_blocks',[]))
        targets.update(performance.get('passage_instructions',{}))
        structure=policy.get('structure',{})
        targets.update(structure.get('title',{}).get('source_blocks',[]))
        targets.update(r['source_block'] for r in structure.get('chapters',[]))
    def scope(b):return tuple(b.locator.get(k) for k in ('format','path','occurrence','page','note'))
    def identity(b):return scope(b),b.source
    old_counts=Counter(identity(b) for b in original)
    new_counts=Counter(identity(b) for b in fresh.blocks)
    def context(blocks,i):
        return tuple(identity(blocks[k]) if 0<=k<len(blocks) else None for k in (i-1,i+1))
    mapped={};used=set()
    for bid in [*sorted(targets),*sorted(set(optional_sources)-targets)]:
        if bid not in by_id:
            if bid in targets:conflict()
            continue
        old=by_id[bid];oi=next(i for i,b in enumerate(original) if b.id==bid)
        if old_counts[identity(old)]>1 and new_counts[identity(old)]!=old_counts[identity(old)]:
            if bid in targets:conflict()
            continue
        candidates=[(i,b) for i,b in enumerate(fresh.blocks) if identity(b)==identity(old)]
        exact=[b for _,b in candidates if b.id==bid and b.locator==old.locator]
        if len(exact)==1 and old_counts[identity(old)]==1:matches=exact
        elif len(candidates)==1 and old_counts[identity(old)]==1:matches=[candidates[0][1]]
        else:matches=[b for i,b in candidates if context(fresh.blocks,i)==context(original,oi) and any(v is not None for v in context(original,oi))]
        if len(matches)!=1 or matches[0].id in used:
            if bid in targets:conflict()
            continue
        mapped[bid]=matches[0].id;used.add(matches[0].id)
    new_repairs=deepcopy(repairs);new_by_id={b.id:b for b in fresh.blocks}
    for edit in new_repairs:
        if 'block' not in edit:continue
        old=edit['block'];edit['block']=mapped[old]
        if old!=edit['block']:edit.setdefault('rebased_from',[]).append(old)
        b=new_by_id[edit['block']]
        if b.source!=edit['original']:conflict()
        b.source=edit['replacement'];b.issue=None
        b.evidence.append('Saved edit reapplied to a unique immutable-source occurrence')
    new_policy=deepcopy(policy)
    if new_policy:
        new_policy['blocks']={mapped[k]:v for k,v in new_policy.get('blocks',{}).items()}
        if 'pdf_glyph_aliases' in new_policy:
            new_policy['pdf_glyph_aliases']={mapped[k]:v for k,v in new_policy['pdf_glyph_aliases'].items()}
        performance=new_policy.get('performance',{})
        if 'disabled_blocks' in performance:
            performance['disabled_blocks']=[mapped[k] for k in performance['disabled_blocks']]
        if 'passage_instructions' in performance:
            performance['passage_instructions']={mapped[k]:v for k,v in performance['passage_instructions'].items()}
        structure=new_policy.get('structure',{})
        if structure.get('title'):structure['title']['source_blocks']=[mapped[k] for k in structure['title']['source_blocks']]
        for chapter in structure.get('chapters',[]):chapter['source_block']=mapped[chapter['source_block']]
    return new_repairs,new_policy,mapped
