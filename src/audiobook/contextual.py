"""Resolve source-owned expressions before the ordinary narration formatter."""
from collections import Counter, defaultdict
import re

from . import context_config, context_grammar as grammar, context_lexical, context_numbers, context_structure
from .abbreviations import PROTECTED
from .core import Error, digest, key
from .linguistic import literal_cues

REVIEW_LIMIT = 32
REVIEW_CONTEXT = {'neighbors', 'table_header', 'chapter'}
DEFINITION=re.compile(r'^In this chapter +([A-Za-z]{1,10}\.) +(?:means|stands for) +([A-Za-z]+(?: [A-Za-z]+){0,5})\.',re.I)
QUOTED_DATE_ORDER=re.compile(r'(?<!\w)(?:uses|date order(?: is)?)\s+(DD/MM|MM/DD)(?:/YYYY)?\s*:\s*$',re.I)
SCALE_HEADER=re.compile(r'\((USD|CAD|AUD|NZD|EUR|GBP|\$|£|€)\s+(thousands|millions|billions)\)$')
CHRONOLOGY_ENTRY=re.compile(r'(?P<date>'+grammar.SLASH_DATE+r')[ \t]+[–—][ \t]+(?=\w)')


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


def table_view(rows, header_columns=None):
    """Keep typed cell ownership while preparing the established spoken table."""
    cells, parts = [], ['Table. ']
    position = len(parts[0])
    mappings=header_columns or {}
    for row_index, row in enumerate(rows[1:], 1):
        for column,value in enumerate(row):
            header=rows[0][mappings.get(f'{row_index}:{column}',column)]
            prefix = f'{header}: '
            suffix = '; ' if column+1 < len(row) else '. '
            start = position+len(prefix)
            cells.append((start,start+len(value),row_index,column,header,value))
            parts.extend((prefix,value,suffix))
            position += len(prefix)+len(value)+len(suffix)
    return ''.join(parts).rstrip(),cells


def column_anchors(cells, date_headers, originals, header_columns):
    observations = defaultdict(lambda: defaultdict(list))
    for _,_,row,column,header,value in cells:
        if header.casefold().strip() not in date_headers:
            continue
        if originals and (row>=len(originals) or column>=len(originals[row]) or originals[row][column]!=value):
            continue
        match = re.fullmatch(r'([0-9]{1,2})/([0-9]{1,2})(?:/([0-9]{4}|[0-9]{2}))?',value.strip())
        if match:
            a,b,*tail = match.groups()
            fields = [a,b]+(tail if tail[0] is not None else [])
            orders=[order for order in ('MD','DM') if grammar.date_candidate(fields,order,())]
            if len(orders)==1:
                header_column=header_columns.get(f'{row}:{column}',column)
                observations[header_column][orders[0]].append({'row':row,'column':column,'surface':value})
    return {column:dict(readings) for column,readings in observations.items()}


