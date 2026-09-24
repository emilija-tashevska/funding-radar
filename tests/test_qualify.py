import pytest

from src.funding_radar.models import Round
from src.funding_radar.qualify import Rules, approx_usd, is_confirmed, normalize_stage, qualifies

CONFIG = {
    "filters": {
        "min_amount": 2_000_000,
        "stages": ["pre-seed", "seed", "series a", "series b"],
        "regions": ["uk", "europe"],
        "investor_floors": {"Antler": 300_000, "Entrepreneur First": 300_000},
    }
}
RULES = Rules.from_config(CONFIG)


def _round(**overrides) -> Round:
    values = dict(company="Acme AI", stage="Series A", amount_value=5_000_000,
                  currency="GBP", region="uk", company_domain="acme.ai")
    values.update(overrides)
    return Round(**values)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Series A", "series a"), ("series A funding", "series a"), ("Pre-Seed", "pre-seed"),
        ("pre seed round", "pre-seed"), ("Seed", "seed"), ("Series B extension", "series b"),
        ("Series D", "series d+"), ("growth round", "growth"), ("venture debt", "debt"),
        ("Innovate UK grant", "grant"), ("", ""), ("unknown", ""),
    ],
)
def test_stages_normalise_to_one_label(raw, expected):
    assert normalize_stage(raw) == expected


def test_pre_seed_is_not_read_as_seed():
    assert normalize_stage("pre-seed round") == "pre-seed"


def test_a_qualifying_round_passes():
    assert qualifies(_round(), RULES).qualified


@pytest.mark.parametrize(
    "overrides,expected_reason",
    [
        ({"region": "us"}, "outside the brief"),
        ({"stage": "Series D"}, "outside the brief"),
        ({"stage": "venture debt"}, "outside the brief"),
        ({"amount_value": 500_000}, "below the"),
    ],
)
def test_rounds_outside_the_brief_are_rejected_with_a_reason(overrides, expected_reason):
    verdict = qualifies(_round(**overrides), RULES)
    assert not verdict.qualified and expected_reason in verdict.reason


def test_antler_and_ef_lower_the_floor_for_their_companies():
    small = _round(amount_value=500_000, stage="pre-seed")
    assert not qualifies(small, RULES).qualified
    backed = _round(amount_value=500_000, stage="pre-seed", investors=["Antler", "Angel X"])
    assert qualifies(backed, RULES).qualified
    ef = _round(amount_value=400_000, stage="pre-seed", investors=["Entrepreneur First"])
    assert qualifies(ef, RULES).qualified


def test_the_lower_floor_is_still_a_floor():
    assert not qualifies(_round(amount_value=100_000, stage="pre-seed", investors=["Antler"]), RULES).qualified


def test_an_untracked_investor_does_not_lower_the_floor():
    assert not qualifies(_round(amount_value=500_000, investors=["Some Angel Syndicate"]), RULES).qualified


def test_an_undisclosed_amount_is_kept_for_review():
    verdict = qualifies(_round(amount_value=None), RULES)
    assert verdict.qualified and "undisclosed" in verdict.reason


def test_currencies_are_compared_without_pretending_to_be_precise():
    assert approx_usd(2_000_000, "EUR") == 2_000_000       # taken as reported
    assert approx_usd(20_000_000, "SEK") == pytest.approx(1_900_000)  # converted roughly
    assert approx_usd(None, "GBP") is None


def test_confirmation_needs_specifics_plus_either_a_domain_or_a_second_outlet():
    assert is_confirmed(_round())                                    # domain + amount + stage
    assert not is_confirmed(_round(amount_value=None))               # no amount
    assert not is_confirmed(_round(stage="", stage_raw=""))          # no stage
    assert not is_confirmed(_round(company_domain=""))               # unresolved, single outlet
    assert is_confirmed(_round(company_domain=""), distinct_outlets=2)


def test_a_round_with_no_stage_named_still_qualifies_on_size():
    """Headlines often omit the stage; those rounds stay in the unconfirmed tier."""
    unnamed = _round(stage="", stage_raw="")
    assert qualifies(unnamed, RULES).qualified
    assert not is_confirmed(unnamed)
