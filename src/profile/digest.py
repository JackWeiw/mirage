"""Signature-digest accessors — thin derivations over the faithful call_tree +
topdown_tree + business_model on a Profile (spec §7).

These are accessors, NOT a stored IR: materialize into a stored digest only when
derivation cost or synthesis-context size triggers it (spec §8.1 sunset). Living
in `profile/` (not on the pydantic Profile) keeps the schema clean and avoids a
profile->codegen import edge — `archetype_of` takes the ArchetypeInferrer from
the caller (downstream-driven contract).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from profile.profile_schema import CallTreeNode, Profile, Stage, TopdownNode


def hotspot_subtree(profile: Profile, top_k: int) -> list[CallTreeNode]:
    """The top_k hottest call-sites (by self_pct) across the call_tree forest."""
    if profile.call_tree is None:
        return []
    flat: list[CallTreeNode] = []

    def walk(node: CallTreeNode) -> None:
        flat.append(node)
        for child in node.children:
            walk(child)

    for root in profile.call_tree:
        walk(root)
    flat.sort(key=lambda n: n.self_pct, reverse=True)
    return flat[:top_k]


def dominant_bottleneck(profile: Profile) -> str | None:
    """Dotted path of the customer's dominant topdown bottleneck: descend the
    max-value child from the largest L1 root. None if no topdown_tree."""
    if not profile.topdown_tree:
        return None

    def deepest(node: TopdownNode) -> str:
        path = [node.name.lower()]
        current = node
        while current.children:
            current = max(current.children, key=lambda c: c.value)
            path.append(current.name.lower())
        return ".".join(path)

    root = max(profile.topdown_tree, key=lambda r: r.value)
    return deepest(root)


def per_module_self_pct(profile: Profile) -> dict[str, float]:
    """Aggregate self_pct by library across the call_tree (None library -> 'custom')."""
    if profile.call_tree is None:
        return {}
    agg: dict[str, float] = {}

    def walk(node: CallTreeNode) -> None:
        key = node.library or "custom"
        agg[key] = agg.get(key, 0.0) + node.self_pct
        for child in node.children:
            walk(child)

    for root in profile.call_tree:
        walk(root)
    return agg


def stages(profile: Profile) -> list[Stage]:
    """Business-model stages if the structured descriptor is present, else []."""
    if profile.business_model is None:
        return []
    return profile.business_model.stages


def archetype_of(node: CallTreeNode, inferrer: object) -> str:
    """Infer a leaf archetype via a caller-supplied ArchetypeInferrer (codegen).

    The inferrer is injected so the profile layer has no codegen import edge.
    Returns 'compute' if the inferrer has no `infer` callable.
    """
    infer = getattr(inferrer, "infer", None)
    if not callable(infer):
        return "compute"
    result: object = infer(node.function)
    return result if isinstance(result, str) else "compute"
