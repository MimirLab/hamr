"""Map explicit TeX math tokens to the shared expression model."""

import re

from .source_math import Expression, OPERATORS


TOKENS = re.compile(r'\\[a-zA-Z]+|\\[^\n]|[^\s]')
SINGLE = {'cdot':'·','times':'×','div':'÷','pm':'±','le':'≤','leq':'≤','ge':'≥','geq':'≥',
          'ne':'≠','neq':'≠','infty':'∞','lparen':'(','rparen':')','lbrack':'[','rbrack':']'}
NAMES = {'sin':'sine','cos':'cosine','tan':'tangent','log':'log','ln':'natural log','exp':'exponential'}
GREEK = frozenset('alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron '
                  'pi rho sigma tau upsilon phi chi psi omega'.split())
SPACING = frozenset((',',':',';','!','quad','qquad','left','right',' '))
FUNCTIONS = frozenset(NAMES.values())


def separated_product(previous, current):
    before = previous.kind!='mo' and previous.text not in FUNCTIONS or previous.text in (')',']')
    after = current.kind!='mo' or current.text in ('(','[')
    return before and after


class Math:
    def __init__(self,text):
        self.tokens = TOKENS.findall(text)
        if len(self.tokens)>4096:
            raise ValueError('The TeX equation exceeds the token limit.')
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
            while (script:=self.peek()) in ('_','^'):
                self.take()
                if script in scripts or expr.kind=='mi' and expr.text in FUNCTIONS:
                    raise ValueError('The TeX equation uses an unsupported script.')
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
            if command in NAMES or command.lower() in GREEK:
                return Expression('mi',text=NAMES.get(command,command.lower()))
            if command=='approx':
                return Expression('mo',text='approximately equals')
            token = SINGLE.get(command,token)
        if token.isalpha():
            return Expression('mi',text=token)
        if token in OPERATORS:
            return Expression('mo',text=token)
        raise ValueError('The TeX equation uses an unsupported token.')


def tex_math(text):
    return Math(text).group('',0)
