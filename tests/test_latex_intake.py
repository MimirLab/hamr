from pathlib import Path
from zipfile import ZipFile

from audiobook.intake import inspect_source, inspect_saved
from audiobook.planner import plan
from audiobook.source_bundle import capture


ROOT = r"""\documentclass{book}
\title{A short source control}
\newcommand{\sample}{labelled control}
\includeonly{chapters/start}
\begin{document}
\maketitle
\include{chapters/start}
\include{unused}
\input chapters/last
\end{document}
"""
CHAPTER = r"""\chapter[Short title]{Opening chapter}
The \emph{first reader} prints this \sample. Follow this note\footnote{The reading keeps every word.}.
Next comes math $x^2+1$.

\begin{enumerate}
\item Read the first clause.
\item[Aside] Keep this thought separate.
\item Read the second clause.
\end{enumerate}

\begin{tabular}{ll}
Rock & Salt \\
Moss & Dirt \\
\end{tabular}

\begin{verbatim}
\documentclass{article}
\input{not-a-dependency}
\end{verbatim}

\begin{verbatim*}
Literal unmatched { brace
\end{verbatim*}

\begin{minted}[linenos]{text}
\input{not-a-dependency}
\end{minted}
"""


def make_project(tmp_path):
    root = tmp_path/'project'
    (root/'chapters').mkdir(parents=True)
    members = {'main.tex': ROOT, 'chapters/start.tex': CHAPTER,
               'chapters/last.tex': '% '+('% ' * 1000)+'\n'+
                   r'\chapter{Closing chapter} The last words are present.',
               'unused.tex': 'This chapter is excluded.'}
    for path, text in members.items():
        (root/path).write_bytes(text.encode('utf-8'))
    archive = tmp_path/'project.zip'
    with ZipFile(archive,'w') as target:
        for name,text in reversed(list(members.items())):
            target.writestr(f'project/{name}',text)
    return root,archive,members


def say(result):
    return [row['text'] for row in result['utterances']]


def test_latex_direct_folder_archive_share_order_math_notes_tables_and_lineage(tmp_path):
    root,archive,members = make_project(tmp_path)
    documents = [inspect_source(path) for path in (root/'main.tex',root,archive)]
    plans = [plan(doc) for doc in documents]
    assert all(not result['issues'] for result in plans)
    assert say(plans[0])==say(plans[1])==say(plans[2])
    narration = ' '.join(say(plans[0]))
    assert 'labelled control' in narration and 'x to the power of 2 plus 1' in narration
    assert 'Note. The reading keeps every word. End note.' in narration
    assert 'Number one, Read the first clause.' in narration
    assert 'Aside. Keep this thought separate. Number two, Read the second clause.' in narration
    assert 'Table. Rock; Salt. Moss; Dirt.' in narration
    assert narration.endswith('Closing chapter The last words are present.')
    assert 'excluded' not in narration
    assert any('not-a-dependency' in b.source for b in documents[0].blocks if b.role=='code')
    assert any('unmatched { brace' in b.source for b in documents[0].blocks if b.role=='code')
    for doc in documents:
        heading = next(b for b in doc.blocks if b.source=='Opening chapter')
        assert Path(heading.locator['source_file']).name=='start.tex'
        a,b = heading.locator['character_range']
        assert CHAPTER[a:b].startswith(r'\chapter[Short title]')
        assert heading.locator['lines']==[1,1]


def test_latex_snapshot_contains_reachable_members_and_survives_working_edit(tmp_path):
    root,_,members = make_project(tmp_path)
    dest=tmp_path/'snapshot'
    dest.mkdir()
    saved,source = capture(root,dest)
    with ZipFile(saved) as archive:
        assert set(archive.namelist())==set(members)-{'unused.tex'}
        for name in archive.namelist():
            assert archive.read(name)==members[name].encode()
    (root/'chapters/start.tex').write_text('The working copy changed.')
    assert 'labelled control' in ' '.join(say(plan(inspect_saved(saved,source))))


def test_latex_unresolved_condition_does_not_open_either_body_or_hide_later_prose(tmp_path):
    source=tmp_path/'document.tex'
    source.write_text(r"""\documentclass{article}
\begin{document}
The introduction is here.
\ifx\one\two
\input{rejected}
\else
\input{alternative}
\fi
The closing paragraph is available.
\end{document}
""")
    (tmp_path/'rejected.tex').write_text('An unselected string.')
    (tmp_path/'alternative.tex').write_text('Another unselected string.')
    result=plan(inspect_source(source))
    assert len(result['issues'])==1 and 'conditional' in result['issues'][0]['reason']
    assert say(result)==['The introduction is here.','The closing paragraph is available.']


def test_latex_missing_escaped_paths_and_cycles_are_scoped(tmp_path):
    root=tmp_path/'book'
    root.mkdir()
    source=root/'main.tex'
    source.write_text(r"""\documentclass{article}
\begin{document}
\input{missing}
\input{../outside}
\input{loop}
The final chapter still reads.
\end{document}
""")
    (root/'loop.tex').write_text(r'\input{loop}')
    (tmp_path/'outside.tex').write_text('Escaped material.')
    result=plan(inspect_source(source))
    assert len(result['issues'])==3
    assert say(result)==['The final chapter still reads.']
    assert all(issue['location']['source_file'].endswith('.tex') for issue in result['issues'])


def test_latex_input_within_sentence_keeps_continuity_and_both_sources(tmp_path):
    root = tmp_path/'book'
    root.mkdir()
    main = root/'main.tex'
    main.write_text(r'The keeper \input{motion} through the mist.')
    part = root/'motion.tex'
    part.write_text('watched the harbor')
    archive = tmp_path/'book.zip'
    with ZipFile(archive,'w') as output:
        for file in (main,part):
            output.write(file,'edition/'+file.name)
    for path,entry in ((main,None),(root,'main.tex'),(archive,'1')):
        doc = inspect_source(path,entry=entry)
        assert say(plan(doc))==['The keeper watched the harbor through the mist.']
        block, = doc.blocks
        fragments = block.locator['source_fragments']
        assert [Path(span['source_file']).name for span in fragments]==['main.tex','motion.tex','main.tex']
        if path==archive:
            assert [span['member'] for span in fragments]==[
                'edition/main.tex','edition/motion.tex','edition/main.tex']
        for text,span,expected in zip((main.read_text(),part.read_text(),main.read_text()),fragments,
                                    ('The keeper ','watched the harbor',' through the mist.')):
            start,end = span['character_range']
            assert text[start:end]==expected
