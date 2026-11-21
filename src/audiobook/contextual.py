"""Resolve source-owned expressions before the ordinary narration formatter."""
from collections import Counter, defaultdict
import re

from . import context_config, context_grammar as grammar
from .abbreviations import PROTECTED
from .core import Error, digest, key
from .linguistic import literal_cues

REVIEW_LIMIT = 32
REVIEW_CONTEXT = {'neighbors', 'table_header', 'chapter'}
DEFINITION=re.compile(r'^In this chapter +([A-Za-z]{1,10}\.) +(?:means|stands for) +([A-Za-z]+(?: [A-Za-z]+){0,5})\.',re.I)


def review_data(value):
    if value is None:
        return None, {}
    if not isinstance(value, list) or len(value) > REVIEW_LIMIT:
        context_config.invalid(f'Context review must be a JSON array of at most {REVIEW_LIMIT} replies.')
    by_id = defaultdict(list)
    for item in value:
        if isinstance(item, dict) and isinstance(item.get('occurrence_id'), str):
            by_id[item['occurrence_id']].append(item)
    return value, by_id


def review_choice(reply, request):
    if (not isinstance(reply, dict) or set(reply) != {'source_digest','occurrence_id','action','candidate_id','evidence_ids','requested_context'}
            or reply['source_digest'] != request['source_digest'] or reply['occurrence_id'] != request['occurrence_id']):
        return 'invalid_reply', None
    evidence, extra = reply['evidence_ids'], reply['requested_context']
    if not all(isinstance(values,list) and all(isinstance(v,str) for v in values) and len(values)==len(set(values)) for values in (evidence,extra)):
        return 'invalid_reply', None
    if set(evidence) - {f['id'] for f in request['evidence']} or set(extra) - REVIEW_CONTEXT:
        return 'invalid_reply', None
    action, chosen = reply['action'], reply['candidate_id']
    if action == 'select' and not extra and chosen in [c['id'] for c in request['candidates']]:
        return 'selected', chosen
    if action == 'cannot_resolve' and chosen is None and not extra:
        return 'cannot_resolve', None
    if action == 'request_context' and chosen is None and extra:
        return 'additional_context_unavailable', None
    return 'invalid_reply', None


def table_view(rows):
    """Keep typed cell ownership while preparing the established spoken table."""
    cells, parts = [], ['Table. ']
    position = len(parts[0])
    for row_index, row in enumerate(rows[1:], 1):
        for column, (header,value) in enumerate(zip(rows[0],row)):
            prefix = f'{header}: '
            suffix = '; ' if column+1 < len(row) else '. '
            start = position+len(prefix)
            cells.append((start,start+len(value),row_index,column,header,value))
            parts.extend((prefix,value,suffix))
            position += len(prefix)+len(value)+len(suffix)
    return ''.join(parts).rstrip(),cells


def column_anchors(cells, date_headers):
    observations = defaultdict(lambda: defaultdict(list))
    for _,_,row,column,header,value in cells:
        if header.casefold().strip() not in date_headers:
            continue
        match = re.fullmatch(r'([0-9]{1,2})/([0-9]{1,2})(?:/([0-9]{4}))?',value.strip())
        if match:
            a,b,*tail = match.groups()
            fields = [a,b]+(tail if tail[0] is not None else [])
            orders=[order for order in ('MD','DM') if grammar.date_candidate(fields,order,())]
            if len(orders)==1:
                observations[column][orders[0]].append({'row':row,'column':column,'surface':value})
    return {column:dict(readings) for column,readings in observations.items()}


