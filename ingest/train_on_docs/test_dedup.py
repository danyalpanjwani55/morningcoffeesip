"""Tests for Engine 2 — dedup-vs-wiki (2.4).

Pins the spec's bar:
  * an anchor the wiki already cites -> dropped (Tier-1 exact);
  * a brand-new anchor -> kept;
  * a near-text restatement of a known fact -> dropped @0.82 (Tier-2 near);
  * an empty wiki -> all novel;
  * a contradiction / update -> KEPT (never silently dropped; resolver + review
    adjudicate supersede/conflict — NOT dedup's call).

The wiki-page fixtures mirror the format ``agent_wiki_builder`` emits: a
``## Source anchors`` block of ``_anchor_md`` citations and a ``## What's known``
block of fact bullets — so Tier-1/Tier-2 are tested against the REAL shape.

Deterministic; stdlib only; no network.
"""

from __future__ import annotations

from genesis_contracts import Anchor, Event, InMemoryCorpus

import agent_wiki_builder as awb
from ingest.train_on_docs.dedup import dedup_anchors_vs_wiki


def _corpus():
    """Corpus with the text behind several source docs (for Tier-2)."""
    return InMemoryCorpus(
        [
            Event("e1", "2026-06-19T08:00:00Z", "email",
                  "the list price is set at 5200 dollars per unit", "src-price", "m1"),
            Event("e2", "2026-06-20T08:00:00Z", "email",
                  "the warranty period covers twenty four months of coverage",
                  "src-warranty", "m2"),
            Event("e3", "2026-06-21T08:00:00Z", "email",
                  "a brand new and entirely unrelated topic about hiring a designer",
                  "src-new", "m3"),
            # A genuine PARAPHRASE of the warranty fact (not identical text) —
            # exercises the Tier-2 boundary rather than a similarity of 1.0.
            Event("e4", "2026-06-22T08:00:00Z", "email",
                  "the warranty period covers twenty four full months of coverage",
                  "src-warranty-para", "m4"),
        ]
    )


def _wiki_page(*, anchors, known):
    """A wiki page in the builder's emitted shape: a Source-anchors citation
    block (via ``_anchor_md``) + a What's-known fact-bullet block."""
    lines = [
        "---",
        "status: 🟡 UNVERIFIED",
        "---",
        "",
        "# Source 01 — src-price",
        "",
        "## What's known (load-bearing — each bound to the source)",
        "",
    ]
    for fact in known:
        lines.append(f"- {fact}")
    lines += ["", "## Source anchors (citations)", ""]
    for a in anchors:
        lines.append(f"- {awb._anchor_md(a)}")
    lines += ["", "## Cross-links", "", "- index: ../index.md", ""]
    return "\n".join(lines) + "\n"


def test_already_cited_anchor_is_dropped_tier1_exact():
    cited = Anchor("src-price", "email", "m1")
    page = _wiki_page(anchors=[cited], known=["the list price is set at 5200"])
    res = dedup_anchors_vs_wiki([cited], _corpus(), [page])
    assert res.novel_anchors == []
    assert res.duplicates == [cited]
    assert res.verdicts[0].tier == "exact"


def test_brand_new_anchor_is_kept():
    cited = Anchor("src-price", "email", "m1")
    fresh = Anchor("src-new", "email", "m3")
    page = _wiki_page(anchors=[cited], known=["the list price is set at 5200"])
    res = dedup_anchors_vs_wiki([fresh], _corpus(), [page])
    assert res.novel_anchors == [fresh]
    assert res.duplicates == []
    assert res.verdicts[0].tier == "novel"


def test_near_text_restatement_is_dropped_tier2_at_threshold():
    # The wiki states the warranty fact; the candidate's corpus text is a genuine
    # PARAPHRASE (not the identical string — adds "full") -> Tier-2 near-dup. The
    # score must clear 0.82 yet be strictly below 1.0 (proving the BOUNDARY, not
    # an identical-string shortcut).
    para_anchor = Anchor("src-warranty-para", "email", "m4")
    page = _wiki_page(
        anchors=[Anchor("src-other", "email", "zz")],  # different citation -> not Tier-1
        known=["the warranty period covers twenty four months of coverage"],
    )
    res = dedup_anchors_vs_wiki([para_anchor], _corpus(), [page])
    assert res.duplicates == [para_anchor], res.verdicts
    assert res.verdicts[0].tier == "near"
    assert 0.82 <= res.verdicts[0].score < 1.0


def test_empty_wiki_keeps_everything():
    a1 = Anchor("src-price", "email", "m1")
    a2 = Anchor("src-warranty", "email", "m2")
    res = dedup_anchors_vs_wiki([a1, a2], _corpus(), [])
    assert res.novel_anchors == [a1, a2]
    assert res.all_novel is True


def test_high_similarity_contradiction_is_kept_not_silently_dropped():
    # THE SAFETY-BEARING CASE (spec line 94): the wiki states ``list_price = 5200``;
    # a NEW doc states ``list_price = 4900`` — a same-shape UPDATE that is ~0.88
    # TEXT-similar (above 0.82), so plain Tier-2 would DROP it. The fact-value
    # guard must KEEP it (tier "update"), because silently dropping a correction
    # is exactly the failure the never-silently-delete rule forbids.
    update = Anchor("src-price-v2", "email", "m9")
    corpus = InMemoryCorpus(
        [
            Event("e9", "2026-06-25T08:00:00Z", "email",
                  "list_price = 4900", "src-price-v2", "m9"),
        ]
    )
    page = _wiki_page(
        anchors=[Anchor("src-price", "email", "m1")],
        known=["list_price = 5200"],
    )
    res = dedup_anchors_vs_wiki([update], corpus, [page])
    # proven kept DESPITE crossing the similarity threshold:
    assert update in res.novel_anchors
    assert update not in res.duplicates
    assert res.verdicts[0].tier == "update"
    assert res.verdicts[0].score >= 0.82


def test_identical_value_restatement_is_still_a_duplicate():
    # Guard the guard: a SAME-value restatement (``list_price = 5200`` again) is a
    # true duplicate, NOT a contradiction — it must still be dropped, so the
    # contradiction guard can't be used to smuggle re-statements back in.
    restate = Anchor("src-price-again", "email", "m7")
    corpus = InMemoryCorpus(
        [
            Event("e7", "2026-06-24T08:00:00Z", "email",
                  "list_price = 5200", "src-price-again", "m7"),
        ]
    )
    page = _wiki_page(
        anchors=[Anchor("src-price", "email", "m1")],
        known=["list_price = 5200"],
    )
    res = dedup_anchors_vs_wiki([restate], corpus, [page])
    assert restate in res.duplicates
    assert res.verdicts[0].tier == "near"
