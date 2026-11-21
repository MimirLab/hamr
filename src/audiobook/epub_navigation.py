"""Project chapter navigation onto ordered source occurrences, never select prose.

Broken/auxiliary targets remain inspectable. Prefix content and repeated source
occurrences stay in the primary reading flow even if navigation omits them.
"""
import copy
from .core import Block,key,Error


def route(blocks,entries,soup):
    records=[];warnings=[]
    for index,entry in enumerate(entries):
        item={**entry,'entry':index}
        try:
            document=soup(entry['path']);body=document.find('body') or document
            target=body.find(id=entry['anchor']) if entry['anchor'] else body
            if target is None:raise ValueError('missing anchor')
            ranks={id(n):i for i,n in enumerate(body.descendants)}
            item.update(status='resolved',rank=ranks.get(id(target),-1))
        except (Error,ValueError):
            item['status']='unresolved';warnings.append('EPUB navigation target could not be resolved: '+entry['path']+'#'+entry['anchor'])
        records.append(item)
    by_path={}
    for item in records:
        if item['status']=='resolved':by_path.setdefault(item['path'],[]).append(item)
    result=[];chapter=None;chapter_id=None;active=False;occurrence=None;pending=[]
    for block in blocks:
        loc=block.locator;current_occurrence=loc['occurrence']
        if occurrence!=current_occurrence:
            occurrence=current_occurrence
            pending=sorted(by_path.get(loc['path'],[]),key=lambda e:(e['rank'],e['entry']))
        base_length=loc.get('base_text_length',len(block.source))
        spans=loc.get('inline_semantics',[])
        starts={s['id']:s['source_start'] for s in spans if s['kind']=='anchor'}
        root_rank=loc.get('dom_rank',loc.get('text_node_rank',-1))
        cuts={0,base_length};boundary_ranks={0:root_rank}
        for entry in pending:
            offset=starts.get(entry['anchor'])
            if offset is None or not 0<offset<base_length:continue
            protected=any(s['kind'] in ('mathml','note_reference','pronunciation_hint','ruby') and s['source_start']<offset<s['source_end'] for s in spans)
            in_word=block.source[offset-1].isalnum() and block.source[offset].isalnum()
            if block.role not in ('body','heading','caption') or protected or in_word:
                boundary_ranks[0]=max(boundary_ranks[0],entry['rank'])
                warnings.append('Chapter anchor uses its enclosing source block to preserve a protected span: '+entry['path']+'#'+entry['anchor'])
            else:cuts.add(offset);boundary_ranks[offset]=entry['rank']
        cuts=sorted(cuts);intervals=list(zip(cuts,cuts[1:])) or [(0,0)]
        for start,end in intervals:
            rank=boundary_ranks.get(start,root_rank)
            reached=[e for e in pending if e['rank']<=rank]
            if reached:
                # Multiple labels at one boundary do not create empty chapters.
                latest=max(e['rank'] for e in reached)
                chosen=next(e for e in reached if e['rank']==latest)
                chapter=chosen['label'];chapter_id='epub:'+str(occurrence)+':'+chosen['path']+'#'+chosen['anchor']
                active=True;pending=[e for e in pending if e not in reached]
            elif not active and (chapter!=block.chapter or block.role=='heading'):
                chapter=block.chapter;chapter_id='epub-prefix:'+str(occurrence)+':'+block.id
            if len(intervals)==1:part=block
            else:
                part=copy.deepcopy(block);part.locator['source_range']=[start,end]
                part.id='b'+key({'origin':block.id,'source_range':[start,end]})[:16]
                part.links=[{**link,**({'source_start':link['source_start']-start} if 'source_start' in link else {})} for link in block.links if start<=link.get('source_start',base_length-1)<end]
                part.source=block.source[start:end]
                selected=[]
                for span in spans:
                    a,b=span['source_start'],span['source_end']
                    intersects=(a<b and a<end and b>start) or (a==b and start<=a<end) or (a==b==end==base_length)
                    if not intersects:continue
                    item={**span,'source_start':max(start,a)-start,'source_end':min(end,b)-start}
                    item['source']=part.source[item['source_start']:item['source_end']];selected.append(item)
                part.locator['inline_semantics']=selected
                part.evidence.append('Source interval bounded by authored chapter navigation; ordered text retained')
            part.chapter=chapter or block.chapter;part.locator['chapter_id']=chapter_id or 'epub:'+str(occurrence)+':'+block.id
            result.append(part)
    return result,records,warnings
