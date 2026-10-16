"""Preserve an explicit private-model boundary when a proposed need paraphrases it."""

import re


def private_model_boundary(text):
    """Require both an explicit unavailable statement and its model-internals scope."""
    unavailable = re.search(
        r"\b(?:do not|don't|cannot|can't|not familiar|not know|no access)\b|"
        r"(?:不掌握|不了解|不清楚|不知道|无法提供|不能提供|没有权限)",
        text,
        re.IGNORECASE,
    )
    scoped = re.search(
        r"(?:private|internal).{0,60}(?:layer|channel|architect|fusion)|"
        r"(?:私有|内部).{0,20}(?:结构|层|通道|融合)|层结构.{0,12}通道",
        text,
        re.IGNORECASE,
    )
    return bool(unavailable and scoped)


def asks_private_model_detail(target):
    return bool(
        re.search(
            r"tensor operations?|(?:channel.wise )?concat|cross.attention|"
            r"mathematical.{0,20}fusion|(?:private|internal).{0,30}(?:layer|channel|architect)|"
            r"(?:inside|enter|before).{0,25}DiT|DiT blocks|私有.{0,12}(?:结构|层|融合)|"
            r"层结构|内部.{0,12}(?:融合|通道)|通道数|数学.{0,10}融合|沿通道拼接",
            target,
            re.IGNORECASE,
        )
    )
