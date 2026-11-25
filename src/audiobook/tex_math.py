"""Map explicit TeX math tokens to the shared expression model."""

from dataclasses import dataclass
from itertools import islice
import re

from .source_math import Expression, GREEK_NAMES, LIMIT_OPERATORS, OPERATORS, PRIME_MARKS, limit_operator


TOKENS = re.compile(r'\\[a-zA-Z]+|\\[^\n]|%[^\r\n]*|#[1-9]|[^\s]')
SINGLE = {'cdot':'·','times':'×','div':'÷','pm':'±','le':'≤','leq':'≤','ge':'≥','geq':'≥',
          'ne':'≠','neq':'≠','infty':'∞','lparen':'(','rparen':')','lbrack':'[','rbrack':']',
          'sum':'∑','prod':'∏','prime':'′','vert':'∣','mid':'∣'}
NAMES = {'sin':'sine','cos':'cosine','tan':'tangent','log':'log','ln':'natural log','exp':'exponential'}
GREEK = {name.removeprefix('capital ').capitalize() if name.startswith('capital ') else name:letter
         for letter,name in GREEK_NAMES.items() if letter!='ς'}
SPACING = frozenset((',',':',';','!','quad','qquad','left','right',' '))
FUNCTIONS = frozenset(NAMES.values())
STANDARD_COMMANDS = frozenset(SINGLE)|frozenset(NAMES)|frozenset(GREEK)|SPACING|{'frac','dfrac','tfrac','sqrt','approx'}
PRIMES = {"'":1, **PRIME_MARKS}
MAX_TOKENS = 4096


def tokenize(text):
    tokens = (match[0] for match in TOKENS.finditer(text) if not match[0].startswith('%'))
    return tuple(islice(tokens,MAX_TOKENS+1))


@dataclass(frozen=True)
class MathMacro:
    arity: int
    body: tuple[str,...]
    default: tuple[str,...] | None = None

    @classmethod
    def bound(cls,arity,body,default=None):
        parts = tokenize(body)
        if not parts or len(parts)>MAX_TOKENS or any(
                part.startswith('#') and (len(part)!=2 or int(part[1])>arity) for part in parts):
            return None
        optional = tokenize(default) if default is not None else None
        if optional is not None and len(optional)>MAX_TOKENS:
            return None
        return cls(arity,parts,optional)


def macro_argument(tokens,index,opening='{'):
    if index==len(tokens):
        raise ValueError('A LaTeX math macro is missing its argument.')
    if tokens[index]!=opening:
        if opening=='{' and tokens[index] not in ('}',']'):
            return (tokens[index],),index+1
        raise ValueError('A LaTeX math macro has an unbound argument.')
    nesting = 0
    closing = '}' if opening=='{' else ']'
    for end in range(index+1,len(tokens)):
        token = tokens[end]
        if token==closing and nesting==0:
            return tokens[index+1:end],end+1
        nesting += (token=='{')-(token=='}')
        if nesting<0:
            break
    raise ValueError('A LaTeX math macro argument is unclosed.')


def expand_macros(tokens,macros,used):
    budget = MAX_TOKENS*8

    def visit(items,depth):
        nonlocal budget
        budget -= len(items)
        if depth>16 or budget<0:
            raise ValueError('The LaTeX math macro expansion exceeds its reading budget.')
        cursor = 0
        while cursor<len(items):
            token = items[cursor]
            cursor += 1
            name = token[1:] if token.startswith('\\') else None
            if name not in macros:
                yield token
                continue
            macro = macros[name]
            used.add(name)
            if macro is None:
                raise ValueError(f'The LaTeX math macro {token} has no verified definition.')
            args = []
            if macro.default is not None:
                if cursor<len(items) and items[cursor]=='[':
                    first,cursor = macro_argument(items,cursor,'[')
                else:
                    first = macro.default
                args.append(first)
            while len(args)<macro.arity:
                arg,cursor = macro_argument(items,cursor)
                args.append(arg)
            body = []
            for part in macro.body:
                body.extend(args[int(part[1])-1] if part.startswith('#') else (part,))
                if len(body)>MAX_TOKENS:
                    raise ValueError('The LaTeX math macro replacement exceeds its reading budget.')
            yield from visit(body,depth+1)

    result = []
    for token in visit(tokens,0):
        result.append(token)
        if len(result)>MAX_TOKENS:
            raise ValueError('The expanded TeX equation exceeds the token limit.')
    return result


