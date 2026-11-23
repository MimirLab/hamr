"""Build the small observer spelling table from pinned, local source archives."""

from argparse import ArgumentParser
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile


def given_names(archive):
    with ZipFile(archive) as names:
        return {name.lower() for part in ('male','female')
                for name in names.read(f'names/{part}.txt').decode('utf-8').splitlines()
                if re.fullmatch('[A-Za-z][A-Za-z]+',name)}


def pronunciation_groups(dictionary, names):
    pronunciations = defaultdict(set)
    with dictionary.open(encoding='utf-8') as lines:
        for line in lines:
            fields = line.partition('#')[0].split()
            if len(fields)<2:
                continue
            name = re.sub(r'\(\d+\)$','',fields[0].lower())
            if name in names:
                pronunciations[name].add(tuple(fields[1:]))
    groups = defaultdict(list)
    for name,phones in pronunciations.items():
        if len(phones)==1:
            groups[next(iter(phones))].append(name)
    return sorted(sorted(group) for group in groups.values() if len(group)>1)


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('dictionary',type=Path)
    parser.add_argument('names',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--dictionary-revision',required=True)
    args = parser.parse_args()
    assert re.fullmatch('[0-9a-f]{40}',args.dictionary_revision)
    groups = pronunciation_groups(args.dictionary,given_names(args.names))
    table = {'sources':{
        'cmudict':{'revision':args.dictionary_revision,'sha256':checksum(args.dictionary)},
        'names':{'version':'1.3','sha256':checksum(args.names)},
    },'groups':groups}
    args.output.write_text(json.dumps(table,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
