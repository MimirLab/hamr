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


def ask(endpoint, requests):
    payload = json.dumps({'schema_version': 1, 'requests': requests}, ensure_ascii=False).encode('utf-8')
    if len(payload) > MAX_BYTES:
        return 'request_too_large', []
    content = bytearray()
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=2,
                          headers={'Content-Type':'application/json','Accept':'application/json','Accept-Encoding':'identity'}) as client:
            with wall_budget(BUDGET_SECONDS, client.close, 'context_reviewer_timeout', 'The local candidate reviewer exceeded its time budget.'):
                with client.stream('POST',endpoint,content=payload) as response:
                    response.raise_for_status()
                    if response.headers.get('content-encoding','identity')!='identity':
                        return 'invalid_reply', []
                    for chunk in response.iter_raw():
                        if len(content)+len(chunk)>MAX_BYTES:
                            return 'invalid_reply', []
                        content.extend(chunk)
    except (httpx.HTTPError, Error):
        return 'unavailable', []
    try:
        replies = json.loads(content)
    except (ValueError, UnicodeError, RecursionError):
        return 'invalid_reply', []
    if not isinstance(replies,list) or len(replies)>len(requests):
        return 'invalid_reply', []

    pending = {request['occurrence_id']:request for request in requests}
    accepted=[]
    for reply in replies:
        identity=reply.get('occurrence_id') if isinstance(reply,dict) else None
        request=pending.pop(identity,None) if isinstance(identity,str) else None
        status, chosen=review_choice(reply,request) if request else ('invalid_reply',None)
        if status=='invalid_reply':
            return status,[]
        if chosen:
            accepted.append(reply)
    return ('selected' if accepted else 'declined'),accepted


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

    outcome,replies=ask(endpoint,pending)
    receipt={'transport':'loopback_http','outcome':outcome,'requests':len(pending),'selections':len(replies),
             'source_sha256':doc.source_sha256,'configuration_sha256':context['configuration_sha256'],
             'request_sha256':key(pending)}
    final={**draft['policy'],'context_review_receipt':receipt}
    if replies:
        final['context_review']=[*chosen,*replies]
    return plan(doc,final,aliases,rule_revision)