def separated_product(previous, current):
    before = (previous.kind!='mo' and previous.text not in FUNCTIONS or previous.text in (')',']')) and not limit_operator(previous)
    after = current.kind!='mo' or current.text in ('(','[') or limit_operator(current)
    return before and after


class Math:
    def __init__(self,text,macros,used):
        tokens = tokenize(text)
        if len(tokens)>MAX_TOKENS:
            raise ValueError('The TeX equation exceeds the token limit.')
        self.tokens = expand_macros(tokens,macros,used) if macros else tokens
        self.index = 0

    def peek(self,offset=0):
        position = self.index+offset
        return self.tokens[position] if position<len(self.tokens) else ''

    def take(self):
        token = self.peek()
        self.index += bool(token)
        return token

    def group(self,end,depth):
        if depth>16:
            raise ValueError('The TeX equation exceeds the nesting limit.')
        expressions = []
        while (token:=self.peek()) and token!=end:
            if token.startswith('\\') and token[1:] in SPACING:
                self.take()
                continue
            expr = self.atom(depth,merge_number=True)
            scripts = {}
            while (mark:=self.peek()) in ('_','^') or mark in PRIMES:
                script = '^' if mark in PRIMES else mark
                if script in scripts or expr.kind=='mi' and expr.text in FUNCTIONS:
                    raise ValueError('The TeX equation uses an unsupported script.')
                if mark in PRIMES:
                    count = 0
                    while self.peek() in PRIMES:
                        count += PRIMES[self.take()]
                    scripts[script] = Expression('mrow',(Expression('mo',text='′'),)*count)
                else:
                    self.take()
                    scripts[script] = self.atom(depth+1)
            if scripts:
                kind = 'msubsup' if len(scripts)==2 else 'msub' if '_' in scripts else 'msup'
                expr = Expression(kind,(expr,*(scripts[key] for key in ('_','^') if key in scripts)))
            if expressions and separated_product(expressions[-1],expr):
                expressions.append(Expression('mo',text='×'))
            expressions.append(expr)
        if end and self.take()!=end or not expressions:
            raise ValueError('A TeX group is empty or unclosed.')
        return Expression('mrow',tuple(expressions))

    def atom(self,depth,*,merge_number=False):
        if depth>16:
            raise ValueError('The TeX equation exceeds the nesting limit.')
        token = self.take()
        if token=='{':
            return self.group('}',depth+1)
        if token.isdecimal():
            while merge_number and self.peek().isdecimal():
                token += self.take()
            if merge_number and self.peek()=='.' and self.peek(1).isdecimal():
                token += self.take()
                while self.peek().isdecimal():
                    token += self.take()
            return Expression('mn',text=token)
        if token.startswith('\\'):
            command = token[1:]
            if command in ('frac','dfrac','tfrac'):
                return Expression('mfrac',(self.atom(depth+1),self.atom(depth+1)))
            if command=='sqrt':
                if self.peek()=='[':
                    self.take()
                    index = self.group(']',depth+1)
                    return Expression('mroot',(self.atom(depth+1),index))
                return Expression('msqrt',(self.atom(depth+1),))
            if command in NAMES or command in GREEK:
                return Expression('mi',text=NAMES.get(command,GREEK.get(command)))
            if command=='approx':
                return Expression('mo',text='approximately equals')
            token = SINGLE.get(command,token)
        if token.isalpha():
            return Expression('mi',text=token)
        if token in OPERATORS or token in LIMIT_OPERATORS:
            return Expression('mo',text=token)
        raise ValueError('The TeX equation uses an unsupported token.')


def tex_math(text,macros=None,used=None):
    return Math(text,macros,used if used is not None else set()).group('',0)
