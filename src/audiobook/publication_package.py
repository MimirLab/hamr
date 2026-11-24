"""Original member access and snapshots shared by source publications."""

import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from .core import Error, key
from .source_bundle import copy_stream, relative_file, safe_name, stamp


def folder_files(root, *, exclude=()):
    result = {}

    def fail(error):
        raise error

    for path,dirs,files in os.walk(root,onerror=fail,followlinks=False):
        here = Path(path)
        dirs[:] = [name for name in dirs if (here/name).relative_to(root).as_posix() not in exclude]
        for name in dirs:
            child = here/name
            if child.is_symlink() or child.is_junction():
                raise Error('unsafe_source_member','A publication directory entry follows a link.')
        for name in files:
            relative = (here/name).relative_to(root).as_posix()
            child = relative_file(root,relative)
            result[relative] = stamp(child.stat())
    return result


def capture_folder(root,target,files,*,verify=None,include_root=True):
    listed = files()
    budget = sum(value[2]+1024 for value in listed.values())
    if shutil.disk_usage(target.parent).free<budget:
        raise Error('disk_space',f'Need at least {budget:,} bytes to save the original publication.')
    with ZipFile(target,'x',compression=ZIP_STORED,allowZip64=True) as archive:
        for name,before in sorted(listed.items()):
            source = relative_file(root,name)
            if stamp(source.stat())!=before:
                raise Error('source_changed',f'Publication member changed during capture: {name}.')
            record = ZipInfo(safe_name(root.name+'/'+name if include_root else name))
            record.create_system = 3
            record.external_attr = (stat.S_IFREG|0o400)<<16
            with archive.open(record,'w',force_zip64=True) as destination:
                copy_stream(source,destination)
        if (verify(listed) if verify else files())!=listed:
            raise Error('source_changed','Publication members changed during capture.')


def member_bytes(archive,member,limit):
    if member.size is not None and member.size>limit:
        raise Error('source_structure_size',f'Publication member exceeds its reading budget: {member.name}.')
    with archive.open_member(member) as stream:
        data = stream.read(limit+1)
    if len(data)>limit:
        raise Error('source_structure_size',f'Publication member exceeds its reading budget: {member.name}.')
    return data


def member_location(sha,member,role):
    return {'member':member.name,'archive_entry':member.index,'size':member.size,'role':role,
            'location_id':key([sha,member.index,member.name])[:20]}


def bind_member_block(block,member,occurrence,sha,records,resolve):
    block.locator.update(member=member.name,archive_entry=member.index,spine_occurrence=occurrence,
                         location_id=key([sha,member.index,occurrence,block.id])[:20])
    bind_resources(block,lambda ref:records.get(resolve(member.name,ref['href'])))


def bind_resources(block,lookup):
    for ref in block.locator.pop('resource_references',[]):
        asset = lookup(ref) if ref['kind']=='image' else None
        if asset and asset['role']=='asset':
            asset['status'] = 'referenced'
            block.links.append({'kind':'source_resource','href':ref['href'],'label':ref.get('label'),
                                'destinations':['source.html#resource-'+asset['location_id']]})


def archive_packages(archive,members,*,textpack=False):
    """Use the same publication boundaries for discovery and page sequences."""
    from .fictionbook_package import archive_books as fb3_books
    from .gitbook_intake import archive_spaces
    from .mdbook_intake import archive_books
    from .ncc_intake import archive_books as ncc_books
    from .odf_presentation import archive_books as odp_books
    from .opf_intake import archive_books as opf_books
    from .presentation_intake import archive_books as pptx_books
    from .pml_package import archive_books as pml_books
    from .scrivener_intake import archive_books as scrivener_books
    from .sphinx_intake import archive_books as sphinx_books
    from .textbundle import archive_scopes
    from .xml_package import archive_books as xml_books
    from .xps_intake import archive_books as xps_books

    books,claimed = scrivener_books(members) if not textpack else ([],set())
    packages = [('scrivener',item,PurePosixPath(item.name).stem) for item in books]
    bundles,owned = archive_scopes(archive,[item for item in members if item.index not in claimed],explicit=textpack)
    packages.extend(('textbundle',item,PurePosixPath(item.name).parent.as_posix()) for item in bundles)
    claimed |= owned
    if textpack:
        return packages,claimed
    remaining = [item for item in members if item.index not in claimed]
    editions,xps_members = xps_books(archive,remaining)
    packages.extend(('xps',descriptor,title) for descriptor,title in editions)
    remaining = [item for item in remaining if item.index not in xps_members]
    editions,fb3_members = fb3_books(archive,remaining)
    packages.extend(('fb3',descriptor,title) for descriptor,title in editions)
    remaining = [item for item in remaining if item.index not in fb3_members]
    slides,pptx_members = pptx_books(archive,remaining)
    packages.extend(('pptx',descriptor,title) for descriptor,title in slides)
    remaining = [item for item in remaining if item.index not in pptx_members]
    slides,odp_members = odp_books(archive,remaining)
    packages.extend(('odp',descriptor,title) for descriptor,title in slides)
    remaining = [item for item in remaining if item.index not in odp_members]
    spaces,owned = archive_spaces(archive,remaining)
    packages.extend(('gitbook',item,book.title or book.root or 'GitBook') for book,item in spaces)
    claimed |= owned
    remaining = [item for item in remaining if item.index not in owned]
    books,owned = archive_books(archive,remaining)
    packages.extend(('mdbook',book.summary,book.root or book.source) for book in books)
    remaining = [item for item in remaining if item.index not in owned]
    projects,project_members = sphinx_books(archive,remaining)
    packages.extend(('sphinx',config,book.title or book.root or 'Sphinx') for book,config in projects)
    remaining = [item for item in remaining if item.index not in project_members]
    editions,opf_members = opf_books(archive,remaining)
    packages.extend((book.route,descriptor,book.title) for book,descriptor in editions)
    remaining = [item for item in remaining if item.index not in opf_members]
    titles,ncc_members = ncc_books(archive,remaining)
    packages.extend(('daisy202',descriptor,book.title) for book,descriptor in titles)
    remaining = [item for item in remaining if item.index not in ncc_members]
    books,pml_members = pml_books(archive,remaining)
    packages.extend(books)
    remaining = [item for item in remaining if item.index not in pml_members]
    editions,xml_members = xml_books(archive,remaining)
    packages.extend(('xml-book',descriptor,title) for descriptor,title in editions)
    spaces,plain = archive_spaces(archive,[item for item in remaining if item.index not in xml_members],declared=False)
    packages.extend(('gitbook',item,book.root or 'GitBook') for book,item in spaces)
    return packages,claimed | xps_members | fb3_members | pptx_members | odp_members | owned | project_members | opf_members | ncc_members | pml_members | xml_members | plain
