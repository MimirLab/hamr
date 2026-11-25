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


def test_markdown_math_uses_the_same_reading_as_latex_and_mathml(tmp_path):
    sources = {
        'md':'Dr. Lane found $x^2+1$ on the page.',
        'tex':'Dr. Lane found $x^2+1$ on the page.',
        'html':('<p>Dr. Lane found <math><mrow><msup><mi>x</mi><mn>2</mn></msup><mo>+</mo>'
                '<mn>1</mn></mrow></math> on the page.</p>'),
    }
    for extension,source in sources.items():
        path = tmp_path/f'formula.{extension}'
        path.write_text(source,encoding='utf-8')
        result = plan(inspect_source(path))
        assert not result['issues']
        assert say(result)==['Doctor Lane found x to the power of 2 plus 1 on the page.']


def test_markdown_math_keeps_prices_literals_and_note_ownership(tmp_path):
    path = tmp_path/'control.md'
    path.write_text(
        'She paid $15 and wrote $x^2+1$.[^a]\n\n'
        'The price was $5 and C$. Keep `$x$` as written.\n\n'
        '[^a]: Here $y$ is the constant.\n',encoding='utf-8')
    doc = inspect_source(path)
    result = plan(doc)
    assert not result['issues']
    speech = ' '.join(say(result))
    assert 'She paid fifteen dollars and wrote x to the power of 2 plus 1.' in speech
    assert 'Note. Here y is the constant. End note.' in speech
    assert 'The price was five dollars and C$. Keep $x$ as written.' in speech
    body = [item for item in doc.blocks if item.role=='body']
    formulas = [span for span in body[0].locator['inline_semantics'] if span['kind']=='formula']
    assert [span['source'] for span in formulas]==['$x^2+1$']
    assert all(span['kind']!='formula' for span in body[1].locator['inline_semantics'])


def test_local_math_macros_respect_arguments_scope_and_definition_locations(tmp_path):
    defs = r'\newcommand{\pair}[2][A]{(#1, #2)}'
    (tmp_path/'defs.tex').write_text(defs,encoding='utf-8')
    main = tmp_path/'book.tex'
    source = r"""\documentclass{article}
\input{defs}
\begin{document}
First read $\pair{y''}$.

\begin{quote}
\renewcommand{\pair}[2][B]{(#1, #2)}
Then read $\pair{x'}$.
\end{quote}

Finally read $\pair[C]{y}$ and $\pair{z}$.
\end{document}
"""
    main.write_text(source,encoding='utf-8')
    doc = inspect_source(main)
    result = plan(doc)
    assert not result['issues']
    assert say(result)==[
        'First read open parenthesis A comma y double prime close parenthesis.',
        'Then read open parenthesis B comma x prime close parenthesis.',
        'Finally read open parenthesis C comma y close parenthesis and open parenthesis A comma z close parenthesis.',
    ]
    formulas = [span for block in doc.blocks for span in block.locator.get('inline_semantics',())
                if span['kind']=='formula']
    assert len(formulas)==4
    for span in formulas:
        where = span['source_locator']
        start,end = where['character_range']
        assert source[start:end]==span['source']
        origin, = span['macro_definitions']
        start,end = origin['character_range']
        original = defs if origin['source_file']=='defs.tex' else source
        assert '\\pair' in original[start:end] and origin['command']=='pair'
    assert [span['macro_definitions'][0]['source_file'] for span in formulas]==[
        'defs.tex','book.tex','defs.tex','defs.tex']


def test_math_macro_expansion_bounds_and_refuses_other_tex_operations(tmp_path):
    main = tmp_path/'book.tex'
    main.write_text(r"""\documentclass{article}
\newcommand{\loop}[1]{\loop{#1}}
\newcommand{\localfile}[1]{\input{#1}}
\begin{document}
This expression is $\loop{x}$.

This expression is $\localfile{hidden}$.

The regular prose is still present.
\end{document}
""",encoding='utf-8')
    (tmp_path/'hidden.tex').write_text('Hidden data must remain outside this reading.',encoding='utf-8')
    result = plan(inspect_source(main))
    assert len(result['issues'])==2
    assert 'budget' in result['issues'][0]['reason']
    assert say(result)==['The regular prose is still present.']


