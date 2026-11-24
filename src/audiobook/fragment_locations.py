"""Keep internal links intact when embedding an ordered parsed fragment."""

from .core import key


def rebase_blocks(blocks,scope):
    ids = {block.id:'b'+key([*scope,block.id])[:16] for block in blocks}
    destinations = {'source.html#'+old:'source.html#'+new for old,new in ids.items()}

    def rebase_link(link):
        if 'destinations' in link:
            link['destinations'] = [destinations.get(value,value) for value in link['destinations']]
        if 'destination' in link:
            value = link['destination']
            link['destination'] = destinations.get(value,value)

    for block in blocks:
        block.id = ids[block.id]
        for link in block.links:
            rebase_link(link)
        for cell in block.locator.get('table_cell_semantics',()):
            for link in cell.get('notes',()):
                rebase_link(link)
        for link in (block.locator.get('table_caption') or {}).get('notes',()):
            rebase_link(link)