class Evidence:
    def __init__(self, resolver, text, start, end, header, anchors, chapter, chapter_id):
        self.resolver = resolver
        self.before = text[max(0,start-96):start].rsplit('\n',1)[-1]
        self.continuation=text[end:end+96]
        self.after = self.continuation.split('\n',1)[0]
        self.before_start,self.after_start=start-len(self.before),end
        self.header, self.anchors = header,anchors
        self.inferred_order = next(iter(anchors)) if len(anchors)==1 and len({row['surface'].strip() for row in next(iter(anchors.values()))})>=2 else None
        local = resolver.chapters.get(chapter_id,resolver.chapters.get(chapter,{}))
        self.settings = {**resolver.config['conventions'],**resolver.document_conventions,**local}
        self.origins = {name:('chapter policy' if name in local else 'document policy' if name in resolver.document_conventions else 'listening settings') for name in self.settings}
        self.governors = set()
        self.neighbors = set()
        self.definitions=[]
        self.currency_label=None

    def setting(self, name):
        return self.settings.get(name)

    def cue(self, group, *, side='left'):
        text = self.before.rstrip(' \t:="“\'‘') if side=='left' else self.after.lstrip(' \t')
        match = self.resolver.cues[group][side].search(text)
        if match:
            origin=self.before_start if side=='left' else self.after_start+len(self.after)-len(text)
            self.governors.add((group,side,match[0],origin+match.start(),origin+match.end()))
        return match is not None

    def nearby(self, group):
        clause=re.split(r'[.!?;:"“”\n]',self.before)[-1]
        words=list(re.finditer(r'\S+',clause))
        start=words[-10].start() if len(words)>10 else 0
        context=clause[start:]
        if re.search(r'\b(?:not|never|neither)\b',context,re.I):return False
        match=self.resolver.cues[group]['nearby'].search(context)
        if match:
            origin=self.before_start+len(self.before)-len(clause)+start
            self.neighbors.add((group,match[0],origin+match.start(),origin+match.end()))
        return match is not None

    def header_cue(self, group):
        if self.header is None:
            return False
        value=self.header.casefold().strip()
        return value in self.resolver.config['cues'][group] or group=='finance' and any(
            value.startswith(phrase+' (') for phrase in self.resolver.config['cues'][group])

    def finance(self):
        label=self.header if self.header else self.before.rstrip(' \t:')
        match=re.search(r'\((USD|CAD|AUD|NZD|EUR|GBP)\)$',label)
        self.currency_label=match[1] if match else None
        words=label[:match.start()].rstrip() if match else label
        valid=self.header_cue('finance') if self.header else self.resolver.cues['finance']['left'].search(words) is not None
        if valid and not self.header:
            self.governors.add(('finance','left',label,self.before_start,self.before_start+len(label)))
        return valid

    def details(self, family):
        if family=='governor':return {'cues':sorted(self.governors),'origin':'adjacent source wording'}
        if family=='nearby':return {'cues':sorted(self.neighbors),'origin':'bounded source clause with an adjacent governing word'}
        if family=='column':return {'header':self.header,'origin':'same table column'}
        if family=='anchors':return {'date_order':self.inferred_order,'anchors':self.anchors,'origin':'valid unambiguous source dates'}
        if family=='definition':return {'definitions':self.definitions,'origin':'explicit source definition in this chapter'}
        if family=='convention':return {'values':self.settings,'origins':self.origins}
        if family=='syntax':return {'origin':'source spelling and expression grammar'}
        return {'origin':'preserve surface without assigning additional meaning'}


class Projection:
    """Reserve whole expressions across the existing formatting passes."""
    def __init__(self,text, slots, salt):
        self.slots=[]
        prefix='hamrctx'+digest(salt.encode())[:16].translate(str.maketrans('0123456789','abcdefghij'))
        while prefix in text:prefix+='x'
        pieces=[];cursor=0
        for index,(a,b,spoken,event) in enumerate(sorted(slots,key=lambda slot:slot[:2]),1):
            marker=prefix+format(index,'x').translate(str.maketrans('0123456789','klmnopqrst'))+'z'
            self.slots.append((marker,text[a:b],literal_cues(spoken),event))
            pieces.extend((text[cursor:a],marker));cursor=b
        self.text=''.join((*pieces,text[cursor:]))

    def restore(self,text, *, original=False):
        pieces=[];shifts=[];protected=[];cursor=offset=0
        for marker,raw,spoken,event in self.slots:
            a=text.find(marker,cursor)
            if a<0 or text.find(marker,a+len(marker))>=0:
                raise Error('context_projection','A contextual reading was modified during formatting. Inspect the source and aliases.')
            value=raw if original else spoken
            pieces.extend((text[cursor:a],value))
            protected.append((a+offset,a+offset+len(value)))
            if event is not None and not original:
                event['output_span']=list(protected[-1])
            cursor=a+len(marker);offset+=len(value)-len(marker)
            shifts.append((cursor,offset))
        return ''.join((*pieces,text[cursor:])),shifts,protected

    @staticmethod
    def offsets(edits,keys,shifts):
        for edit in edits:
            for name in keys:
                if name in edit:
                    at=edit[name]
                    edit[name]=at+next((delta for end,delta in reversed(shifts) if end<=at),0)

    def finish(self,spoken,abbreviations,normalization):
        if not self.slots:
            return spoken,[]
        for audit,edits in ((abbreviations,abbreviations.get('readings',[])+abbreviations.get('review',[])),
                            (normalization,normalization.get('edits',[]))):
            if 'input' in audit:
                audit['input'],shifts,_=self.restore(audit['input'],original=True)
                self.offsets(edits,('input_start','input_end'),shifts)
        spoken,shifts,protected=self.restore(spoken)
        self.offsets(normalization.get('edits',[]),('output_start','output_end'),shifts)
        return spoken,protected


