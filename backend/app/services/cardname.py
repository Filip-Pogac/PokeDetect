"""Shared vocabulary for the parts of a card name that identify a printing.

A Pokemon card's name is not just the Pokemon. "Darmanitan", "Galarian
Darmanitan" and "Galarian Darmanitan VMAX" are three different cards, in
different sets, at different numbers, rarities and prices - so the regional
form prefix and the class suffix are load-bearing, not decoration.

They are also the two pieces most easily lost. Both print in a smaller or more
stylised font than the base name, so OCR routinely breaks them onto a line of
their own, and fuzzy string matching treats a missing modifier as a near-miss
("Darmanitan" scores 0.9 against "Galarian Darmanitan VMAX") rather than as
what it really is: a different card.

This module is the single place that knows those words. It is deliberately
dependency-free and shared by all three stages that need them:

- vision, which stitches stray modifier lines back onto the name it read;
- carddb.resolve_name, which must not snap a modified reading onto a bare name;
- imagematch, which penalises candidates whose modifiers disagree with it.

Only words that genuinely appear inside TCGdex card names belong here. A word
that never appears in the database (a rarity marker like "Tera", say) would be
read off the card, found missing from every candidate, and penalise them all.
"""
from __future__ import annotations

import re

# Modifiers printed *before* the base name: regional forms, and the era
# prefixes that are spelled as part of the name in the card database.
FORM_PREFIXES = frozenset(
    {
        "alolan", "galarian", "hisuian", "paldean",
        "dark", "light", "shining", "radiant", "shadow",
        "primal", "mega", "origin", "forme", "detective", "surfing", "flying",
    }
)

# Class/subtype markers printed *after* it, usually in the stylised font that
# makes them the first thing OCR drops.
SUBTYPE_TOKENS = frozenset(
    {
        "ex", "gx", "v", "vmax", "vstar", "vunion", "union",
        "break", "prime", "legend", "star",
    }
)

ALL_MODIFIERS = FORM_PREFIXES | SUBTYPE_TOKENS

# Penalties used when comparing the modifiers of a reading against those of a
# candidate. They are asymmetric on purpose - see agreement().
_MISSING_PENALTY = 0.4
_EXTRA_PENALTY = 0.15


def words(text: str) -> list[str]:
    """Lowercased alphabetic words of a name, punctuation dropped.

    Comparison is word-level throughout this module, which is what keeps
    "Darkrai" from registering as carrying the "Dark" prefix.
    """
    return re.sub(r"[^A-Za-z ]", " ", text or "").lower().split()


def modifiers(name: str) -> set[str]:
    """The form/subtype words in a name - the parts that make it specific."""
    return {w for w in words(name) if w in ALL_MODIFIERS}


def base_name(name: str) -> str:
    """The name with its modifiers stripped: "Galarian Darmanitan VMAX" -> "Darmanitan".

    Used as a widening fallback: an exact modified name matches only one or two
    printings, so if the modifier was misread there is nothing else in the pool
    to recover with.
    """
    kept = [w for w in (name or "").split() if not modifiers(w)]
    return " ".join(kept).strip()


def modifier_kind(line: str) -> str | None:
    """"prefix"/"suffix" if a line holds *only* modifiers, else None.

    OCR regularly puts "Galarian" or "VMAX" on their own line. On their own they
    are not names - the name cleaner rightly rejects them - but discarding them
    is exactly what turns a Galarian Darmanitan VMAX into a Darmanitan, so they
    are classified here to be rejoined with the neighbouring line.
    """
    tokens = words(line)
    if not tokens or len(tokens) > 2:
        return None
    if all(t in FORM_PREFIXES for t in tokens):
        return "prefix"
    if all(t in SUBTYPE_TOKENS for t in tokens):
        return "suffix"
    return None


def agreement(read_name: str | None, candidate_name: str) -> float:
    """0-1 agreement between the modifiers of a reading and of a candidate.

    Symmetric in shape but not in severity:

    - a modifier that was *read* but is absent from the candidate is strong
      evidence against it - "VMAX" is not a word OCR invents, so a plain
      "Darmanitan" is simply the wrong card;
    - a modifier the candidate carries but the reading lacks is weaker
      evidence, because a stylised "VMAX" is precisely what OCR misses.

    Returns 1.0 when there is nothing to compare, so a card with no modifiers
    on either side is never penalised.
    """
    if not read_name:
        return 1.0
    read_mods = modifiers(read_name)
    candidate_mods = modifiers(candidate_name)
    if read_mods == candidate_mods:
        return 1.0
    missing = len(read_mods - candidate_mods)
    extra = len(candidate_mods - read_mods)
    return max(0.0, 1.0 - _MISSING_PENALTY * missing - _EXTRA_PENALTY * extra)
