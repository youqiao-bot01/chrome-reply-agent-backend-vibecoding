"""
将 ``CreatorRuleContext`` 各字段收敛到 vocabulary canonical 值。
"""

from __future__ import annotations

from hubstudio_python.reply.service.rules.schema import CONDITION_SCALAR_FIELDS, CreatorRuleContext, GENERAL
from hubstudio_python.models.knowledge_chunk_vocabulary import get_vocabulary


def normalize_context(ctx: CreatorRuleContext) -> CreatorRuleContext:
    """就地规范化并返回同一对象。"""
    vocab = get_vocabulary()
    shop = str(ctx.applicable_shops or "").strip()
    if not shop or shop.upper() == GENERAL:
        shop = None
    for fname in CONDITION_SCALAR_FIELDS:
        raw = getattr(ctx, fname)
        if raw is None:
            continue
        if fname == "intent_category":
            if raw:
                ic = vocab.canonicalize_intent(raw, shop=shop)
                setattr(ctx, fname, ic)
            continue
        setattr(ctx, fname, _canon_scalar(fname, raw, shop=shop))

    if shop and shop.strip().lower() == "toolant":
        from hubstudio_python.models.toolant_creator_type import normalize_toolant_creator_type

        ctx.creator_type = normalize_toolant_creator_type(ctx.creator_type)

    flags = []
    seen: set[str] = set()
    allowed = vocab.flag_values_for_shop(shop)
    for f in ctx.other_creator_conditions:
        if f in allowed and f not in seen:
            seen.add(f)
            flags.append(f)
    ctx.other_creator_conditions = flags
    return ctx


def _canon_scalar(field: str, raw: object, shop: str | None = None) -> str | list[str]:
    vocab = get_vocabulary()
    if isinstance(raw, list):
        out = []
        for item in raw:
            c = vocab.canonicalize(field, item, shop=shop)
            if c and c != GENERAL:
                out.append(c)
        if not out:
            return GENERAL
        return out if len(out) > 1 else out[0]
    c = vocab.canonicalize(field, raw, default=GENERAL, shop=shop)
    return c or GENERAL
