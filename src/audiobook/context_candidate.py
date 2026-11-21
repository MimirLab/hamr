"""Shared candidate values and exact component readings."""
from dataclasses import dataclass

from .spoken_forms import identifier
from .typed_readings import decimal

@dataclass(frozen=True)
class Candidate:
    kind: str
    fields: dict
    spoken: str | None
    evidence: tuple = ()


def literal(raw, spoken=None, *, fields=None, kind='literal'):
    return Candidate(kind, {'surface': raw, **(fields or {})}, raw if spoken is None else spoken, ('fallback',))


def numeric_fields(raw):
    clean = raw.replace('−','-')
    digits = clean.lstrip('+-')
    return {'source_amount':raw,'sign':clean[0] if clean.startswith(('+','-')) else '',
            'source_digits':digits}


def number_spoken(raw):
    return decimal(raw.replace('−','-'))


def exact_components(raw, separator, spoken_separator):
    return (' '+spoken_separator+' ').join(identifier(field) for field in raw.split(separator))
