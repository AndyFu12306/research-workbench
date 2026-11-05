"""Fixed allocation arithmetic uses the Issue #19 synthetic acceptance example."""

from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from research_workbench.analysis.cross_document_pipeline import (
    CrossBudget,
    CrossDocumentError,
    allocate_budget,
    check_allowance,
    exact_cost,
    validate_selection,
)
from research_workbench.analysis.document_pipeline import Pricing, RequestEstimate
from research_workbench.analysis.stage_contracts import StageBudget

PRICE = Pricing("USD", "synthetic-v1", Decimal("2"), Decimal("8"))


def estimates(*values):
    return tuple(RequestEstimate(v, v + 5120) for v in values)


def test_issue19_exact_allocation_and_dynamic_usage():
    groups = (estimates(8000, 10000), estimates(6000, 7000, 8000), estimates(12000))
    plan = allocate_budget(groups, StageBudget(), CrossBudget(10, 200000, Decimal("0.65")), PRICE)
    assert plan.calls == 10
    assert plan.context_tokens == 198200
    assert plan.cost == Decimal("0.621680")
    assert [sum(a.context_tokens for a in g) for g in plan.documents] == [57360, 65480, 46240]
    assert [sum(a.cost for a in g) for g in plan.documents] == [
        Decimal("0.182304"),
        Decimal("0.221072"),
        Decimal("0.137536"),
    ]
    dynamic = (15000, 22000, 4000, 18000)
    actual = [a for g in plan.documents for a in g[:-1]]
    actual += [
        replace(
            plan.cross, input_tokens=i, context_tokens=i + 5120, cost=exact_cost(PRICE, i, 4096)
        )
        for i in dynamic
    ]
    assert sum(a.context_tokens for a in actual) == 161200
    assert sum(a.cost for a in actual) == Decimal("0.547680")
    for n in (23999, 24000):
        check_allowance(plan.cross, RequestEstimate(n, n + 5120), StageBudget(), PRICE)
    with pytest.raises(CrossDocumentError):
        check_allowance(plan.cross, RequestEstimate(24001, 29121), StageBudget(), PRICE)


@pytest.mark.parametrize(
    "selection", [[1, 2], (), (1,), (1, 1), (True, 2), (0, 2), (1, 2, 3, 4, 5, 6)]
)
def test_explicit_ordered_selection_rejects_invalid_inputs(selection):
    with pytest.raises(CrossDocumentError):
        validate_selection(selection)


@pytest.mark.parametrize(
    "budget",
    [CrossBudget(9, 200000), CrossBudget(10, 198199), CrossBudget(10, 200000, Decimal("0.621679"))],
)
def test_allocations_fail_closed_at_limit(budget):
    with pytest.raises(CrossDocumentError):
        allocate_budget(
            (estimates(8000, 10000), estimates(6000, 7000, 8000), estimates(12000)),
            StageBudget(),
            budget,
            PRICE,
        )


def test_exact_price_is_independent_of_callers_decimal_context():
    with localcontext() as context:
        context.prec = 2
        assert exact_cost(PRICE, 8000, 4096) == Decimal("0.048768")
    assert exact_cost(PRICE, None, 40) is None
    assert exact_cost(None, 20, 40) is None


def test_source_character_limit_and_duplicate_identity_are_strict():
    from research_workbench.analysis.chunking import SourceDocument, SourceIdentity
    from research_workbench.analysis.cross_document_pipeline import FrozenSelection
    from research_workbench.persistence.models import utc_now

    docs = (
        SourceDocument(SourceIdentity("zotero", "personal", "FAKE0001"), "a" * 999999),
        SourceDocument(SourceIdentity("zotero", "personal", "FAKE0002"), "b"),
    )
    selection = FrozenSelection(
        1, (1, 2), "Question", docs, (utc_now(), utc_now()), "fake", "synthetic"
    )
    assert sum(len(d.text) for d in selection.documents) == 1_000_000
    with pytest.raises(CrossDocumentError):
        replace(selection, documents=(docs[0], SourceDocument(docs[1].identity, "bb")))
    with pytest.raises(CrossDocumentError):
        replace(selection, documents=(docs[0], SourceDocument(docs[0].identity, "b")))


def test_inexact_decimal_calculation_is_rejected_instead_of_rounded():
    price = Pricing("USD", "synthetic", Decimal("1." + "1" * 200), Decimal("8"))
    with pytest.raises(CrossDocumentError):
        exact_cost(price, 100, 40)


def test_exact_global_token_and_decimal_cost_boundary_passes():
    groups = (estimates(8000, 10000), estimates(6000, 7000, 8000), estimates(12000))
    plan = allocate_budget(
        groups, StageBudget(), CrossBudget(10, 198200, Decimal("0.621680")), PRICE
    )
    assert plan.context_tokens == 198200 and plan.cost == Decimal("0.621680")
