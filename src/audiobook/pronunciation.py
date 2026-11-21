"""Source-scoped PLS aliases and explicit unsupported-phoneme dispositions."""
import re
from lxml import etree
from .core import digest


def attribute(node,name,inherit=False):
    for n in ([node,*node.parents] if inherit else [node]):
        if not hasattr(n,'attrs'):continue
        for k,v in n.attrs.items():
            if getattr(k,'namespace',None)=='http://www.w3.org/2001/10/synthesis' and getattr(k,'name',None)==name:return v
        if n.get('ssml:'+name):return n.get('ssml:'+name)
    return None


def linked(document,load):
    entries=[];problems=[]
    for link in document.find_all('link',href=True):
        if 'pronunciation' not in str(link.get('rel','')).split() or link.get('type')!='application/pls+xml':continue
        try:
            raw,path=load(link['href']);root=etree.fromstring(raw,etree.XMLParser(resolve_entities=False,no_network=True))
            if len(raw)>1024*1024 or etree.QName(root).namespace!='http://www.w3.org/2005/01/pronunciation-lexicon' or etree.QName(root).localname!='lexicon' or root.get('version')!='1.0':raise ValueError('Unsupported or oversized lexicon')
            lang=root.get('{http://www.w3.org/XML/1998/namespace}lang')
            if not lang or link.get('hreflang') and link['hreflang'].lower()!=lang.lower():raise ValueError('Lexicon language is missing or conflicts with hreflang')
            for lexeme in root.xpath('./*[local-name()="lexeme"]'):
                aliases=lexeme.xpath('./*[local-name()="alias"]/text()');phones=lexeme.xpath('./*[local-name()="phoneme"]/text()')
                for word in lexeme.xpath('./*[local-name()="grapheme"]/text()'):
                    if not str(word).strip() or len(str(word))>256 or any(len(str(v))>256 for v in aliases+phones) or len(entries)>=10000:raise ValueError('Lexicon entry exceeds qualified bounds')
                    entries.append({'word':str(word),'language':lang,'reading':str(aliases[0]) if len(aliases)==1 and not phones else None,'phonemes':[str(p) for p in phones],'alphabet':root.get('alphabet'),'origin':path,'source_sha256':digest(raw)})
        except Exception:problems.append('A linked pronunciation lexicon could not be resolved unambiguously; verify its source reading.')
    return entries,problems


def annotate(source,spans,entries,default_language):
    result=[]
    for entry in entries:
        for match in re.finditer(r'(?<!\w)'+re.escape(entry['word'])+r'(?!\w)',source):
            a,b=match.span();lang=default_language
            languages=[s for s in spans if s['kind']=='language' and s['source_start']<=a and s['source_end']>=b]
            if languages:lang=min(languages,key=lambda s:s['source_end']-s['source_start'])['language']
            if not lang or not (lang.lower()==entry['language'].lower() or lang.lower().startswith(entry['language'].lower()+'-')):continue
            shadowed=any(s['kind']=='pronunciation_hint' and s['source_start']<b and s['source_end']>a for s in spans)
            protected=any(s['kind'] in ('mathml','note_reference','ruby','hidden') and s['source_start']<b and s['source_end']>a for s in spans)
            result.append({'source_start':a,'source_end':b,'source':match[0],'kind':'lexicon_hint',**entry,'shadowed_by_inline':shadowed,'protected_overlap':protected})
    return result


def unresolved(block):
    spans=block.locator.get('inline_semantics',[]);messages=[]
    hidden=[s for s in spans if s['kind']=='hidden']
    for span in spans:
        if any(s['source_start']<=span['source_start'] and s['source_end']>=span['source_end'] for s in hidden):continue
        if span['kind']=='pronunciation_hint':messages.append('Publisher phoneme hint needs an auditioned source-bound reading; the pinned HTTP adapter has no qualified phoneme input.')
        if span['kind']=='lexicon_hint' and not span.get('shadowed_by_inline'):
            peers=[s for s in spans if s['kind']=='lexicon_hint' and s['source_start']==span['source_start'] and s['source_end']==span['source_end'] and not s.get('shadowed_by_inline')]
            if not span.get('reading') or span.get('protected_overlap') or len({s.get('reading') for s in peers})>1:messages.append('Publisher lexicon pronunciation is ambiguous or unsupported; provide a verified occurrence reading.')
    return list(dict.fromkeys(messages))