def test_linked_style_projects_declared_arguments_in_prose_and_retains_definition_bytes(tmp_path):
    root = tmp_path/'project'
    root.mkdir()
    main = root/'book.tex'
    style = root/'terms.sty'
    source = r"""\documentclass{article}
\usepackage{terms}
\begin{document}
The \term{harbor master} opens the gate. A \term*{keeper} can wait.

Use the \choice{safe lane} and the \choice[eastern]{light}.
\end{document}
"""
    declarations = r"""\ExplSyntaxOn
\NewDocumentCommand{\term}{sm}{
  \IfBooleanTF{#1}{\textbf{#2}}{\texttt{#2}}
}
\ExplSyntaxOff
\newcommand{\choice}[2][western]{#1 #2}
"""
    main.write_bytes(source.encode())
    style.write_bytes(declarations.encode())
    archive = tmp_path/'book.zip'
    with ZipFile(archive,'w') as output:
        output.write(style,'edition/terms.sty')
        output.write(main,'edition/book.tex')
    for path in (main,root,archive):
        doc = inspect_source(path)
        result = plan(doc)
        assert not result['issues']
        assert say(result)==[
            'The harbor master opens the gate. A keeper can wait.',
            'Use the western safe lane and the eastern light.',
        ]
        term = next(row for row in doc.inventory if row.get('kind')=='latex_declared_text')
        start,end = term['character_range']
        assert source[start:end]==r'\term{harbor master}'
        definition = term['definition']
        start,end = definition['character_range']
        assert Path(definition['source_file']).name=='terms.sty'
        assert declarations[start:end].startswith(r'\NewDocumentCommand{\term}{sm}')
        assert any(Path(row['member']).name=='terms.sty' for row in doc.metadata['source_resources'])

    target = tmp_path/'snapshot'
    target.mkdir()
    saved,source_ref = capture(main,target)
    style.write_text('The working style changed.',encoding='utf-8')
    assert any('western safe lane' in item for item in say(plan(inspect_saved(saved,source_ref))))


def test_style_template_never_loads_generated_paths_or_declarations_in_literal_examples(tmp_path):
    main = tmp_path/'book.tex'
    main.write_text(r"""\documentclass{article}
\usepackage{terms}
\begin{document}
This needs \dynamic{secret}.

This example cannot activate \listed{the gate}.

The published body remains readable.
\end{document}
""",encoding='utf-8')
    (tmp_path/'terms.sty').write_text(r"""\NewDocumentCommand{\dynamic}{m}{\input{#1}}
\begin{verbatim}
\RequirePackage{other}
\NewDocumentCommand{\listed}{m}{Do not emit #1}
\end{verbatim}
""",encoding='utf-8')
    (tmp_path/'other.sty').write_text(r'\NewDocumentCommand{\listed}{m}{Do not emit #1}',encoding='utf-8')
    (tmp_path/'secret.tex').write_text('Hidden material.',encoding='utf-8')
    destination = tmp_path/'snapshot'
    destination.mkdir()
    saved,source = capture(main,destination)
    with ZipFile(saved) as snapshot:
        assert set(snapshot.namelist())=={'book.tex','terms.sty'}
    report = plan(inspect_saved(saved,source))
    assert len(report['issues'])==2
    assert say(report)==['The published body remains readable.']


def test_an_existing_literal_shorthand_can_contain_authored_math(tmp_path):
    main = tmp_path/'book.tex'
    main.write_text(r"""\documentclass{article}
\newcommand{\value}{$x^2$}
\begin{document}
The value is \value.
\end{document}
""",encoding='utf-8')
    result = plan(inspect_source(main))
    assert not result['issues']
    assert say(result)==['The value is x to the power of 2.']


def test_inline_literals_and_layout_wrappers_preserve_source_order(tmp_path):
    main = tmp_path/'book.tex'
    source = r"""\documentclass{article}
\usepackage{sample}
\begin{document}
\begin{chapterintro}
The sea is still.
\end{chapterintro}

The \begin{spellout}\mintinline{latex}|\begin|\end{spellout} marker is literal.

\begin{commands}
\texsnippet|\end{document}|\\
\lstinline!}{!
\end{commands}

The harbor lights come on.
\end{document}
"""
    main.write_text(source,encoding='utf-8')
    style = r"""\newmintinline[texsnippet]{latex}{}
\NewDocumentEnvironment{spellout}{}{}{}
\newenvironment{chapterintro}{\sffamily}{\vspace{2em minus 1em}}
\NewDocumentEnvironment{commands}{}{
 \begin{trivlist}\item\relax
 \begin{tabular}{|l|}\hline\ignorespaces
}{\\\hline\end{tabular}\end{trivlist}}
\verb|\RequirePackage{ignored}|
"""
    (tmp_path/'sample.sty').write_text(style,encoding='utf-8')
    (tmp_path/'ignored.sty').write_text(r'\newcommand{\marker}{Secret}',encoding='utf-8')
    archive = tmp_path/'edition.zip'
    with ZipFile(archive,'w') as target:
        target.writestr('edition/book.tex',source)
        target.writestr('edition/sample.sty',style)
    for path in (main,tmp_path,archive):
        document = inspect_source(path)
        result = plan(document)
        assert not result['issues']
        assert len(result['utterances'])==4
        assert result['utterances'][0]['text']=='The sea is still.'
        assert result['utterances'][-1]['text']=='The harbor lights come on.'
        prose = next(block for block in document.blocks if 'marker is literal' in block.source)
        assert prose.source==r'The \begin marker is literal.'
        assert any(span['kind']=='literal' and span['source']==r'\begin'
                   for span in prose.locator['inline_semantics'])
        code = next(block for block in document.blocks if block.role=='code')
        assert code.source=='\\end{document}\n}{'
        origin = code.locator['environment_definition']
        assert Path(origin['source_file']).name=='sample.sty'
        start,end = origin['character_range']
        assert style[start:end].startswith(r'\NewDocumentEnvironment{commands}')
        assert not any(Path(row['member']).name=='ignored.sty' for row in document.metadata['source_resources'])


