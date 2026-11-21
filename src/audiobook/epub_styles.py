"""Bounded author CSS semantics; presentation is not inferred from class names."""
import re
import tinycss2

SEMANTIC={'display','visibility','content','vertical-align','speak','speak-as'}
AUDIT_ONLY={'text-transform'}
ATTRIBUTE=r'''\[\s*[\w:-]+(?:\s*[~|^$*]?=\s*(?:"[^"]*"|'[^']*'|[\w:-]+)\s*(?:[is])?)?\]'''
SIMPLE=r'(?:\*|[A-Za-z][\w-]*)?(?:[.#][\w-]+|'+ATTRIBUTE+r')*'
SELECTOR=re.compile(SIMPLE+r'(?:\s*(?:>|\s)\s*'+SIMPLE+r')*')


def properties(node):return vars(node).get('_audiobook_css',{})


def issues(node):
    return [message for parent in [node,*node.parents] for message in vars(parent).get('_audiobook_css_issues',[])]


def apply(document,load):
    """Apply a qualified static subset; preserve unresolved semantic CSS as review.

    Only local linked stylesheets and authored style elements are inspected.
    Unsupported semantic selectors/at-rules abstain rather than guessing.
    """
    body=document.find('body') or document;entries=[];warnings=[];order=0
    def problem(message):body.__dict__.setdefault('_audiobook_css_issues',[]).append(message)
    def declarations(value):return [d for d in tinycss2.parse_declaration_list(value,skip_comments=True,skip_whitespace=True)
                                    if d.type=='declaration' and d.lower_name in SEMANTIC|AUDIT_ONLY]
    for node in document.find_all(['link','style']):
        try:
            if node.name=='link':
                if 'stylesheet' not in str(node.get('rel','')) or not node.get('href'):continue
                raw,origin=load(node['href'])
            else:raw=node.get_text().encode();origin='authored style element'
            if len(raw)>1024*1024:problem('Authored stylesheet exceeds the bounded semantic parser.');continue
            rules,_=tinycss2.parse_stylesheet_bytes(raw,skip_comments=True,skip_whitespace=True)
        except Exception:
            message='Linked stylesheet could not be inspected; authored speech/visibility semantics require a verified source disposition.'
            warnings.append(message);problem(message);continue
        for rule in rules:
            if rule.type=='at-rule':
                if rule.lower_at_keyword=='import' or rule.content and re.search(r'(?:display|visibility|content|vertical-align|speak(?:-as)?)\s*:',tinycss2.serialize(rule.content),re.I):problem('Conditional or imported semantic CSS requires a source-supported disposition.')
                continue
            if rule.type!='qualified-rule':continue
            ds=declarations(rule.content)
            if not ds:continue
            for selector in tinycss2.serialize(rule.prelude).split(','):
                selector=selector.strip();pseudo=None
                match=re.search(r'::?(before|after)$',selector)
                if match:pseudo=match[1];selector=selector[:match.start()].strip()
                if not selector or not SELECTOR.fullmatch(selector) or any(not part.strip() for part in selector.split('>')):
                    if any(d.lower_name in SEMANTIC for d in ds):problem('Semantic CSS uses a selector outside the qualified static subset.')
                    continue
                try:nodes=document.select(selector)
                except Exception:
                    if any(d.lower_name in SEMANTIC for d in ds):problem('Semantic CSS selector could not be resolved.')
                    continue
                attributes=re.findall(ATTRIBUTE,selector);plain=re.sub(ATTRIBUTE,'',selector)
                specificity=(0,plain.count('#'),plain.count('.')+len(attributes),len(re.findall(r'(?:^|[ >])([A-Za-z][\w-]*)',plain)))
                for n in nodes:
                    for d in ds:entries.append((n,d,pseudo,specificity,order,origin,selector))
                order+=1
    for node in document.find_all(style=True):
        for d in declarations(node['style']):entries.append((node,d,None,(1,0,0,0),order,'inline style',node.name))
        order+=1
    for node,d,pseudo,specificity,sequence,origin,selector in entries:
        prop=d.lower_name
        if pseudo and prop!='content':continue
        if not pseudo and prop=='content':
            problem('Generated content outside a qualified before/after pseudo-element needs review.');continue
        if prop=='speak-as' and tinycss2.serialize(d.value).strip() not in ('normal','auto'):
            problem('Authored CSS speech rendering needs a verified reading for this backend.');continue
        if prop=='speak' and tinycss2.serialize(d.value).strip() not in ('normal','auto','none'):
            problem('Authored CSS speech rendering needs a verified reading for this backend.');continue
        value=tinycss2.serialize(d.value).strip();key=pseudo if prop=='content' else prop
        if prop=='content':
            tokens=[t for t in d.value if t.type!='whitespace'];parts=[];supported=True
            if len(tokens)==1 and tokens[0].type=='ident' and tokens[0].value in ('none','normal'):parts=[]
            else:
                for token in tokens:
                    if token.type=='string':parts.append(token.value)
                    elif token.type=='function' and token.lower_name=='attr':
                        args=[t for t in token.arguments if t.type!='whitespace']
                        if len(args)==1 and args[0].type=='ident':parts.append(str(node.get(args[0].value,'')))
                        else:supported=False
                    else:supported=False
            if not supported:problem('Authored generated content needs a verified reading; counters, images and dynamic content are not guessed.');continue
            value=''.join(parts)
        priority=(d.important,specificity,sequence);computed=node.__dict__.setdefault('_audiobook_css',{})
        if key not in computed or priority>=computed[key]['priority']:
            computed[key]={'value':value,'priority':priority,'origin':origin,'selector':selector}
    # Visibility descendants can override ancestors. Abstain on mixed cases;
    # do not let an ancestor shortcut silently hide the visible descendant.
    for node in document.find_all():
        value=properties(node).get('visibility',{}).get('value')
        if value=='visible' and any(properties(a).get('visibility',{}).get('value')=='hidden' for a in node.parents):problem('Mixed inherited visibility requires a source-supported reading.')
    return warnings
