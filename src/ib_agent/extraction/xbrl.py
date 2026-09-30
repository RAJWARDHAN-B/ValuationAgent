"""XBRL company facts -> flat `Fact` rows, and the canonical tag map."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, model_validator

from ib_agent.errors import ConfigError
from ib_agent.models.financials import Fact

DEFAULT_TAXONOMY = "us-gaap"
TAXONOMIES = ("us-gaap", "dei")
_ITEM_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_TAG_NAME = re.compile(r"^(?:(?:us-gaap|dei):)?[A-Z][A-Za-z0-9]*$")


def qualify(tag: str) -> str:
    return tag if ":" in tag else f"{DEFAULT_TAXONOMY}:{tag}"


@dataclass(frozen=True)
class Component:
    name: str
    sign: int
    optional: bool

    @property
    def is_item(self) -> bool:
        return _ITEM_NAME.match(self.name) is not None


def parse_component(raw: str) -> Component:
    sign = -1 if raw.startswith("-") else 1
    name = raw.lstrip("-")
    optional = name.endswith("?")
    return Component(name=name.rstrip("?"), sign=sign, optional=optional)


class TagMapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: Literal["IS", "BS", "CF"]
    unit: Literal["USD", "shares", "USD/shares"] = "USD"
    sign: Literal["as_reported", "outflow_positive"] = "as_reported"
    ltm: Literal["roll_forward", "latest"] = "roll_forward"
    tags: list[str] = Field(min_length=1)
    sum_of: list[str] | None = Field(default=None, min_length=1)

    @property
    def is_instant(self) -> bool:
        return self.statement == "BS"

    @property
    def qnames(self) -> list[str]:
        return [qualify(t) for t in self.tags]

    @property
    def components(self) -> list[Component]:
        return [parse_component(c) for c in self.sum_of or []]

    @model_validator(mode="after")
    def validate_names(self) -> TagMapEntry:
        for tag in self.tags:
            if not _TAG_NAME.match(tag):
                raise ValueError(f"invalid XBRL tag {tag!r}")
        for component in self.components:
            if not (component.is_item or _TAG_NAME.match(component.name)):
                raise ValueError(f"invalid sum_of component {component.name!r}")
        return self


class TagMap(RootModel[dict[str, TagMapEntry]]):
    """Ordered: `sum_of` may only reference canonical items defined earlier."""

    @model_validator(mode="after")
    def validate_references(self) -> TagMap:
        seen: dict[str, TagMapEntry] = {}
        for item, entry in self.root.items():
            if not _ITEM_NAME.match(item):
                raise ValueError(f"invalid line item name {item!r}")
            for component in entry.components:
                if not component.is_item:
                    continue
                ref = seen.get(component.name)
                if ref is None:
                    raise ValueError(f"{item}: sum_of references {component.name!r} before it")
                if ref.is_instant != entry.is_instant or ref.unit != entry.unit:
                    raise ValueError(f"{item}: sum_of component {component.name!r} is incompatible")
            seen[item] = entry
        return self

    def items(self) -> list[tuple[str, TagMapEntry]]:
        return list(self.root.items())

    def units_by_qname(self) -> dict[str, set[str]]:
        units: dict[str, set[str]] = {}
        for _, entry in self.items():
            raw_components = [qualify(c.name) for c in entry.components if not c.is_item]
            for qname in entry.qnames + raw_components:
                units.setdefault(qname, set()).add(entry.unit)
        return units


def load_tag_map(path: Path) -> TagMap:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Missing XBRL tag map: {path}") from exc
    try:
        return TagMap.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid {path}:\n{exc}") from exc


def tidy_facts(company_facts: dict[str, Any], tag_map: TagMap) -> list[Fact]:
    """Flatten `facts[taxonomy][tag]["units"][unit]` for tags and units used by the map."""
    wanted = tag_map.units_by_qname()
    rows: list[Fact] = []
    for taxonomy in TAXONOMIES:
        for tag, concept in company_facts.get("facts", {}).get(taxonomy, {}).items():
            units = wanted.get(f"{taxonomy}:{tag}")
            if not units:
                continue
            for unit, entries in concept.get("units", {}).items():
                if unit not in units:
                    continue
                for entry in entries:
                    fact = _parse_fact(taxonomy, tag, unit, entry)
                    if fact is not None:
                        rows.append(fact)
    return rows


def _parse_fact(taxonomy: str, tag: str, unit: str, entry: dict[str, Any]) -> Fact | None:
    try:
        return Fact(
            taxonomy=taxonomy,
            tag=tag,
            unit=unit,
            value=float(entry["val"]),
            start=date.fromisoformat(entry["start"]) if entry.get("start") else None,
            end=date.fromisoformat(entry["end"]),
            fy=entry.get("fy"),
            fp=entry.get("fp"),
            form=entry["form"],
            filed=date.fromisoformat(entry["filed"]),
            accession=entry["accn"],
            frame=entry.get("frame"),
        )
    except (KeyError, TypeError, ValueError):
        return None
