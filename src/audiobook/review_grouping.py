"""Group review leads without extending a correction or accepting any take."""
import re
from .core import digest,key


def source_groups(document,plan):
    unresolved={issue['block'] for issue in plan['issues']};groups={}
    decisions={row['block']:row for row in plan['decisions']}
    for block in document['blocks']:
        if block['id'] not in unresolved:continue
        candidates=[];locator=block['locator'];source=block['source']
        for span in locator.get('inline_semantics',[]):
            if span['kind'] not in ('pronunciation_hint','lexicon_hint'):continue
            evidence={k:span.get(k) for k in ('phoneme','phonemes','alphabet','reading','origin') if span.get(k) is not None}
            candidates.append((span.get('source',''),span.get('language') or locator.get('language',document.get('language','en')),evidence,None))
        audit=decisions.get(block['id'],{}).get('abbreviations',{})
        for pending in audit.get('review',[]):
            # Without an authored sense, only identical local contexts group.
            evidence_text=audit.get('input',source)
            context=evidence_text[max(0,pending['input_start']-50):pending['input_end']+50]
            candidates.append((pending['source'],locator.get('language',document.get('language','en')),{'reason':pending['reason']},re.sub(r'\s+',' ',context)))
        for spelling,language,evidence,sense_context in candidates:
            identity={'spelling':spelling,'language':language,'evidence':evidence,'sense_context':sense_context};identifier='g'+key(identity)[:16]
            group=groups.setdefault(identifier,{'id':identifier,**identity,'occurrences':[]})
            if any(row['block']==block['id'] for row in group['occurrences']):continue
            group['occurrences'].append({'block':block['id'],'source_sha256':digest(source.encode()),'context':source,'location':{k:v for k,v in locator.items() if k not in ('exact','rows','inline_semantics')}})
    return {'purpose':'Related source-review leads. A group grants no correction scope or audio acceptance.','groups':list(groups.values())}
