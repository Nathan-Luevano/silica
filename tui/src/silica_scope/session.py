from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import discovery, model
from .corpus import Corpus
from .model import SHARD_COUNT


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
    corpus: Corpus | None = None

    @property
    def root(self) -> Path:
        return self.artifacts.root

    @property
    def spec_release(self) -> str:
        if self.g1_supports_sweep and isinstance(self.g1.value, dict):
            return str(self.g1.value.get("spec_release", "unknown"))
        return "unknown"

    @property
    def has_anything(self) -> bool:
        return any(p.present for p in self.artifacts.found)

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
        if self.g1_supports_sweep and isinstance(self.g1.value, dict):
            return self.g1.value.get(key)
        return None

    @property
    def metrics_supports_sweep(self) -> bool:
        return (
            self.metrics.ok
            and isinstance(self.metrics.value, model.Metrics)
            and self.metrics.value.supports_sweep_evidence
        )

    @property
    def g1_supports_sweep(self) -> bool:
        return self.g1.ok and not model.g1_evidence_problems(self.g1.value)

    def shard(self, shard_id: int) -> model.Shard | None:
        for s in self.shards:
            if s.shard_id == shard_id:
                return s
        return None

    @property
    def has_sweep_evidence(self) -> bool:
        # "all 2^32 encodings swept" is a claim that the whole space was
        # covered. one surviving shard record does not support it: either a
        # published metrics/g1 summary, or all 256 shards, does.
        if self.metrics_supports_sweep or self.g1_supports_sweep:
            return True
        return {shard.shard_id for shard in self.shards} == set(range(SHARD_COUNT))

    @property
    def complete_shards(self) -> int:
        return sum(1 for s in self.shards if s.status == "complete")

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
        if self.metrics.ok and isinstance(self.metrics.value, model.Metrics):
            out.extend(
                f"report/metrics.json: {problem}"
                for problem in self.metrics.value.evidence_problems()
            )
        if self.g1.ok:
            out.extend(
                f"g1_metrics.json: {problem}"
                for problem in model.g1_evidence_problems(self.g1.value)
            )
        out.extend(
            f"sweep/shards: {p}"
            for p in self.shard_problems[:5]
            if not p.startswith("not found:")
        )
        for repro in self.reproducers:
            out.extend(f"{repro.path.name}: {p}" for p in repro.problems)
        return out


def load(root: Path) -> Session:
    artifacts = discovery.scan(root)
    shards, shard_problems = model.load_shards(artifacts.path("shards"))
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
        corpus=corpus,
    )
