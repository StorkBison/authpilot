"""Payer policy catalogue (loaded from YAML)."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

HERE = Path(__file__).parent


class Criterion(BaseModel):
    id: str
    text: str
    if_unmet: Literal["clarify", "deny"]
    any_of: list[dict[str, Any]]
    deny_if: list[dict[str, Any]] = []


class Policy(BaseModel):
    id: str
    service_code: str
    title: str
    summary: str
    criteria: list[Criterion]
    payer: str = ""

    def criterion(self, criterion_id: str) -> Criterion | None:
        return next((c for c in self.criteria if c.id == criterion_id), None)

    def for_agent(self) -> dict:
        """What the agent sees: the human-readable policy, not the payer's internal rules."""
        return {
            "policy_id": self.id,
            "payer": self.payer,
            "title": self.title,
            "summary": self.summary,
            "criteria": [
                {"id": c.id, "text": c.text, "if_unmet": c.if_unmet} for c in self.criteria
            ],
        }


@lru_cache
def load_policies() -> dict[str, Policy]:
    raw = yaml.safe_load((HERE / "northwind.yaml").read_text(encoding="utf-8"))
    return {p["id"]: Policy(payer=raw["payer"], **p) for p in raw["policies"]}


def policy_for_service(service_code: str) -> Policy | None:
    return next((p for p in load_policies().values() if p.service_code == service_code), None)
