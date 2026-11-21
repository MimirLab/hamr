"""Explicit, bounded loopback transport for contextual candidate review."""
import ipaddress
import json
from urllib.parse import urlsplit

import httpx

from .contextual import REVIEW_LIMIT, review_choice
from .core import Error, key
from .deadlines import wall_budget


MAX_BYTES = 128 * 1024
BUDGET_SECONDS = 8
CONTEXT_CHARS = 192
PRIVATE_SPANS = {'hidden', 'literal', 'note_reference', 'lexicon_hint', 'pagebreak'}


def validate_endpoint(endpoint):
    try:
        url = urlsplit(endpoint)
        address = ipaddress.ip_address(url.hostname or '')
        if (url.scheme != 'http' or not address.is_loopback or not url.port or url.username is not None
                or url.query or url.fragment or len(endpoint) > 512):
            raise ValueError('invalid local endpoint')
    except ValueError as exc:
        raise Error('context_reviewer_endpoint',
                    'Use an explicit loopback HTTP address and port for contextual candidate review.') from exc
    return endpoint


def encoded(requests):
    return json.dumps({'schema_version': 1, 'requests': requests}, ensure_ascii=False).encode('utf-8')


def ask(client, endpoint, payload, requests, response_budget):
    content = bytearray()
    with client.stream('POST',endpoint,content=payload) as response:
        response.raise_for_status()
        if response.headers.get('content-encoding','identity')!='identity':
            return 'invalid_reply', [], {}, len(content)
        for chunk in response.iter_raw():
            if len(content)+len(chunk)>response_budget:
                return 'invalid_reply', [], {}, response_budget
            content.extend(chunk)
    try:
        replies = json.loads(content)
    except (ValueError, UnicodeError, RecursionError):
        return 'invalid_reply', [], {}, len(content)
    if not isinstance(replies,list) or len(replies)>len(requests):
        return 'invalid_reply', [], {}, len(content)

    pending = {request['occurrence_id']:request for request in requests}
    accepted=[]
    requested={}
    for reply in replies:
        identity=reply.get('occurrence_id') if isinstance(reply,dict) else None
        request=pending.pop(identity,None) if isinstance(identity,str) else None
        status, chosen=review_choice(reply,request) if request else ('invalid_reply',None)
        if status=='invalid_reply':
            return status, [], {}, len(content)
        if chosen:
            accepted.append(reply)
        elif status=='additional_context_unavailable':
            requested[identity]=reply['requested_context']
    outcome='selected' if accepted else 'additional_context_unavailable' if requested else 'declined'
    return outcome, accepted, requested, len(content)


def nearby_text(block, start, end, initial):
    left, right=max(0,start-CONTEXT_CHARS),min(len(block.source),end+CONTEXT_CHARS)
    for span in block.locator.get('inline_semantics',[]):
        if span['kind'] not in PRIVATE_SPANS:
            continue
        a,b=span['source_start'],span['source_end']
        if a<end and start<b:
            return {}
        if b<=start:
            left=max(left,b)
        if a>=end:
            right=min(right,a)
    before,after=block.source[left:start],block.source[end:right]
    # Retain the shorter supplied form when there is no additional safe text.
    return {side:value for side,value in (('before',before),('after',after))
            if len(value)>len(initial.get(side,''))}


def safe_body(block):
    return block.role=='body' and not any(span['kind'] in PRIVATE_SPANS for span in block.locator.get('inline_semantics',[]))


def expand_requests(doc, draft, pending, requested):
    decisions={row['block']:row for row in draft['decisions']}
    requests={row['occurrence_id']:row for row in pending}
    headings={}
    groups={}
    for block in doc.blocks:
        row=decisions.get(block.id)
        if not row or row['action']!='speak':
            continue
        scope=row.get('chapter_occurrence')
        if block.role=='heading' and scope is not None and not block.locator.get('inline_semantics'):
            headings.setdefault(scope,block.source[:CONTEXT_CHARS])
        groups[block.id]=scope
    expanded=[]
    for index,block in enumerate(doc.blocks):
        if block.id not in groups:
            continue
        for event in decisions[block.id].get('context_events',[]):
            identity=event['occurrence_id']
            if identity not in requested:
                continue
            features=requested[identity]
            request=requests[identity]
            extra={}
            if 'chapter' in features and groups[block.id] in headings:
                extra['chapter']=headings[groups[block.id]]
            if 'neighbors' in features and event['view']['kind']=='block_source':
                a,b=event['expression_span']
                if block.source[a:b]==event['source']:
                    neighbors=nearby_text(block,a,b,request['context'])
                    scope=groups[block.id]
                    for name,position in (('previous_paragraph',index-1),('next_paragraph',index+1)):
                        if 0<=position<len(doc.blocks):
                            neighbor=doc.blocks[position]
                            if scope is not None and groups.get(neighbor.id)==scope and safe_body(neighbor):
                                neighbors[name]=neighbor.source[-CONTEXT_CHARS:] if position<index else neighbor.source[:CONTEXT_CHARS]
                    if neighbors:extra['neighbors']=neighbors
            if extra:
                expanded.append({**request,'additional_context':extra})
    return expanded


def exchange(endpoint, pending, expand):
    payload=encoded(pending)
    if len(payload)>MAX_BYTES:
        return 'request_too_large', [], {'round_trips':0}
    receipt={'round_trips':1}
    outcome='unavailable'
    replies=[]
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=2,
                          headers={'Content-Type':'application/json','Accept':'application/json','Accept-Encoding':'identity'}) as client:
            with wall_budget(BUDGET_SECONDS, client.close, 'context_reviewer_timeout', 'The local candidate reviewer exceeded its time budget.'):
                outcome,replies,requested,received=ask(client,endpoint,payload,pending,MAX_BYTES)
                followup=expand(requested) if requested else []
                if followup:
                    additional=encoded(followup)
                    receipt.update(followup_requests=len(followup),followup_sha256=key(followup))
                    if len(payload)+len(additional)<=MAX_BYTES and received<MAX_BYTES:
                        receipt.update(round_trips=2,followup_outcome='unavailable')
                        second,answers,_,_=ask(client,endpoint,additional,followup,MAX_BYTES-received)
                        receipt['followup_outcome']=second
                        replies.extend(answers)
                        outcome='selected' if replies else second
                    else:
                        receipt['followup_outcome']='request_too_large'
    except (httpx.HTTPError, Error):
        pass
    return outcome, replies, receipt


def reviewed_plan(doc, policy, aliases, rule_revision, endpoint):
    from .planner import plan

    endpoint=validate_endpoint(endpoint)
    draft=plan(doc,policy,aliases,rule_revision)
    context=draft.get('contextual_resolution',{})
    chosen=draft['policy'].get('context_review',[])
    chosen_ids={reply['occurrence_id'] for reply in chosen}
    pending=[request for request in context.get('review_requests',[]) if request['occurrence_id'] not in chosen_ids]
    pending=pending[:REVIEW_LIMIT-len(chosen_ids)]
    if not pending:
        return draft

    outcome,replies,transport=exchange(endpoint,pending,lambda requested:expand_requests(doc,draft,pending,requested))
    receipt={**transport,'transport':'loopback_http','outcome':outcome,'requests':len(pending),'selections':len(replies),
             'source_sha256':doc.source_sha256,'configuration_sha256':context['configuration_sha256'],
             'request_sha256':key(pending)}
    final={**draft['policy'],'context_review_receipt':receipt}
    if replies:
        final['context_review']=[*chosen,*replies]
    return plan(doc,final,aliases,rule_revision)
