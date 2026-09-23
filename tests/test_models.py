import pytest

from src.funding_radar.models import Round, normalize_company, normalize_domain


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Acme AI Ltd.", "acme ai"),
        ("Métis Energy B.V.", "metis energy"),
        ("Zopa Limited", "zopa"),
        ("Föra Technologies AB", "fora technologies"),
        ("Orbital", "orbital"),
        ("N26 GmbH", "n26"),
        ("Lovable  ", "lovable"),
    ],
)
def test_company_names_normalise_for_matching(raw, expected):
    assert normalize_company(raw) == expected


def test_a_bare_suffix_is_never_stripped_to_nothing():
    assert normalize_company("Limited") == "limited"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.Acme.co.uk/about", "acme.co.uk"),
        ("http://acme.ai", "acme.ai"),
        ("https://sub.acme.io/careers?x=1", "sub.acme.io"),
        ("", ""),
    ],
)
def test_domains_normalise(raw, expected):
    assert normalize_domain(raw) == expected


def test_a_round_id_is_stable_for_one_company_and_month():
    first = Round(company="Acme AI Ltd", stage="Series A", round_date="2026-09-20")
    second = Round(company="Acme AI", stage="series a", round_date="2026-09-28")
    assert first.round_id_for("d:acme.ai") == second.round_id_for("d:acme.ai")


def test_a_relabelled_round_keeps_its_identity():
    """Outlets disagree on seed vs Series A; the round is still the same round."""
    seed = Round(company="Acme AI", stage="Seed", round_date="2026-09-20")
    series_a = Round(company="Acme AI", stage="Series A", round_date="2026-09-20")
    assert seed.round_id_for("d:acme.ai") == series_a.round_id_for("d:acme.ai")


def test_different_companies_never_collide():
    acme = Round(company="Acme AI", stage="Seed", round_date="2026-09-20")
    other = Round(company="Other AI", stage="Seed", round_date="2026-09-20")
    assert acme.round_id_for("d:acme.ai") != other.round_id_for("d:other.ai")