class Resolver:
    def __init__(self, doc, policy, source_policy, aliases):
        self.doc=doc
        self.policy=policy
        self.config=context_config.effective(policy.get('context'))
        self.implementation=context_config.implementation()
        if policy.get('context_implementation',self.implementation)!=self.implementation:
            raise Error('context_rules_changed','The saved plan uses a different contextual reader.',
                        'Use the matching build or hamr repair JOB --upgrade-rules.')
        self.document_conventions,self.chapters=context_config.scopes(source_policy.get('context',{}))
        self.identity=key([self.config,self.implementation,self.document_conventions,self.chapters])
        self.expressions=grammar.expressions(self.config['lexicon'])
        self.definitions=defaultdict(list)
        for block in doc.blocks:
            match=DEFINITION.match(block.source) if block.role=='body' and not block.issue else None
            if match and match[1].casefold() in self.config['lexicon'] and not any(
                    span['kind'] in ('literal','hidden') and span['source_start']<match.end() for span in block.locator.get('inline_semantics',[])):
                self.definitions[self.scope(block),match[1].casefold()].append(
                    {'block':block.id,'token_span':list(match.span(1)),'reading_span':list(match.span(2)),'reading':match[2]})
        policy.update(context=self.config,context_implementation=self.implementation)
        self.review,self.answers=review_data(policy.get('context_review'))
        self.cues={}
        for group,phrases in self.config['cues'].items():
            expr='(?:'+'|'.join(map(re.escape,sorted(phrases,key=lambda s:(-len(s),s))))+')' if phrases else r'(?!)'
            self.cues[group]={'left':re.compile(r'(?<!\w)'+expr+r'$',re.I),
                              'right':re.compile('^'+expr+r'(?!\w)',re.I),
                              'nearby':re.compile(r'(?<!\w)'+expr+r'(?!\w)',re.I)}
        self.aliases=re.compile('|'.join(r'(?<!\w)'+re.escape(s)+r'(?!\w)' for s in sorted(aliases,key=len,reverse=True))) if aliases else None
        self.requests=[]
        self.request_count=0
        self.used_answers=set()
        self.accepted_answers=[]

    @staticmethod
    def scope(block):
        return block.locator.get('chapter_id') or (block.locator.get('occurrence'),block.chapter)

    def outcome(self, block, view, match, env, options):
        raw=match[0];event=grammar.EVENTS[match.lastgroup]
        span=[match.start(),match.end()]
        seed=[span[0],span[0]+1] if match.lastgroup=='money' and raw[0] in grammar.SYMBOLS else span
        occurrence=key([self.doc.source_sha256,block.id,view,span,raw,event])
        facts={}
        rows=[]
        for option in options:
            references=[];score=0
            for family in dict.fromkeys(option.evidence):
                if family not in facts:
                    details=env.details(family)
                    facts[family]={'id':key([occurrence,family,details]),'kind':family,'weight':self.config['weights'][family],**details}
                references.append(facts[family]['id']);score+=facts[family]['weight']
            rows.append({'id':key([occurrence,self.identity,option.kind,option.fields,references]),'kind':option.kind,
                         'fields':option.fields,'spoken':option.spoken,'evidence_ids':references,'score':score})
        ranked=sorted(rows,key=lambda c:-c['score'])
        winner=ranked[0]
        margin=winner['score']-ranked[1]['score'] if len(ranked)>1 else None
        conflict=bool(env.inferred_order and env.setting('date_order') in ('MD','DM') and env.inferred_order!=env.setting('date_order'))
        unclear=margin is not None and margin<self.config['minimum_margin'] or conflict
        if unclear and margin==0:
            winner=next((c for c in ranked if c['kind'] in ('literal','money_symbol')),winner)
        route=('literal_fallback' if winner['kind'] in ('literal','money_symbol') else
               'automatic_best_guess' if unclear else 'code_resolved')
        row={'event_id':event,'occurrence_id':occurrence,'source_digest':self.doc.source_sha256,'view':view,
             'seed_span':seed,'expression_span':span,'offset_basis':'half-open Unicode code points',
             'source':raw,'candidates':rows,'selected':winner['id'],'score_kind':'heuristic',
             'margin':margin,'route':route,'evidence':list(facts.values())}
        if conflict:row['conflict']='Configured date order disagrees with unambiguous dates in this column.'
        if unclear:
            self.request_count+=1
            request={k:row[k] for k in ('source_digest','occurrence_id','source','candidates','evidence')}
            request['context']={'before':env.before[-64:],'after':env.after[:64],'table_header':env.header}
            if len(self.requests)<REVIEW_LIMIT:
                self.requests.append(request)
            replies=self.answers.get(occurrence,[])
            self.used_answers.add(occurrence)
            status, choice=(review_choice(replies[0],request) if len(replies)==1 else
                            ('invalid_reply' if replies else 'unavailable' if self.review is not None else 'disabled',None))
            row['review']=status
            if choice is not None:
                winner=next(c for c in rows if c['id']==choice)
                row.update(selected=choice,route='reviewer_selected')
                self.accepted_answers.append(replies[0])
            elif self.config['mode']=='strict':
                row['hold']=True
        # A resolved expression role can still leave one field (for example
        # the dollar jurisdiction) unspecified.
        fields=set().union(*(candidate['fields'] for candidate in rows))
        row['field_status']={name:('unspecified' if winner['fields'].get(name) is None else
                                  'selected_with_alternatives' if any(name in c['fields'] and c['fields'][name]!=winner['fields'][name] for c in rows) else
                                  'retained') for name in sorted(fields)}
        row['realization']='existing_renderer' if winner['spoken'] is None else 'explicit_reading'
        return row,winner['spoken']

    def prepare(self, block, text, *, chapter, chapter_id, cells=()):
        records=[];slots=[]
        if not text or block.role=='code':return Projection(text,slots,block.id),records
        literal_spans=[(s['source_start'],s['source_end']) for s in block.locator.get('inline_semantics',[]) if s['kind']=='literal']
        if literal_spans and text!=block.source:
            return Projection(text,slots,block.id),records
        protected=sorted({*literal_spans,*(m.span() for m in PROTECTED.finditer(text)),
                          *(m.span() for m in grammar.PROTECTED_PATH.finditer(text))})
        for a,b in protected:
            if slots and a<slots[-1][1]:
                start,end,_,_=slots.pop();end=max(end,b);slots.append((start,end,text[start:end],None))
            else:slots.append((a,b,text[a:b],None))
        aliases=[m.span() for m in self.aliases.finditer(text)] if self.aliases else []
        anchors=column_anchors(cells,self.config['cues']['date_column']) if cells else {}
        views=cells or [(0,len(text),None,None,None,text)]
        for start,_,row,column,header,value in views:
            view=({'kind':'table_cell','row':row,'column':column,'header':header,'input':value} if row is not None else
                  {'kind':'block_source' if value==block.source else 'prepared_reading'})
            view['text_sha256']=digest(value.encode())
            for match in self.expressions.finditer(value):
                a,b=start+match.start(),start+match.end()
                if any(a<end and b>begin for begin,end in protected+aliases):continue
                env=Evidence(self,value,match.start(),match.end(),header,anchors.get(column,{}),chapter,chapter_id)
                if match.lastgroup=='abbreviation':
                    env.definitions=self.definitions.get((self.scope(block),match[0].casefold()),[])
                defines=any(item['block']==block.id and item['token_span']==[a,b] for item in env.definitions)
                if defines:
                    options=[grammar.literal(match[0],' '.join(match[0][:-1].upper()))]
                elif env.cue('literal') or env.cue('literal_tail',side='right'):
                    options=[grammar.literal(match[0])]
                else:
                    options=grammar.candidates(match.lastgroup,match[0],env)
                if not options:continue
                result,spoken=self.outcome(block,view,match,env,options)
                records.append(result)
                if spoken is not None:slots.append((a,b,spoken,result))
        return Projection(text,slots,block.id),records

    def report(self, events):
        if self.review is not None:
            self.policy['context_review']=self.accepted_answers
        visible={event['occurrence_id'] for event in events}
        return {'configuration_sha256':self.identity,'score_kind':'heuristic',
                'routes':dict(Counter(event['route'] for event in events)),
                'review_requests':[row for row in self.requests if row['occurrence_id'] in visible],
                'review_requests_omitted':max(0,self.request_count-REVIEW_LIMIT),
                'unmatched_reviewer_occurrences':len(self.answers.keys()-self.used_answers)}
