"""Use outline coordinates and heading occurrences, never page-only ranges."""


def apply(blocks,inventory,entries):
    pages={p['page']:p for p in inventory};mapped=[]
    canonical=lambda text:''.join(c.lower() for c in text if c.isalnum())
    for entry in entries:
        page=pages.get(entry.get('page'));candidates=[]
        if page and page.get('rotation',0)==0 and page.get('native_geometry_qualified',True):
            for i,b in enumerate(blocks):
                if b.locator.get('page')!=entry['page'] or canonical(b.source)!=canonical(entry['title']):continue
                rect=b.locator.get('rect');y=entry.get('top')
                if y is not None:y-=page.get('visible_cropbox',[0,0,0,0])[1]
                if y is None or rect and rect[1]-14<=y<=rect[3]+14:candidates.append((i,b))
        if len(candidates)!=1:entry['status']='unresolved';continue
        i,b=candidates[0];entry.update(status='source_matched',source_block=b.id);mapped.append((i,entry,b))
    starts={}
    for i,entry,b in sorted(mapped,key=lambda v:(v[0],v[1]['entry'])):
        if i in starts:continue
        starts[i]=(entry['title'],'pdf-outline:'+str(entry['entry']));b.role='heading'
        b.evidence.append('PDF outline destination agrees with this unique heading occurrence and qualified geometry')
    if starts:
        title='Opening';identity='pdf-outline:prefix'
        for i,b in enumerate(blocks):
            if i in starts:title,identity=starts[i]
            b.chapter=title;b.locator['chapter_id']=identity
    return entries
