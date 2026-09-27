"""What code does with a Jev answer: a policy per decision, never one threshold for all.

A Noul's probability is not a Choice's confidence, and a route is not a
grade, so each decision kind has its own thresholds. `verdict` turns an
answer into `yes`, `no` or `uncertain`; what each means is the caller's
(`main.knowledge`), and uncertain is never read as no.

The thresholds start as the prototype's 0.2/0.8, labelled provisional: they
were never fitted on this wiki. `tool/eval/policy.py` fits them on a labelled
calibration split and writes `eval/jev/policy.json`; a kind that file holds
for the model in use replaces the provisional one, and says it was fitted.
Fitted is a measured error rate on that split, not a claim that the model is
calibrated here — stage 10 holds the held-out evaluation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from common import settings

PROVISIONAL_VERSION = "provisional-1"
ARTIFACT = Path("eval") / "jev" / "policy.json"

NOUL = ("route", "source", "useful", "conflict", "redirect", "coverage")
CHOICE = ("repair", "relation")
KINDS = NOUL + CHOICE

# ponytail: the prototype's uncalibrated thresholds; `tool/eval/policy.py` replaces them per kind.
PROVISIONAL: dict[str, dict] = {
    **{kind: {"no": 0.2, "yes": 0.8} for kind in NOUL},
    **{kind: {"confidence": 0.6, "margin": 0.2} for kind in CHOICE},
}


def valid(kind: str, rule: object) -> bool:
    def unit(v):
        return type(v) in (int, float) and 0 <= v <= 1

    if not isinstance(rule, dict):
        return False
    if kind in NOUL:
        return unit(rule.get("no")) and unit(rule.get("yes")) and rule["no"] < rule["yes"]
    return unit(rule.get("confidence")) and unit(rule.get("margin"))


@dataclass(frozen=True)
class Policy:
    version: str
    rules: dict[str, dict]
    fitted: tuple[str, ...] = ()
    source: str = "provisional"
    problem: str = ""
    provenance: dict = field(default_factory=dict)

    def record(self) -> dict:
        """What a dossier and a replay keep: every rule, which were fitted,
        and from what data — the error tables stay in the artifact."""

        dataset = (self.provenance.get("dataset") or {}).get("sha256")
        return {"version": self.version, "rules": self.rules, "fitted": list(self.fitted),
                "source": self.source, "problem": self.problem, "dataset": dataset}


def provisional() -> Policy:
    return Policy(PROVISIONAL_VERSION, {k: dict(v) for k, v in PROVISIONAL.items()})


def policy(model: str, path: Path | None = None, prompt_version: str | None = None) -> Policy:
    """The policy for `model` asked with `prompt_version`: fitted kinds from
    the artifact when it was fitted on that model and those prompts,
    provisional for the rest. An artifact that cannot be read, or is for
    another model or other prompts, leaves every kind provisional — and a
    rule that is not well formed leaves its kind provisional — each saying why."""

    path = path or settings.HUB / ARTIFACT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return provisional()
    except (OSError, ValueError):
        return Policy(PROVISIONAL_VERSION, provisional().rules, problem="unreadable_artifact")
    if not isinstance(data, dict) or data.get("model") != model:
        return Policy(PROVISIONAL_VERSION, provisional().rules, problem="artifact_for_another_model")
    if prompt_version is not None and data.get("prompt_version") != prompt_version:
        return Policy(PROVISIONAL_VERSION, provisional().rules, problem="artifact_for_other_prompts")
    rules = provisional().rules
    fitted = tuple(k for k, rule in (data.get("rules") or {}).items() if k in KINDS and valid(k, rule))
    for kind in fitted:
        rules[kind] = {n: data["rules"][kind][n] for n in PROVISIONAL[kind]}
    if not fitted:
        return Policy(PROVISIONAL_VERSION, rules, problem="no_valid_rule")
    body = json.dumps({"model": model, "rules": rules, "fitted": fitted}, sort_keys=True)
    version = "fitted-" + hashlib.sha256(body.encode()).hexdigest()[:12]
    try:
        where = path.resolve().relative_to(settings.HUB.resolve()).as_posix()
    except ValueError:
        where = path.name   # a record names no folder of this machine
    return Policy(version, rules, fitted, where,
                  provenance={k: data.get(k) for k in ("dataset", "prompt_version", "normalization_version",
                                                       "fitted_at", "costs", "errors")})


def verdict(pol: Policy, kind: str, value: float | dict) -> str:
    """`yes`, `no` or `uncertain` for a validated answer under `pol`.

    A Noul at or under `no` is no, at or over `yes` is yes. A Choice is
    accepted (`yes`) only when it names no deferral, its confidence reaches
    the rule, and it leads the runner-up by the margin; otherwise the choice
    is left to code (`uncertain`). A Score is accepted on its confidence.
    """

    rule = pol.rules[kind]
    if kind in NOUL:
        return "no" if value <= rule["no"] else "yes" if value >= rule["yes"] else "uncertain"
    ranked = sorted(value["probabilities"].values(), reverse=True) + [0.0, 0.0]
    if "choice" in value and value["choice"] == "defer":
        return "uncertain"
    if value["confidence"] < rule["confidence"] or ranked[0] - ranked[1] < rule["margin"]:
        return "uncertain"
    return "yes"
