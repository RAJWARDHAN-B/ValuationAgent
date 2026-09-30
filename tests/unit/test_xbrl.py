from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ib_agent.errors import ConfigError
from ib_agent.extraction.xbrl import TagMap, load_tag_map, parse_component, tidy_facts

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def test_repo_tag_map_loads() -> None:
    tag_map = load_tag_map(CONFIG_DIR / "xbrl_tag_map.yaml")
    items = dict(tag_map.items())
    assert (
        items["revenue"].qnames[0] == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    assert items["capex"].sign == "outflow_positive"
    assert items["total_assets"].is_instant and not items["revenue"].is_instant


def test_missing_tag_map(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Missing XBRL tag map"):
        load_tag_map(tmp_path / "nope.yaml")


def test_sum_of_must_reference_earlier_items(tmp_path: Path) -> None:
    path = tmp_path / "map.yaml"
    path.write_text("a: {statement: IS, tags: [A], sum_of: [b]}\nb: {statement: IS, tags: [B]}\n")
    with pytest.raises(ConfigError, match="references 'b' before it"):
        load_tag_map(path)


def test_sum_of_rejects_mixed_period_types() -> None:
    with pytest.raises(ValidationError, match="incompatible"):
        TagMap.model_validate(
            {
                "a": {"statement": "BS", "tags": ["A"]},
                "b": {"statement": "IS", "tags": ["B"], "sum_of": ["a"]},
            }
        )


@pytest.mark.parametrize("tag", ["lowercase", "Bad Tag", "ifrs:Revenue"])
def test_invalid_tags_rejected(tag: str) -> None:
    with pytest.raises(ValidationError):
        TagMap.model_validate({"a": {"statement": "IS", "tags": [tag]}})


def test_parse_component() -> None:
    component = parse_component("-OperatingLeaseLiabilityCurrent?")
    assert (component.name, component.sign, component.optional) == (
        "OperatingLeaseLiabilityCurrent",
        -1,
        True,
    )
    assert parse_component("revenue").is_item
    assert not parse_component("Revenues").is_item


def test_tidy_facts_keeps_mapped_tags_and_units_only() -> None:
    tag_map = TagMap.model_validate(
        {
            "revenue": {"statement": "IS", "tags": ["Revenues"]},
            "shares": {
                "statement": "BS",
                "unit": "shares",
                "tags": ["dei:EntityCommonStockSharesOutstanding"],
            },
        }
    )
    entry = {
        "end": "2024-12-31",
        "accn": "0000000001-25-000005",
        "form": "10-K",
        "filed": "2025-02-15",
    }
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {**entry, "start": "2024-01-01", "val": 10, "fy": 2024, "fp": "FY"},
                            {"end": "2024-12-31", "val": 1},  # malformed: no form / filed / accn
                        ],
                        "EUR": [{**entry, "start": "2024-01-01", "val": 9}],
                    }
                },
                "NotMapped": {"units": {"USD": [{**entry, "val": 5}]}},
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {"units": {"shares": [{**entry, "val": 7}]}}
            },
            "ifrs-full": {"Revenue": {"units": {"USD": [{**entry, "val": 3}]}}},
        }
    }

    facts = tidy_facts(payload, tag_map)

    assert sorted((f.qname, f.unit, f.value) for f in facts) == [
        ("dei:EntityCommonStockSharesOutstanding", "shares", 7.0),
        ("us-gaap:Revenues", "USD", 10.0),
    ]
    revenue = next(f for f in facts if f.tag == "Revenues")
    assert revenue.duration_days == 366
    assert revenue.accession == "0000000001-25-000005"