def test_generated_example_preserves_raw_input_without_lexing_it_as_book(tmp_path):
    main = tmp_path/'book.tex'
    main.write_text(r"""\documentclass{book}
\usepackage{sample}
\begin{document}
The first sentence is complete.

\begin{demo}[narrow]
\end{document}
Unbalanced { input and example text \end{demo} are literal.
\end{demo}

The next sentence is still present.
\end{document}
""",encoding='utf-8')
    (tmp_path/'sample.sty').write_text(r"""\ExplSyntaxOn
\NewDocumentEnvironment{demo}{!o}{
  \verbatim_set_processline:N \collect_example:n
  \verbatim_start:
}{\produce_example:}
\ExplSyntaxOff
""",encoding='utf-8')
    document = inspect_source(main)
    result = plan(document)
    assert len(result['issues'])==1
    assert result['issues'][0]['reason']=="The generated output of LaTeX 'demo' needs a verified reading."
    assert say(result)==['The first sentence is complete.','The next sentence is still present.']
    example = next(block for block in document.blocks if 'Unbalanced' in block.source)
    assert example.role=='navigation'
    assert example.source.startswith(r'\end{document}')
    assert example.locator['source_control'] is True


def test_forwarded_missing_empty_and_star_arguments_have_distinct_meaning(tmp_path):
    main = tmp_path/'book.tex'
    main.write_text(r"""\documentclass{article}
\NewDocumentCommand{\labeltext}{m}{\IfNoValueTF{#1}{absent}{present}}
\NewDocumentCommand{\wrapper}{o}{\labeltext{#1}}
\NewDocumentCommand{\startext}{m}{\IfBooleanTF{#1}{open}{closed}}
\NewDocumentCommand{\starwrapper}{s}{\startext{#1}}
\begin{document}
The option is \wrapper. The empty option is \wrapper[].
The gate is \starwrapper*. The door is \starwrapper.
\end{document}
""",encoding='utf-8')
    result = plan(inspect_source(main))
    assert not result['issues']
    assert ' '.join(say(result))=='The option is absent. The empty option is present. The gate is open. The door is closed.'


def test_literal_display_retains_unverified_rendered_escapes_and_layout_text(tmp_path):
    main = tmp_path/'book.tex'
    main.write_text(r"""\documentclass{article}
\setminted{escapeinside=«»}
\NewDocumentEnvironment{dynamic}{}{\sffamily The beginning.}{}
\begin{document}
This example uses \mintinline{latex}|«\textbf{an escape}»|.

\begin{dynamic}The middle.\end{dynamic}

The ending is present.
\end{document}
""",encoding='utf-8')
    result = plan(inspect_source(main))
    assert len(result['issues'])==2
    assert 'escapes verified' in result['issues'][0]['reason']
    assert "LaTeX 'dynamic' environment" in result['issues'][1]['reason']
    assert say(result)==['The ending is present.']


def test_document_bootstrap_keeps_conditional_body_unresolved_and_ignores_suffix(tmp_path):
    main = tmp_path/'book.tex'
    main.write_text(r"""\documentclass{article}
\iffalse
\begin{document}This branch is unverified.\end{document}
\fi
\newmintinline[snippet]{latex}{}
\begin{document}
The \snippet|\begin| marker appears here.
\end{document}
\begin{ignored
""",encoding='utf-8')
    result = plan(inspect_source(main))
    assert len(result['issues'])==1
    assert 'conditional' in result['issues'][0]['reason']
    assert say(result)==[r'The \begin marker appears here.']
