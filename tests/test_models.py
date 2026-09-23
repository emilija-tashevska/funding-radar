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


def test_same_round_reported_twice_shares_an_id():
    first = Round(company="Acme AI Ltd", stage="Series A", round_date="2026-09-20", company_domain="acme.ai")
    second = Round(company="Acme AI", stage="series a", round_date="2026-09-28", company_domain="acme.ai")
    assert first.round_id == second.round_id


def test_a_later_round_at_a_new_stage_is_a_separate_row():
    seed = Round(company="Acme AI", stage="Seed", round_date="2026-01-10", company_domain="acme.ai")
    series_a = Round(company="Acme AI", stage="Series A", round_date="2026-09-10", company_domain="acme.ai")
    assert seed.round_id != series_a.round_id


def test_companies_without_a_domain_still_match_by_name():
    first = Round(company="Metris Energy", stage="Seed", round_date="2026-09-02")
    second = Round(company="Metris Energy Ltd.", stage="seed", round_date="2026-09-20")
    assert first.round_id == second.round_id
