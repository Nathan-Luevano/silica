from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import discovery, model
from .corpus import Corpus


@dataclass
class Session:
    artifacts: discovery.Artifacts
    metrics: model.Loaded = field(default_factory=model.Loaded)
    g1: model.Loaded = field(default_factory=model.Loaded)
    g4: model.Loaded = field(default_factory=model.Loaded)
    result_hash: model.Loaded = field(default_factory=model.Loaded)
    normalization: model.Loaded = field(default_factory=model.Loaded)
    shards: list[model.Shard] = field(default_factory=list)
    shard_problems: list[str] = field(default_factory=list)
    reproducers: list[model.Reproducer] = field(default_factory=list)
    goals: list[model.Goal] = field(default_factory=list)
    goals_error: str = ""
    corpus: Corpus | None = None

    @property
    def root(self) -> Path:
        return self.artifacts.root

    @property
    def spec_release(self) -> str:
        if self.g1.ok and isinstance(self.g1.value, dict):
            return str(self.g1.value.get("spec_release", "unknown"))
        return "unknown"

    @property
    def has_anything(self) -> bool:
        return bool(self.artifacts.found)

    def category_counts(self) -> dict[str, int]:
        if self.g4.ok and isinstance(self.g4.value, dict):
            raw = self.g4.value.get("category_counts")
            if isinstance(raw, dict):
                return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float))}
        return {}

    def g4_int(self, key: str) -> int | None:
        if self.g4.ok and isinstance(self.g4.value, dict):
            v = self.g4.value.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        return None

    def g1_value(self, key: str) -> object | None:
        if self.g1.ok and isinstance(self.g1.value, dict):
            return self.g1.value.get(key)
        return None

    def shard(self, shard_id: int) -> model.Shard | None:
        for s in self.shards:
            if s.shard_id == shard_id:
                return s
        return None

    @property
    def has_sweep_evidence(self) -> bool:
        # "all 2^32 encodings swept" is a claim about a run. don't make it
        # from a checkout that only ships the ten reproducers.
        return bool(self.shards) or self.metrics.ok or self.g1.ok

    def problems(self) -> list[str]:
        # a missing file is not a problem: a published checkout ships only
        # reproducers/ and result_hash.txt by design. what belongs here is a
        # file that exists and is wrong.
        out: list[str] = []
        for label, loaded in (
            ("report/metrics.json", self.metrics),
            ("g1_metrics.json", self.g1),
            ("g4_metrics.json", self.g4),
            ("result_hash.txt", self.result_hash),
        ):
            if loaded.error and not loaded.error.startswith("not found:"):
                out.append(f"{label}: {loaded.error}")
        if self.metrics.ok:
            out.extend(f"report/metrics.json: {w}" for w in self.metrics.value.warnings)
        out.extend(
            f"sweep/shards: {p}"
            for p in self.shard_problems[:5]
            if not p.startswith("not found:")
        )
        if self.goals_error and self.goals_error != "GOALS.yml not found":
            out.append(f"GOALS.yml: {self.goals_error}")
        for repro in self.reproducers:
            out.extend(f"{repro.path.name}: {p}" for p in repro.problems)
        return out


def load(root: Path, goals_file: Path | None = None) -> Session:
    artifacts = discovery.scan(root, goals_file)
    shards, shard_problems = model.load_shards(artifacts.path("shards"))
    goals, goals_error = model.load_goals(artifacts.goals_file)
    corpus = Corpus(artifacts.path("disagreements"))
    for shard in shards:
        shard.has_corpus = corpus.has_shard(shard.shard_id)
        shard.disagreement_bytes = corpus.shard_bytes(shard.shard_id)
    return Session(
        artifacts=artifacts,
        metrics=model.load_metrics(artifacts.path("metrics")),
        g1=model.load_flat_json(artifacts.path("g1_metrics")),
        g4=model.load_flat_json(artifacts.path("g4_metrics")),
        result_hash=model.load_result_hash(artifacts.path("result_hash")),
        normalization=model.load_flat_json(artifacts.path("normalization")),
        shards=shards,
        shard_problems=shard_problems,
        reproducers=model.load_reproducers(artifacts.path("reproducers")),
        goals=goals,
        goals_error=goals_error,
        corpus=corpus,
    )