class Evidence:
    def __init__(self, resolver, text, start, end, header, anchors, chapter, chapter_id, table_id, column, chronology=None, *, header_by_id=False):
        self.resolver = resolver
        self.text = text
        self.start = start
        self.before = text[max(0,start-96):start].rsplit('\n',1)[-1]
        self.continuation=text[end:end+96]
        self.after = self.continuation.split('\n',1)[0]
        self.before_start,self.after_start=start-len(self.before),end
        self.header, self.anchors = header,anchors
        self.chronology=chronology
        self.header_source = {'block':table_id,'row':0,'column':column} if table_id is not None else None
        header_ids=resolver.table_header_ids.get(table_id,())
        if self.header_source and column<len(header_ids) and header_ids[column]:
            self.header_source['id']=header_ids[column]
        self.header_by_id=header_by_id
        self.inferred_order = next(iter(anchors)) if len(anchors)==1 and len({row['surface'].strip() for row in next(iter(anchors.values()))})>=2 else None
        self.settings = {}
        self.origins = {}
        self.date_conventions = []
        for origin, settings in resolver.scoped_conventions(chapter,chapter_id,table_id):
            self.settings.update(settings)
            self.origins.update(dict.fromkeys(settings,origin))
            if 'date_order' in settings:
                self.date_conventions.append((origin,settings['date_order']))
        self.governors = set()
        self.neighbors = set()
        self.definitions=[]
        self.date_definition=None
        self.currency_label=None
        self.number_anchors={}
        self.duration_label=None

    def setting(self, name):
        return self.settings.get(name)

    def preceding_text(self, length):
        start=max(0,self.start-length)
        return start,self.text[start:self.start]

    def number_separator(self):
        if len(self.number_anchors)==1:
            mark, observed=next(iter(self.number_anchors.items()))
            if len(observed)>=2:
                return mark
        return None

    def cue(self, group, *, side='left'):
        text = self.before.rstrip(' \t:="“\'‘') if side=='left' else self.after.lstrip(' \t')
        match = self.resolver.cues[group][side].search(text)
        if match is None and side=='left':
            match = self.resolver.cues[group][side].search(re.sub(r'\s+(?:is|was|equals|reads|says)$','',text,flags=re.I))
        if match:
            origin=self.before_start if side=='left' else self.after_start+len(self.after)-len(text)
            self.governors.add((group,side,match[0],origin+match.start(),origin+match.end()))
        return match[0] if match else None

    def literal_mention(self):
        quoted=self.before.strip().endswith(('“','"')) and self.after.lstrip().startswith(('”','"'))
        return self.cue('literal') or self.cue('literal_tail',side='right') or quoted and self.cue('quoted_string')

    def quoted_date_order(self):
        quote = re.search(r'[“"]([^“”"]*)$',self.before)
        if not quote or self.after.lstrip(' \t.!?,;')[:1]!=('”' if quote[0][0]=='“' else '"'):
            return None
        declaration = QUOTED_DATE_ORDER.search(quote[1])
        if not declaration:
            return None
        start = self.before_start+quote.start(1)
        self.date_definition={'span':[start+declaration.start(1),start+declaration.end(1)],'source':declaration[1]}
        return 'DM' if declaration[1].upper().startswith('DD') else 'MD'

    def nearby(self, group, *, side='left'):
        before=re.split(r'[.!?;:"“”\n]',self.before)[-1]
        after=re.split(r'[.!?;:"“”\n]',self.after)[0]
        locations=[(before,self.before_start+len(self.before)-len(before))]
        if side=='either':locations.append((after,self.after_start))
        found=False
        for clause,origin in locations:
            words=list(re.finditer(r'\S+',clause))
            start=words[-10].start() if len(words)>10 else 0
            context=clause[start:]
            if re.search(r'\b(?:not|never|neither)\b',context,re.I):continue
            match=self.resolver.cues[group]['nearby'].search(context)
            if match:
                self.neighbors.add((group,match[0],origin+start+match.start(),origin+start+match.end()))
                found=True
        return found

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

    def scale_label(self):
        if not self.header:return None
        match=SCALE_HEADER.search(self.header)
        if not match:return None
        symbol,plural=match.groups()
        currency={'$':self.setting('dollar_currency'),'£':'GBP','€':'EUR'}.get(symbol,symbol)
        return currency,plural[:-1],{'thousands':'1000','millions':'1000000','billions':'1000000000'}[plural]

    def details(self, family):
        if family=='governor':
            if self.chronology:return {'origin':'authored chronology heading and date-entry shape','heading':self.chronology}
            return {'cues':sorted(self.governors),'origin':'adjacent source wording'}
        if family=='nearby':return {'cues':sorted(self.neighbors),'origin':'bounded source clause with an adjacent governing word'}
        if family=='column':return {'header':self.header,'source':self.header_source,
                                   'origin':'authored table header reference' if self.header_by_id else 'same table column'}
        if family=='anchors':
            mark=self.number_separator()
            if mark:return {'decimal_separator':mark,'anchors':list(self.number_anchors[mark].values()),
                            'origin':'unambiguous written measurements in the same source section'}
            return {'date_order':self.inferred_order,'anchors':self.anchors,'origin':'valid unambiguous source dates'}
        if family=='definition':
            if self.date_definition:return {'definition':self.date_definition,'origin':'explicit date order inside the same quotation'}
            return {'definitions':self.definitions,'origin':'explicit source definition in this chapter'}
        if family=='convention':return {'values':self.settings,'origins':self.origins,'date_order_lineage':self.date_conventions}
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
            self.slots.append((marker,text[a:b],literal_cues(spoken,current=True),event))
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
            output_span=(a+offset,a+offset+len(value))
            if value:protected.append(output_span)
            if event is not None and not original:
                event['output_span']=list(output_span)
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
        self.document_conventions,self.chapters,self.tables=context_config.scopes(source_policy.get('context',{}))
        table_ids={block.id for block in doc.blocks if block.role=='table'}
        self.table_header_ids={block.id:block.locator['header_ids'] for block in doc.blocks if 'cell_ids' in block.locator}
        if self.tables.keys()-table_ids:
            context_config.invalid('A context table policy names an unknown source table.')
        self.identity=key([self.config,self.implementation,self.document_conventions,self.chapters,self.tables])
        self.expressions=grammar.expressions(self.config['lexicon'],self.config['initialisms'],
                                             self.config['cues']['price_noun'],self.config['native_names'])
        self.definitions=defaultdict(list)
        self.chronologies=defaultdict(lambda:defaultdict(list))
        self.number_anchors=defaultdict(lambda:defaultdict(dict))
        self.number_exclusions={}
        self.chronology_headings={}
        self.chronology_entries={}
        headings=self.config['cues']['date_chronology']
        chronology_headings=re.compile(r'(?<!\w)(?:'+'|'.join(map(re.escape,headings))+r')(?!\w)',re.I) if headings else None
        quantity_units=set(self.config['cues']['quantity_unit'])|set(context_numbers.SI_SYMBOLS)
        number_pattern=context_numbers.witness_pattern(grammar.LEFT,grammar.RIGHT,quantity_units)
        for block in doc.blocks:
            scope=self.scope(block)
            if block.role=='heading' and not block.issue and chronology_headings and chronology_headings.search(block.source):
                self.chronology_headings[scope]={'block':block.id,'title':block.source}
            if block.role=='body' and not block.issue and scope in self.chronology_headings:
                entry=CHRONOLOGY_ENTRY.match(block.source)
                if entry and not any(span['kind'] in ('literal','hidden') and span['source_start']<entry.end('date')
                                     for span in block.locator.get('inline_semantics',[])):
                    value=entry['date']
                    self.chronology_entries[block.id]=value
                    orders=[order for order in ('MD','DM') if grammar.date_candidate(value.split('/'),order,())]
                    if len(orders)==1:
                        self.chronologies[scope][orders[0]].append({'block':block.id,'surface':value,'span':list(entry.span('date'))})
            match=DEFINITION.match(block.source) if block.role=='body' and not block.issue else None
            if match and match[1].casefold() in self.config['lexicon'] and not any(
                    span['kind'] in ('literal','hidden') and span['source_start']<match.end() for span in block.locator.get('inline_semantics',[])):
                self.definitions[self.scope(block),match[1].casefold()].append(
                    {'block':block.id,'token_span':list(match.span(1)),'reading_span':list(match.span(2)),'reading':match[2]})
            if block.role=='body' and not block.issue and ('.' in block.source or ',' in block.source):
                excluded=[(span['source_start'],span['source_end']) for span in block.locator.get('inline_semantics',[])
                          if span['kind'] in ('literal','hidden','hyperlink','language','ruby')]
                excluded.extend(match.span() for match in context_numbers.QUOTATIONS.finditer(block.source))
                if block.locator.get('format')=='markdown' and block.locator.get('exact','').lstrip().startswith('>'):
                    excluded=[(0,len(block.source))]
                if excluded:self.number_exclusions[block.id]=excluded
                for mark, number, span in context_numbers.witnesses(block.source,number_pattern,excluded):
                    observed=self.number_anchors[scope][mark]
                    if len(observed)<4:
                        observed.setdefault(number,{'block':block.id,'span':span,'value':number})
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

    def scoped_conventions(self, chapter, chapter_id, table_id):
        return (('listening settings',self.config['conventions']),('document policy',self.document_conventions),
                ('chapter policy',self.chapters.get(chapter_id,self.chapters.get(chapter,{}))),
                ('table policy',self.tables.get(table_id,{})))

    def table_ditto(self, block, chapter, chapter_id):
        scopes=self.scoped_conventions(chapter,chapter_id,block.id)
        return next((settings['ditto_marker'] for _,settings in reversed(scopes) if 'ditto_marker' in settings),None)

    def structural_evidence(self, block, cells, table_fragments):
        return context_structure.observations(block,cells,table_fragments,source_digest=self.doc.source_sha256)

    def outcome(self, block, view, match, env, options):
        raw=match[0];event=grammar.EVENTS[match.lastgroup]
        if match.lastgroup=='money_rate' and all(option.kind=='literal' for option in options) and env.literal_mention():
            event='CR-N040'
        span=[match.start(),match.end()]
        seed=[span[0],span[0]+1] if match.lastgroup=='money' and raw[0] in grammar.SYMBOLS else span
        if match.lastgroup=='uncertainty':
            seed=[span[0],span[0]+raw.index(')')+1]
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
            if option.components:
                rows[-1]['components']=[{'event_id':child.event_id,'kind':child.kind,'fields':child.fields,
                                         'span':[span[0]+i for i in child.span],
                                         'source':raw[child.span[0]:child.span[1]]} for child in option.components]
        ranked=sorted(rows,key=lambda c:-c['score'])
        winner=ranked[0]
        margin=winner['score']-ranked[1]['score'] if len(ranked)>1 else None
        conflict=bool(env.inferred_order and env.setting('date_order') in ('MD','DM') and env.inferred_order!=env.setting('date_order'))
        unclear=margin is not None and margin<self.config['minimum_margin'] or conflict
        if unclear and margin==0:
            winner=next((c for c in ranked if c['kind'] in ('literal','money_symbol') or c['fields'].get('day_month_order')=='retain_alternatives_if_not_established' or c['fields'].get('sense',False) is None),winner)
        route=('literal_fallback' if winner['kind'] in ('literal','literal_fallback','money_symbol','unknown_numeric_form') or winner['fields'].get('day_month_order')=='retain_alternatives_if_not_established' else
               'automatic_best_guess' if unclear else 'code_resolved')
        row={'event_id':event,'occurrence_id':occurrence,'source_digest':self.doc.source_sha256,'view':view,
             'seed_span':seed,'expression_span':span,'offset_basis':'half-open Unicode code points',
             'source':raw,'candidates':rows,'selected':winner['id'],'score_kind':'heuristic',
             'margin':margin,'route':route,'evidence':list(facts.values())}
        if view['kind']=='block_source' and (leaves:=context_structure.source_leaves(block,*span)):
            row['source_leaves']=leaves
        if conflict:
            location='chronology' if env.chronology else 'column'
            row['conflict']=f'Configured date order disagrees with unambiguous dates in this {location}.'
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
        related=grammar.related_events(match.lastgroup,raw,env,winner)
        if related:row['related_event_ids']=related
        return row,winner['spoken']

    def prepare(self, block, text, *, chapter, chapter_id, cells=(), prepared_spans=None):
        records=[];slots=[]
        if not text or block.role=='code':return Projection(text,slots,block.id),records
        literal_spans=[(s['source_start'],s['source_end']) for s in block.locator.get('inline_semantics',[]) if s['kind']=='literal']
        if prepared_spans is not None:
            literal_spans=prepared_spans
        elif literal_spans and text!=block.source:
            return Projection(text,slots,block.id),records
        protected=sorted({*literal_spans,*(m.span() for m in PROTECTED.finditer(text)),
                          *(m.span() for m in grammar.PROTECTED_PATH.finditer(text))})
        for a,b in protected:
            if slots and a<slots[-1][1]:
                start,end,_,_=slots.pop();end=max(end,b);slots.append((start,end,text[start:end],None))
            else:slots.append((a,b,text[a:b],None))
        aliases=[m.span() for m in self.aliases.finditer(text)] if self.aliases else []
        header_columns=block.locator.get('header_columns',{})
        anchors=column_anchors(cells,self.config['cues']['date_column'],block.locator.get('rows',[]),header_columns) if cells else {}
        views=cells or [(0,len(text),None,None,None,text)]
        guard_slots={(a,b):i for i,(a,b,_,_) in enumerate(slots)}
        for start,_,row,column,header,value in views:
            cell=f'{row}:{column}'
            header_column=header_columns.get(cell,column)
            authored_header=cell in header_columns
            view=({'kind':'table_cell','row':row,'column':column,'header':header,'input':value} if row is not None else
                  {'kind':'block_source' if value==block.source else 'prepared_reading'})
            view['text_sha256']=digest(value.encode())
            for match in context_lexical.OPAQUE.finditer(value):
                a,b=start+match.start(),start+match.end()
                if (a,b) not in guard_slots or any(a<end and b>begin for begin,end in literal_spans+aliases):
                    continue
                env=Evidence(self,value,match.start(),match.end(),header,anchors.get(header_column,{}),chapter,chapter_id,
                             block.id if row is not None else None,header_column,header_by_id=authored_header)
                option=context_lexical.opaque_reading(match.lastgroup,match[0],env)
                if option is not None:
                    result,spoken=self.outcome(block,view,match,env,[option])
                    records.append(result)
                    slots[guard_slots[a,b]]=(a,b,spoken,result)
            for match in self.expressions.finditer(value):
                a,b=start+match.start(),start+match.end()
                if any(a<end and b>begin for begin,end in protected+aliases):continue
                chronology=(self.chronology_headings.get(self.scope(block)) if match.lastgroup=='slash' and a==0
                            and value==block.source and match[0]==self.chronology_entries.get(block.id) else None)
                inferred=self.chronologies.get(self.scope(block),{}) if chronology else anchors.get(header_column,{})
                env=Evidence(self,value,match.start(),match.end(),header,inferred,chapter,chapter_id,
                             block.id if row is not None else None,header_column,chronology,header_by_id=authored_header)
                if (match.lastgroup in ('separators','measurement','uncertainty') and value==block.source and row is None
                        and not any(a<end and begin<b for begin,end in self.number_exclusions.get(block.id,()))):
                    env.number_anchors=self.number_anchors.get(self.scope(block),{})
                if match.lastgroup=='abbreviation':
                    env.definitions=self.definitions.get((self.scope(block),match[0].casefold()),[])
                defines=any(item['block']==block.id and item['token_span']==[a,b] for item in env.definitions)
                if defines:
                    options=[grammar.literal(match[0],' '.join(match[0][:-1].upper()))]
                elif env.literal_mention() and match.lastgroup not in ('censoring','stage_direction','shell_token'):
                    if (match.lastgroup in ('money','money_modifier','money_rate','slash') and
                            (env.cue('literal_tail',side='right') or env.before.strip().endswith(('“','"')))):
                        options=[grammar.literal_notation(match[0])]
                    else:options=[context_lexical.literal_candidate(match.lastgroup,match[0],env)]
                else:
                    options=grammar.candidates(match.lastgroup,match[0],env)
                if not options:continue
                result,spoken=self.outcome(block,view,match,env,options)
                records.append(result)
                if spoken is not None:
                    if env.duration_label and env.setting('narration_style')!='technical' and not result.get('hold'):
                        selected=next(candidate for candidate in result['candidates'] if candidate['id']==result['selected'])
                        if selected['fields'].get('field_order'):
                            first,last=env.duration_label
                            origin=start-len(header)-2 if header is not None else start
                            label_start,label_end=origin+first,origin+last
                            if not any(label_start<end and begin<label_end for begin,end,_,_ in slots):
                                slots.append((label_start,label_end,'',None))
                                result['duration_format']={'span':[first,last],'scope':'table_header' if header is not None else view['kind'],
                                                           'source':text[label_start:label_end],'rendered_as':'spoken_duration_units'}
                    slots.append((a,b,spoken,result))
        if guard_slots and records:
            records.sort(key=lambda row:(row['view'].get('row',0),row['view'].get('column',0),row['expression_span']))
        return Projection(text,slots,block.id),records

    def report(self, events):
        if self.review is not None:
            self.policy['context_review']=self.accepted_answers
        visible={event['occurrence_id'] for event in events}
        result={'configuration_sha256':self.identity,'score_kind':'heuristic',
                'routes':dict(Counter(event['route'] for event in events)),
                'review_requests':[row for row in self.requests if row['occurrence_id'] in visible],
                'review_requests_omitted':max(0,self.request_count-REVIEW_LIMIT),
                'unmatched_reviewer_occurrences':len(self.answers.keys()-self.used_answers)}
        receipt=self.policy.get('context_review_receipt')
        if isinstance(receipt,dict) and receipt.get('configuration_sha256')==self.identity and receipt.get('source_sha256')==self.doc.source_sha256:
            result['review_provider']=receipt
        return result
