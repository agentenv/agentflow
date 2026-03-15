from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from string import Template
from typing import Mapping


@dataclass(frozen=True)
class BundledTemplateParameter:
    name: str
    description: str
    default: str


@dataclass(frozen=True)
class BundledTemplate:
    name: str
    example_name: str
    description: str
    parameters: tuple[BundledTemplateParameter, ...] = ()
    support_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderedBundledTemplateFile:
    relative_path: str
    content: str


@dataclass(frozen=True)
class RenderedBundledTemplate:
    yaml: str
    support_files: tuple[RenderedBundledTemplateFile, ...] = ()


_DEFAULT_FUZZ_SWARM_SHARDS = 32
_DEFAULT_FUZZ_SWARM_CONCURRENCY = 8
_DEFAULT_FUZZ_MATRIX_MANIFEST_BUCKET_COUNT = 4
_DEFAULT_FUZZ_MATRIX_MANIFEST_CONCURRENCY = 16
_DEFAULT_FUZZ_HIERARCHICAL_BUCKET_COUNT = 4
_DEFAULT_FUZZ_HIERARCHICAL_CONCURRENCY = 16
_DEFAULT_FUZZ_CATALOG_SHARDS = 128
_DEFAULT_FUZZ_CATALOG_CONCURRENCY = 32
_FUZZ_MATRIX_MANIFEST_SUPPORT_FILE = "manifests/codex-fuzz-matrix.axes.yaml"
_FUZZ_HIERARCHICAL_GROUPED_AXES_SUPPORT_FILE = "manifests/codex-fuzz-hierarchical-grouped.axes.yaml"
_FUZZ_HIERARCHICAL_AXES_SUPPORT_FILE = "manifests/codex-fuzz-hierarchical.axes.yaml"
_FUZZ_HIERARCHICAL_FAMILIES_SUPPORT_FILE = "manifests/codex-fuzz-hierarchical.families.yaml"
_FUZZ_CATALOG_SUPPORT_FILE = "manifests/codex-fuzz-catalog.csv"
_FUZZ_CATALOG_FAMILIES = (
    {"target": "libpng", "corpus": "png"},
    {"target": "libjpeg", "corpus": "jpeg"},
    {"target": "freetype", "corpus": "fonts"},
    {"target": "sqlite", "corpus": "sql"},
)
_FUZZ_CATALOG_STRATEGIES = (
    {"sanitizer": "asan", "focus": "parser"},
    {"sanitizer": "asan", "focus": "structure-aware"},
    {"sanitizer": "ubsan", "focus": "differential"},
    {"sanitizer": "ubsan", "focus": "stateful"},
)


def _parse_positive_template_int(template_name: str, field_name: str, raw_value: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"template `{template_name}` expects `{field_name}` to be an integer, got `{raw_value}`") from exc
    if value < 1:
        raise ValueError(f"template `{template_name}` expects `{field_name}` to be at least 1, got `{raw_value}`")
    return value


def _template_string_value(template_name: str, field_name: str, raw_value: str | None, *, default: str) -> str:
    value = (raw_value if raw_value is not None else default).strip()
    if not value:
        raise ValueError(f"template `{template_name}` expects `{field_name}` to be a non-empty string")
    return value


def _validate_template_settings(template_name: str, raw_values: Mapping[str, str], *, allowed: set[str]) -> None:
    unknown = sorted(set(raw_values) - allowed)
    if unknown:
        supported = ", ".join(f"`{name}`" for name in sorted(allowed))
        unknown_display = ", ".join(f"`{name}`" for name in unknown)
        raise ValueError(
            f"template `{template_name}` does not recognize {unknown_display}; supported settings: {supported}"
        )


def _fanout_suffix(index: int, count: int) -> str:
    width = max(1, len(str(count - 1)))
    return f"{index:0{width}d}"


def _fuzz_matrix_manifest_total_shards(bucket_count: int) -> int:
    return len(_FUZZ_CATALOG_FAMILIES) * len(_FUZZ_CATALOG_STRATEGIES) * bucket_count


def _render_codex_fuzz_matrix_manifest_axes(bucket_count: int) -> str:
    lines: list[str] = ["family:"]
    for family in _FUZZ_CATALOG_FAMILIES:
        lines.extend(
            (
                f"  - target: {family['target']}",
                f"    corpus: {family['corpus']}",
            )
        )

    lines.append("strategy:")
    for strategy in _FUZZ_CATALOG_STRATEGIES:
        lines.extend(
            (
                f"  - sanitizer: {strategy['sanitizer']}",
                f"    focus: {strategy['focus']}",
            )
        )

    lines.append("seed_bucket:")
    for index in range(bucket_count):
        lines.extend(
            (
                f"  - bucket: seed_{index + 1:03d}",
                f"    seed: {4101 + index}",
            )
        )
    return "\n".join(lines) + "\n"


def _render_codex_fuzz_family_values() -> str:
    lines: list[str] = []
    for family in _FUZZ_CATALOG_FAMILIES:
        lines.extend(
            (
                f"- target: {family['target']}",
                f"  corpus: {family['corpus']}",
            )
        )
    return "\n".join(lines) + "\n"


def _render_codex_fuzz_hierarchical_grouped_template(values: Mapping[str, str] | None = None) -> RenderedBundledTemplate:
    template_name = "codex-fuzz-hierarchical-grouped"
    raw_values = dict(values or {})
    allowed = {"bucket_count", "concurrency", "name", "working_dir"}
    _validate_template_settings(template_name, raw_values, allowed=allowed)

    bucket_count = _parse_positive_template_int(
        template_name,
        "bucket_count",
        raw_values.get("bucket_count", str(_DEFAULT_FUZZ_HIERARCHICAL_BUCKET_COUNT)),
    )
    concurrency = _parse_positive_template_int(
        template_name,
        "concurrency",
        raw_values.get("concurrency", str(_DEFAULT_FUZZ_HIERARCHICAL_CONCURRENCY)),
    )
    total_shards = _fuzz_matrix_manifest_total_shards(bucket_count)
    name = _template_string_value(
        template_name,
        "name",
        raw_values.get("name"),
        default=f"codex-fuzz-hierarchical-grouped-{total_shards}",
    )
    working_dir = _template_string_value(
        template_name,
        "working_dir",
        raw_values.get("working_dir"),
        default=f"./codex_fuzz_hierarchical_grouped_{total_shards}",
    )

    rendered_yaml = Template(
        """# Configurable hierarchical Codex fuzz matrix with grouped reducers
#
# This scaffold keeps only the reusable fuzz axes in a sidecar manifest. The
# per-family reducer roster is derived automatically from the expanded shard
# fanout via `fanout.group_by`, so maintainers do not need to keep a second
# family manifest in sync.
#
# Usage:
#   agentflow init fuzz-hierarchical-grouped.yaml --template codex-fuzz-hierarchical-grouped
#   agentflow init fuzz-hierarchical-grouped-128.yaml --template codex-fuzz-hierarchical-grouped --set bucket_count=8 --set concurrency=32
#   agentflow inspect fuzz-hierarchical-grouped.yaml --output summary
#   agentflow run fuzz-hierarchical-grouped.yaml --preflight never

name: $name
description: Configurable hierarchical $total_shards-shard Codex fuzz matrix that derives per-target reducers from `fanout.group_by`.
working_dir: $working_dir
concurrency: $concurrency

nodes:
  - id: init
    agent: codex
    tools: read_write
    timeout_seconds: 60
    prompt: |
      Create the following directory structure silently if it does not already exist:
        mkdir -p docs crashes
      If crashes/README.md is missing or empty, create it with:
        # Crash Registry
        | Timestamp | Target | Sanitizer | Bucket | Shard | Evidence |
        |---|---|---|---|---|---|
      If docs/campaign_notes.md is missing or empty, create it with:
        # Campaign Notes
        Use this file only for cross-shard lessons and retargeting guidance.
      Then respond with exactly: INIT_OK

    success_criteria:
      - kind: output_contains
        value: INIT_OK

  - id: fuzzer
    fanout:
      as: shard
      matrix_path: $_FUZZ_HIERARCHICAL_GROUPED_AXES_SUPPORT_FILE
      derive:
        label: "{{ shard.target }} / {{ shard.sanitizer }} / {{ shard.focus }} / {{ shard.bucket }}"
        workspace: "agents/{{ shard.target }}_{{ shard.sanitizer }}_{{ shard.bucket }}_{{ shard.suffix }}"
    agent: codex
    model: gpt-5-codex
    tools: read_write
    depends_on: [init]
    target:
      kind: local
      cwd: "{{ shard.workspace }}"
    timeout_seconds: 3600
    retries: 2
    retry_backoff_seconds: 2
    extra_args:
      - "--search"
      - "-c"
      - 'model_reasoning_effort="high"'
    prompt: |
      You are Codex fuzz shard {{ shard.number }} of {{ shard.count }} in an authorized campaign.

      Campaign inputs:
      - Target: {{ shard.target }}
      - Corpus family: {{ shard.corpus }}
      - Sanitizer: {{ shard.sanitizer }}
      - Strategy focus: {{ shard.focus }}
      - Seed bucket: {{ shard.bucket }}
      - Seed: {{ shard.seed }}
      - Label: {{ shard.label }}
      - Workspace: {{ shard.workspace }}

      Shard contract:
      - Stay within {{ shard.workspace }} unless you are appending to the shared crash registry or notes.
      - Treat `$_FUZZ_HIERARCHICAL_GROUPED_AXES_SUPPORT_FILE` as the source of truth for the reusable campaign axes.
      - The staged reducers are derived automatically from the unique target/corpus pairs in this fanout.
      - Use the label, target family, sanitizer, focus, and seed bucket to keep the campaign reproducible.
      - Prefer high-signal crashers, assertion failures, memory safety bugs, or logic corruptions.
      - Record confirmed findings in `crashes/README.md` and copy minimal repro artifacts into `crashes/`.
      - Add short cross-shard lessons to `docs/campaign_notes.md` when they help other shards avoid duplicate work.

  - id: family_merge
    fanout:
      as: family
      group_by:
        from: fuzzer
        fields: [target, corpus]
    agent: codex
    model: gpt-5-codex
    tools: read_only
    depends_on: [fuzzer]
    timeout_seconds: 300
    prompt: |
      {% set target = "{{ family.target }}" %}
      {% set corpus = "{{ family.corpus }}" %}
      {% set target_completed = fanouts.fuzzer.completed.nodes | selectattr("target", "equalto", target) | list %}
      {% set target_failed = fanouts.fuzzer.failed.nodes | selectattr("target", "equalto", target) | list %}
      {% set target_outputs = fanouts.fuzzer.with_output.nodes | selectattr("target", "equalto", target) | list %}
      Prepare the maintainer handoff for target family {{ target }} (corpus {{ corpus }}).

      Campaign snapshot:
      - Total shards: {{ fanouts.fuzzer.size }}
      - Completed shards: {{ fanouts.fuzzer.summary.completed }}
      - Failed shards: {{ fanouts.fuzzer.summary.failed }}
      - Silent shards: {{ fanouts.fuzzer.summary.without_output }}
      - {{ target }} completed shards: {{ target_completed | length }}
      - {{ target }} failed shards: {{ target_failed | length }}
      - {{ target }} shards with output: {{ target_outputs | length }}

      Focus only on {{ target }}. Summarize strong or confirmed findings first, then recurring lessons, then quiet or failed shards that need retargeting.

      {% for shard in target_outputs %}
      ### {{ shard.label }} :: {{ shard.id }} (status: {{ shard.status }})
      {{ shard.output }}

      {% endfor %}
      {% if target_failed %}
      Failed {{ target }} shards:
      {% for shard in target_failed %}
      - {{ shard.id }} :: {{ shard.label }}
      {% endfor %}
      {% endif %}
      {% if not target_outputs %}
      No {{ target }} shard produced reducer-ready output. Say that explicitly and use the failed shard list to suggest retargeting.
      {% endif %}

  - id: merge
    agent: codex
    model: gpt-5-codex
    tools: read_only
    depends_on: [family_merge]
    timeout_seconds: 300
    prompt: |
      Consolidate this hierarchical $total_shards-shard fuzz campaign into a maintainer handoff.
      Start with campaign-wide status, then group the strongest findings by target family, and end with failed or quiet shards that need retargeting.

      Campaign status:
      - Total shards: {{ fanouts.fuzzer.size }}
      - Completed shards: {{ fanouts.fuzzer.summary.completed }}
      - Failed shards: {{ fanouts.fuzzer.summary.failed }}
      - Silent shards: {{ fanouts.fuzzer.summary.without_output }}
      - Family reducers completed: {{ fanouts.family_merge.summary.completed }} / {{ fanouts.family_merge.size }}

      {% for review in fanouts.family_merge.with_output.nodes %}
      ## {{ review.target }} :: {{ review.id }} (status: {{ review.status }})
      {{ review.output }}

      {% endfor %}
      {% if fanouts.fuzzer.failed.size %}
      Failed shard ids:
      {% for shard in fanouts.fuzzer.failed.nodes %}
      - {{ shard.id }} :: {{ shard.label }}
      {% endfor %}
      {% endif %}
"""
    ).substitute(
        name=name,
        total_shards=total_shards,
        working_dir=working_dir,
        concurrency=concurrency,
        _FUZZ_HIERARCHICAL_GROUPED_AXES_SUPPORT_FILE=_FUZZ_HIERARCHICAL_GROUPED_AXES_SUPPORT_FILE,
    )
    return RenderedBundledTemplate(
        yaml=rendered_yaml,
        support_files=(
            RenderedBundledTemplateFile(
                relative_path=_FUZZ_HIERARCHICAL_GROUPED_AXES_SUPPORT_FILE,
                content=_render_codex_fuzz_matrix_manifest_axes(bucket_count),
            ),
        ),
    )


def _render_codex_fuzz_matrix_manifest_template(values: Mapping[str, str] | None = None) -> RenderedBundledTemplate:
    template_name = "codex-fuzz-matrix-manifest"
    raw_values = dict(values or {})
    allowed = {"bucket_count", "concurrency", "name", "working_dir"}
    _validate_template_settings(template_name, raw_values, allowed=allowed)

    bucket_count = _parse_positive_template_int(
        template_name,
        "bucket_count",
        raw_values.get("bucket_count", str(_DEFAULT_FUZZ_MATRIX_MANIFEST_BUCKET_COUNT)),
    )
    concurrency = _parse_positive_template_int(
        template_name,
        "concurrency",
        raw_values.get("concurrency", str(_DEFAULT_FUZZ_MATRIX_MANIFEST_CONCURRENCY)),
    )
    total_shards = _fuzz_matrix_manifest_total_shards(bucket_count)
    name = _template_string_value(
        template_name,
        "name",
        raw_values.get("name"),
        default=f"codex-fuzz-matrix-manifest-{total_shards}",
    )
    working_dir = _template_string_value(
        template_name,
        "working_dir",
        raw_values.get("working_dir"),
        default=f"./codex_fuzz_matrix_manifest_{total_shards}",
    )

    rendered_yaml = Template(
        """# Configurable Codex fuzz matrix with manifest-backed axes
#
# This scaffold keeps the reusable target, strategy, and seed-bucket axes in a
# sidecar manifest while still letting maintainers scale the campaign up or down
# without hand-editing both files from scratch.
#
# Usage:
#   agentflow init fuzz-matrix-manifest.yaml --template codex-fuzz-matrix-manifest
#   agentflow init fuzz-matrix-manifest-128.yaml --template codex-fuzz-matrix-manifest --set bucket_count=8 --set concurrency=32
#   agentflow inspect fuzz-matrix-manifest.yaml --output summary
#   agentflow run fuzz-matrix-manifest.yaml --preflight never

name: $name
description: Configurable $total_shards-shard Codex fuzz matrix backed by a manifest sidecar with reusable axes, derived labels, and per-shard workdirs.
working_dir: $working_dir
concurrency: $concurrency

nodes:
  - id: init
    agent: codex
    tools: read_write
    timeout_seconds: 60
    prompt: |
      Create the following directory structure silently if it does not already exist:
        mkdir -p docs crashes
      If crashes/README.md is missing or empty, create it with:
        # Crash Registry
        | Timestamp | Label | Target | Sanitizer | Evidence | Artifact |
        |---|---|---|---|---|---|
      If docs/campaign_notes.md is missing or empty, create it with:
        # Campaign Notes
        Use this file only for cross-shard lessons and retargeting guidance.
      Then respond with exactly: INIT_OK

    success_criteria:
      - kind: output_contains
        value: INIT_OK

  - id: fuzzer
    fanout:
      as: shard
      matrix_path: $_FUZZ_MATRIX_MANIFEST_SUPPORT_FILE
      derive:
        label: "{{ shard.target }}/{{ shard.sanitizer }}/{{ shard.focus }}/{{ shard.bucket }}"
        workspace: "agents/{{ shard.target }}_{{ shard.sanitizer }}_{{ shard.bucket }}_{{ shard.suffix }}"
    agent: codex
    model: gpt-5-codex
    tools: read_write
    depends_on: [init]
    target:
      kind: local
      cwd: "{{ shard.workspace }}"
    timeout_seconds: 3600
    retries: 2
    retry_backoff_seconds: 2
    extra_args:
      - "--search"
      - "-c"
      - 'model_reasoning_effort="high"'
    prompt: |
      You are Codex fuzz shard {{ shard.number }} of {{ shard.count }} in an authorized campaign.

      Campaign inputs:
      - Label: {{ shard.label }}
      - Target: {{ shard.target }}
      - Corpus family: {{ shard.corpus }}
      - Sanitizer: {{ shard.sanitizer }}
      - Strategy focus: {{ shard.focus }}
      - Seed bucket: {{ shard.bucket }}
      - Seed: {{ shard.seed }}
      - Workspace: {{ shard.workspace }}

      Shard contract:
      - Stay within {{ shard.workspace }} unless you are appending to the shared crash registry or notes.
      - Treat `$_FUZZ_MATRIX_MANIFEST_SUPPORT_FILE` as the source of truth for the reusable campaign axes.
      - Use the label, target family, sanitizer, focus, and seed bucket to keep the campaign reproducible.
      - Prefer high-signal crashers, assertion failures, memory safety bugs, or logic corruptions.
      - Record confirmed findings in `crashes/README.md` and copy minimal repro artifacts into `crashes/`.
      - Add short cross-shard lessons to `docs/campaign_notes.md` when they help other shards avoid duplicate work.

  - id: merge
    agent: codex
    model: gpt-5-codex
    tools: read_only
    depends_on: [fuzzer]
    timeout_seconds: 300
    prompt: |
      Consolidate this $total_shards-shard manifest-backed fuzz matrix into a maintainer handoff.
      Group the findings by target family first, then by sanitizer/focus, and end with seed buckets that need retargeting.

      {% for shard in fanouts.fuzzer.nodes %}
      ### {{ shard.label }} :: {{ shard.id }} (status: {{ shard.status }})
      {{ shard.output or "(no output)" }}

      {% endfor %}
"""
    ).substitute(
        name=name,
        total_shards=total_shards,
        working_dir=working_dir,
        concurrency=concurrency,
        _FUZZ_MATRIX_MANIFEST_SUPPORT_FILE=_FUZZ_MATRIX_MANIFEST_SUPPORT_FILE,
    )
    return RenderedBundledTemplate(
        yaml=rendered_yaml,
        support_files=(
            RenderedBundledTemplateFile(
                relative_path=_FUZZ_MATRIX_MANIFEST_SUPPORT_FILE,
                content=_render_codex_fuzz_matrix_manifest_axes(bucket_count),
            ),
        ),
    )


def _render_codex_fuzz_hierarchical_template(values: Mapping[str, str] | None = None) -> RenderedBundledTemplate:
    template_name = "codex-fuzz-hierarchical-manifest"
    raw_values = dict(values or {})
    allowed = {"bucket_count", "concurrency", "name", "working_dir"}
    _validate_template_settings(template_name, raw_values, allowed=allowed)

    bucket_count = _parse_positive_template_int(
        template_name,
        "bucket_count",
        raw_values.get("bucket_count", str(_DEFAULT_FUZZ_HIERARCHICAL_BUCKET_COUNT)),
    )
    concurrency = _parse_positive_template_int(
        template_name,
        "concurrency",
        raw_values.get("concurrency", str(_DEFAULT_FUZZ_HIERARCHICAL_CONCURRENCY)),
    )
    total_shards = _fuzz_matrix_manifest_total_shards(bucket_count)
    name = _template_string_value(
        template_name,
        "name",
        raw_values.get("name"),
        default=f"codex-fuzz-hierarchical-manifest-{total_shards}",
    )
    working_dir = _template_string_value(
        template_name,
        "working_dir",
        raw_values.get("working_dir"),
        default=f"./codex_fuzz_hierarchical_manifest_{total_shards}",
    )

    rendered_yaml = Template(
        """# Configurable hierarchical Codex fuzz matrix with manifest-backed axes
#
# This scaffold keeps the reusable fuzz axes and the reducer family roster in
# sidecar manifests so maintainers can resize or retarget the campaign without
# hand-editing both the fuzzer matrix and the family reducers.
#
# Usage:
#   agentflow init fuzz-hierarchical.yaml --template codex-fuzz-hierarchical-manifest
#   agentflow init fuzz-hierarchical-128.yaml --template codex-fuzz-hierarchical-manifest --set bucket_count=8 --set concurrency=32
#   agentflow inspect fuzz-hierarchical.yaml --output summary
#   agentflow run fuzz-hierarchical.yaml --preflight never

name: $name
description: Configurable hierarchical $total_shards-shard Codex fuzz matrix backed by manifests for reusable axes and family reducers.
working_dir: $working_dir
concurrency: $concurrency

nodes:
  - id: init
    agent: codex
    tools: read_write
    timeout_seconds: 60
    prompt: |
      Create the following directory structure silently if it does not already exist:
        mkdir -p docs crashes
      If crashes/README.md is missing or empty, create it with:
        # Crash Registry
        | Timestamp | Target | Sanitizer | Bucket | Shard | Evidence |
        |---|---|---|---|---|---|
      If docs/campaign_notes.md is missing or empty, create it with:
        # Campaign Notes
        Use this file only for cross-shard lessons and retargeting guidance.
      Then respond with exactly: INIT_OK

    success_criteria:
      - kind: output_contains
        value: INIT_OK

  - id: fuzzer
    fanout:
      as: shard
      matrix_path: $_FUZZ_HIERARCHICAL_AXES_SUPPORT_FILE
      derive:
        label: "{{ shard.target }} / {{ shard.sanitizer }} / {{ shard.focus }} / {{ shard.bucket }}"
        workspace: "agents/{{ shard.target }}_{{ shard.sanitizer }}_{{ shard.bucket }}_{{ shard.suffix }}"
    agent: codex
    model: gpt-5-codex
    tools: read_write
    depends_on: [init]
    target:
      kind: local
      cwd: "{{ shard.workspace }}"
    timeout_seconds: 3600
    retries: 2
    retry_backoff_seconds: 2
    extra_args:
      - "--search"
      - "-c"
      - 'model_reasoning_effort="high"'
    prompt: |
      You are Codex fuzz shard {{ shard.number }} of {{ shard.count }} in an authorized campaign.

      Campaign inputs:
      - Target: {{ shard.target }}
      - Corpus family: {{ shard.corpus }}
      - Sanitizer: {{ shard.sanitizer }}
      - Strategy focus: {{ shard.focus }}
      - Seed bucket: {{ shard.bucket }}
      - Seed: {{ shard.seed }}
      - Label: {{ shard.label }}
      - Workspace: {{ shard.workspace }}

      Shard contract:
      - Stay within {{ shard.workspace }} unless you are appending to the shared crash registry or notes.
      - Treat `$_FUZZ_HIERARCHICAL_AXES_SUPPORT_FILE` as the source of truth for the reusable campaign axes.
      - Use the label, target family, sanitizer, focus, and seed bucket to keep the campaign reproducible.
      - Prefer high-signal crashers, assertion failures, memory safety bugs, or logic corruptions.
      - Record confirmed findings in `crashes/README.md` and copy minimal repro artifacts into `crashes/`.
      - Add short cross-shard lessons to `docs/campaign_notes.md` when they help other shards avoid duplicate work.

  - id: family_merge
    fanout:
      as: family
      values_path: $_FUZZ_HIERARCHICAL_FAMILIES_SUPPORT_FILE
    agent: codex
    model: gpt-5-codex
    tools: read_only
    depends_on: [fuzzer]
    timeout_seconds: 300
    prompt: |
      {% set target = "{{ family.target }}" %}
      {% set corpus = "{{ family.corpus }}" %}
      {% set target_completed = fanouts.fuzzer.completed.nodes | selectattr("target", "equalto", target) | list %}
      {% set target_failed = fanouts.fuzzer.failed.nodes | selectattr("target", "equalto", target) | list %}
      {% set target_outputs = fanouts.fuzzer.with_output.nodes | selectattr("target", "equalto", target) | list %}
      Prepare the maintainer handoff for target family {{ target }} (corpus {{ corpus }}).

      Campaign snapshot:
      - Total shards: {{ fanouts.fuzzer.size }}
      - Completed shards: {{ fanouts.fuzzer.summary.completed }}
      - Failed shards: {{ fanouts.fuzzer.summary.failed }}
      - Silent shards: {{ fanouts.fuzzer.summary.without_output }}
      - {{ target }} completed shards: {{ target_completed | length }}
      - {{ target }} failed shards: {{ target_failed | length }}
      - {{ target }} shards with output: {{ target_outputs | length }}

      Focus only on {{ target }}. Summarize strong or confirmed findings first, then recurring lessons, then quiet or failed shards that need retargeting.

      {% for shard in target_outputs %}
      ### {{ shard.label }} :: {{ shard.id }} (status: {{ shard.status }})
      {{ shard.output }}

      {% endfor %}
      {% if target_failed %}
      Failed {{ target }} shards:
      {% for shard in target_failed %}
      - {{ shard.id }} :: {{ shard.label }}
      {% endfor %}
      {% endif %}
      {% if not target_outputs %}
      No {{ target }} shard produced reducer-ready output. Say that explicitly and use the failed shard list to suggest retargeting.
      {% endif %}

  - id: merge
    agent: codex
    model: gpt-5-codex
    tools: read_only
    depends_on: [family_merge]
    timeout_seconds: 300
    prompt: |
      Consolidate this hierarchical $total_shards-shard fuzz campaign into a maintainer handoff.
      Start with campaign-wide status, then group the strongest findings by target family, and end with failed or quiet shards that need retargeting.

      Campaign status:
      - Total shards: {{ fanouts.fuzzer.size }}
      - Completed shards: {{ fanouts.fuzzer.summary.completed }}
      - Failed shards: {{ fanouts.fuzzer.summary.failed }}
      - Silent shards: {{ fanouts.fuzzer.summary.without_output }}
      - Family reducers completed: {{ fanouts.family_merge.summary.completed }} / {{ fanouts.family_merge.size }}

      {% for review in fanouts.family_merge.with_output.nodes %}
      ## {{ review.target }} :: {{ review.id }} (status: {{ review.status }})
      {{ review.output }}

      {% endfor %}
      {% if fanouts.fuzzer.failed.size %}
      Failed shard ids:
      {% for shard in fanouts.fuzzer.failed.nodes %}
      - {{ shard.id }} :: {{ shard.label }}
      {% endfor %}
      {% endif %}
"""
    ).substitute(
        name=name,
        total_shards=total_shards,
        working_dir=working_dir,
        concurrency=concurrency,
        _FUZZ_HIERARCHICAL_AXES_SUPPORT_FILE=_FUZZ_HIERARCHICAL_AXES_SUPPORT_FILE,
        _FUZZ_HIERARCHICAL_FAMILIES_SUPPORT_FILE=_FUZZ_HIERARCHICAL_FAMILIES_SUPPORT_FILE,
    )
    return RenderedBundledTemplate(
        yaml=rendered_yaml,
        support_files=(
            RenderedBundledTemplateFile(
                relative_path=_FUZZ_HIERARCHICAL_AXES_SUPPORT_FILE,
                content=_render_codex_fuzz_matrix_manifest_axes(bucket_count),
            ),
            RenderedBundledTemplateFile(
                relative_path=_FUZZ_HIERARCHICAL_FAMILIES_SUPPORT_FILE,
                content=_render_codex_fuzz_family_values(),
            ),
        ),
    )


def _render_codex_fuzz_swarm_template(values: Mapping[str, str] | None = None) -> RenderedBundledTemplate:
    template_name = "codex-fuzz-swarm"
    raw_values = dict(values or {})
    allowed = {"shards", "concurrency", "name", "working_dir"}
    _validate_template_settings(template_name, raw_values, allowed=allowed)

    shards = _parse_positive_template_int(
        template_name,
        "shards",
        raw_values.get("shards", str(_DEFAULT_FUZZ_SWARM_SHARDS)),
    )
    concurrency = _parse_positive_template_int(
        template_name,
        "concurrency",
        raw_values.get("concurrency", str(_DEFAULT_FUZZ_SWARM_CONCURRENCY)),
    )
    name = _template_string_value(template_name, "name", raw_values.get("name"), default=f"codex-fuzz-swarm-{shards}")
    working_dir = _template_string_value(
        template_name,
        "working_dir",
        raw_values.get("working_dir"),
        default=f"./codex_fuzz_swarm_{shards}",
    )

    rendered_yaml = Template(
        """# Configurable Codex fuzzing swarm
#
# This scaffold is the easiest way to right-size a Codex fuzz campaign for the
# machine and budget you actually have. Start with the default 32-shard layout,
# then scale it up or down with `agentflow init --set shards=...`.
#
# Usage:
#   agentflow init fuzz-swarm.yaml --template codex-fuzz-swarm
#   agentflow init fuzz-128.yaml --template codex-fuzz-swarm --set shards=128 --set concurrency=32
#   agentflow inspect fuzz-swarm.yaml
#   agentflow run fuzz-swarm.yaml --preflight never

name: $name
description: Configurable $shards-shard Codex fuzzing swarm with shared init, retries, per-shard workdirs, and a merge reducer.
working_dir: $working_dir
concurrency: $concurrency

nodes:
  - id: init
    agent: codex
    tools: read_write
    timeout_seconds: 60
    prompt: |
      Create the following directory structure silently if it does not already exist:
        mkdir -p docs crashes locks
      If crashes/README.md is missing or empty, create it with:
        # Crash Registry
        | Timestamp | Shard | Target | Evidence | Artifact |
        |---|---|---|---|---|
      If docs/global_lessons.md is missing or empty, create it with:
        # Shared Lessons
        Use this file only for reusable campaign-wide notes.
      Then respond with exactly: INIT_OK

    success_criteria:
      - kind: output_contains
        value: INIT_OK

  - id: fuzzer
    fanout:
      count: $shards
      as: shard
    agent: codex
    model: gpt-5-codex
    tools: read_write
    depends_on: [init]
    target:
      kind: local
      cwd: agents/agent_{{ shard.suffix }}
    timeout_seconds: 3600
    retries: 2
    retry_backoff_seconds: 2
    extra_args:
      - "--search"
      - "-c"
      - 'model_reasoning_effort="high"'
    prompt: |
      You are Codex fuzz shard {{ shard.number }} of {{ shard.count }} in an authorized campaign.

      Shared workspace:
      - Root: {{ pipeline.working_dir }}
      - Shard dir: agents/agent_{{ shard.suffix }}
      - Crash registry: crashes/README.md
      - Shared notes: docs/global_lessons.md

      Shard contract:
      - Own only files under agents/agent_{{ shard.suffix }} unless you are appending to the shared docs or crash registry with locking.
      - Keep your inputs and notes deterministic so another engineer can replay them.
      - Use shard id `{{ shard.suffix }}` to vary corpus slices, seeds, flags, or target areas.
      - Focus on deep, high-signal failure modes rather than shallow lint or unit-test noise.
      - When you confirm a real issue, copy the minimal reproducer into `crashes/` and append a one-line entry to the registry.
      - When a target area looks exhausted, write concise lessons to `docs/`.
      - Continue searching until timeout.

  - id: merge
    agent: codex
    model: gpt-5-codex
    tools: read_only
    depends_on: [fuzzer]
    timeout_seconds: 300
    prompt: |
      Consolidate this $shards-shard fuzzing campaign into a maintainer handoff.
      Summarize the strongest crash families first, then recurring lessons, then quiet shards that need retargeting.

      {% for shard in fanouts.fuzzer.nodes %}
      ### {{ shard.id }} (status: {{ shard.status }})
      {{ shard.output or "(no output)" }}

      {% endfor %}
"""
    ).substitute(
        name=name,
        shards=shards,
        working_dir=working_dir,
        concurrency=concurrency,
        suffix_start=_fanout_suffix(0, shards),
        suffix_end=_fanout_suffix(shards - 1, shards),
    )
    return RenderedBundledTemplate(yaml=rendered_yaml)


def _render_codex_fuzz_catalog_rows(shards: int) -> list[dict[str, str]]:
    combinations = [(family, strategy) for family in _FUZZ_CATALOG_FAMILIES for strategy in _FUZZ_CATALOG_STRATEGIES]
    rendered_rows: list[dict[str, str]] = []
    for index in range(shards):
        family, strategy = combinations[index % len(combinations)]
        bucket_index = index // len(combinations)
        bucket = f"seed_{bucket_index + 1:03d}"
        suffix = _fanout_suffix(index, shards)
        rendered_rows.append(
            {
                "label": f"{family['target']}/{strategy['sanitizer']}/{strategy['focus']}/{bucket}",
                "target": family["target"],
                "corpus": family["corpus"],
                "sanitizer": strategy["sanitizer"],
                "focus": strategy["focus"],
                "bucket": bucket,
                "seed": str(4101 + bucket_index),
                "workspace": f"agents/{family['target']}_{strategy['sanitizer']}_{bucket}_{suffix}",
            }
        )
    return rendered_rows


def _render_codex_fuzz_catalog_csv(shards: int) -> str:
    rows = _render_codex_fuzz_catalog_rows(shards)
    buffer = StringIO()
    fieldnames = ("label", "target", "corpus", "sanitizer", "focus", "bucket", "seed", "workspace")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _render_codex_fuzz_catalog_template(values: Mapping[str, str] | None = None) -> RenderedBundledTemplate:
    template_name = "codex-fuzz-catalog"
    raw_values = dict(values or {})
    allowed = {"shards", "concurrency", "name", "working_dir"}
    _validate_template_settings(template_name, raw_values, allowed=allowed)

    shards = _parse_positive_template_int(
        template_name,
        "shards",
        raw_values.get("shards", str(_DEFAULT_FUZZ_CATALOG_SHARDS)),
    )
    concurrency = _parse_positive_template_int(
        template_name,
        "concurrency",
        raw_values.get("concurrency", str(_DEFAULT_FUZZ_CATALOG_CONCURRENCY)),
    )
    name = _template_string_value(
        template_name,
        "name",
        raw_values.get("name"),
        default=f"codex-fuzz-catalog-{shards}",
    )
    working_dir = _template_string_value(
        template_name,
        "working_dir",
        raw_values.get("working_dir"),
        default=f"./codex_fuzz_catalog_{shards}",
    )

    rendered_yaml = Template(
        """# Configurable Codex fuzz catalog
#
# This scaffold keeps shard metadata in a sidecar CSV so maintainers can retarget
# large campaigns in a spreadsheet without rewriting the reducer or launch settings.
#
# Usage:
#   agentflow init fuzz-catalog.yaml --template codex-fuzz-catalog
#   agentflow init fuzz-catalog-48.yaml --template codex-fuzz-catalog --set shards=48 --set concurrency=12
#   agentflow inspect fuzz-catalog.yaml --output summary
#   agentflow run fuzz-catalog.yaml --preflight never

name: $name
description: Configurable $shards-shard Codex fuzz campaign backed by a CSV shard catalog for maintainer-friendly retargeting.
working_dir: $working_dir
concurrency: $concurrency

nodes:
  - id: init
    agent: codex
    tools: read_write
    timeout_seconds: 60
    prompt: |
      Create the following directory structure silently if it does not already exist:
        mkdir -p docs crashes
      If crashes/README.md is missing or empty, create it with:
        # Crash Registry
        | Timestamp | Label | Target | Evidence | Artifact |
        |---|---|---|---|---|
      If docs/campaign_notes.md is missing or empty, create it with:
        # Campaign Notes
        Use this file only for cross-shard lessons and retargeting guidance.
      Then respond with exactly: INIT_OK

    success_criteria:
      - kind: output_contains
        value: INIT_OK

  - id: fuzzer
    fanout:
      as: shard
      values_path: $_FUZZ_CATALOG_SUPPORT_FILE
    agent: codex
    model: gpt-5-codex
    tools: read_write
    depends_on: [init]
    target:
      kind: local
      cwd: "{{ shard.workspace }}"
    timeout_seconds: 3600
    retries: 2
    retry_backoff_seconds: 2
    extra_args:
      - "--search"
      - "-c"
      - 'model_reasoning_effort="high"'
    prompt: |
      You are Codex fuzz shard {{ shard.number }} of {{ shard.count }} in an authorized campaign.

      Campaign inputs:
      - Catalog label: {{ shard.label }}
      - Target: {{ shard.target }}
      - Corpus family: {{ shard.corpus }}
      - Sanitizer: {{ shard.sanitizer }}
      - Strategy focus: {{ shard.focus }}
      - Seed bucket: {{ shard.bucket }}
      - Seed: {{ shard.seed }}
      - Workspace: {{ shard.workspace }}

      Shard contract:
      - Stay within {{ shard.workspace }} unless you are appending to the shared crash registry or notes.
      - Treat `$_FUZZ_CATALOG_SUPPORT_FILE` as the source of truth for your assignment.
      - Use the catalog label, target family, sanitizer, focus, and seed bucket to keep the campaign reproducible.
      - Prefer high-signal crashers, assertion failures, memory safety bugs, or logic corruptions.
      - Record confirmed findings in `crashes/README.md` and copy minimal repro artifacts into `crashes/`.
      - Add short cross-shard lessons to `docs/campaign_notes.md` when they help other shards avoid duplicate work.

  - id: merge
    agent: codex
    model: gpt-5-codex
    tools: read_only
    depends_on: [fuzzer]
    timeout_seconds: 300
    prompt: |
      Consolidate this $shards-shard catalog-backed fuzz campaign into a maintainer handoff.
      Group the findings by target family first, then by sanitizer/focus, and end with catalog rows that need retargeting.

      {% for shard in fanouts.fuzzer.nodes %}
      ### {{ shard.label }} :: {{ shard.id }} (status: {{ shard.status }})
      {{ shard.output or "(no output)" }}

      {% endfor %}
"""
    ).substitute(
        name=name,
        shards=shards,
        working_dir=working_dir,
        concurrency=concurrency,
        _FUZZ_CATALOG_SUPPORT_FILE=_FUZZ_CATALOG_SUPPORT_FILE,
    )
    return RenderedBundledTemplate(
        yaml=rendered_yaml,
        support_files=(
            RenderedBundledTemplateFile(
                relative_path=_FUZZ_CATALOG_SUPPORT_FILE,
                content=_render_codex_fuzz_catalog_csv(shards),
            ),
        ),
    )


_BUNDLED_TEMPLATES = (
    BundledTemplate(
        name="pipeline",
        example_name="pipeline.yaml",
        description="Generic Codex/Claude/Kimi starter DAG.",
    ),
    BundledTemplate(
        name="codex-fanout-repo-sweep",
        example_name="codex-fanout-repo-sweep.yaml",
        description="Codex repo sweep that fans out one plan into 8 review shards and a final merge.",
    ),
    BundledTemplate(
        name="codex-fuzz-matrix",
        example_name="fuzz/codex-fuzz-matrix.yaml",
        description="Codex fuzz starter that uses `fanout.matrix` for target families and sanitizer/seed variants.",
    ),
    BundledTemplate(
        name="codex-fuzz-matrix-derived",
        example_name="fuzz/codex-fuzz-matrix-derived.yaml",
        description="Codex fuzz starter that uses `fanout.derive` to compute reusable shard labels and workdirs from matrix inputs.",
    ),
    BundledTemplate(
        name="codex-fuzz-matrix-curated",
        example_name="fuzz/codex-fuzz-matrix-curated.yaml",
        description="Curated Codex fuzz matrix that uses `fanout.exclude`, `fanout.include`, and `fanout.derive` to tune real campaigns without a catalog file.",
    ),
    BundledTemplate(
        name="codex-fuzz-matrix-128",
        example_name="fuzz/codex-fuzz-matrix-128.yaml",
        description="128-shard Codex fuzz matrix that uses `fanout.matrix` for target families, strategies, and seed buckets.",
    ),
    BundledTemplate(
        name="codex-fuzz-hierarchical-128",
        example_name="fuzz/codex-fuzz-hierarchical-128.yaml",
        description="128-shard Codex fuzz matrix with per-target reducers that use fanout summaries to keep large merges readable.",
    ),
    BundledTemplate(
        name="codex-fuzz-hierarchical-grouped",
        example_name="fuzz/codex-fuzz-hierarchical-grouped.yaml",
        description="Configurable hierarchical Codex fuzz matrix that uses `fanout.group_by` to derive reducer families from the shard fanout.",
        parameters=(
            BundledTemplateParameter(
                name="bucket_count",
                description="Number of reusable seed buckets to render into the sidecar axes manifest.",
                default=str(_DEFAULT_FUZZ_HIERARCHICAL_BUCKET_COUNT),
            ),
            BundledTemplateParameter(
                name="concurrency",
                description="Maximum number of shards to run in parallel.",
                default=str(_DEFAULT_FUZZ_HIERARCHICAL_CONCURRENCY),
            ),
            BundledTemplateParameter(
                name="name",
                description="Pipeline name override.",
                default="codex-fuzz-hierarchical-grouped-<shards>",
            ),
            BundledTemplateParameter(
                name="working_dir",
                description="Pipeline working directory override.",
                default="./codex_fuzz_hierarchical_grouped_<shards>",
            ),
        ),
        support_files=(_FUZZ_HIERARCHICAL_GROUPED_AXES_SUPPORT_FILE,),
    ),
    BundledTemplate(
        name="codex-fuzz-hierarchical-manifest",
        example_name="fuzz/codex-fuzz-hierarchical-manifest.yaml",
        description="Configurable hierarchical Codex fuzz matrix that keeps reusable axes and reducer families in sidecar manifests.",
        parameters=(
            BundledTemplateParameter(
                name="bucket_count",
                description="Number of reusable seed buckets to render into the sidecar axes manifest.",
                default=str(_DEFAULT_FUZZ_HIERARCHICAL_BUCKET_COUNT),
            ),
            BundledTemplateParameter(
                name="concurrency",
                description="Maximum number of shards to run in parallel.",
                default=str(_DEFAULT_FUZZ_HIERARCHICAL_CONCURRENCY),
            ),
            BundledTemplateParameter(
                name="name",
                description="Pipeline name override.",
                default="codex-fuzz-hierarchical-manifest-<shards>",
            ),
            BundledTemplateParameter(
                name="working_dir",
                description="Pipeline working directory override.",
                default="./codex_fuzz_hierarchical_manifest_<shards>",
            ),
        ),
        support_files=(
            _FUZZ_HIERARCHICAL_AXES_SUPPORT_FILE,
            _FUZZ_HIERARCHICAL_FAMILIES_SUPPORT_FILE,
        ),
    ),
    BundledTemplate(
        name="codex-fuzz-matrix-manifest",
        example_name="fuzz/codex-fuzz-matrix-manifest.yaml",
        description="Configurable Codex fuzz matrix that keeps reusable axes in `fanout.matrix_path` and scales by rendering more seed buckets.",
        parameters=(
            BundledTemplateParameter(
                name="bucket_count",
                description="Number of reusable seed buckets to render into the sidecar manifest.",
                default=str(_DEFAULT_FUZZ_MATRIX_MANIFEST_BUCKET_COUNT),
            ),
            BundledTemplateParameter(
                name="concurrency",
                description="Maximum number of shards to run in parallel.",
                default=str(_DEFAULT_FUZZ_MATRIX_MANIFEST_CONCURRENCY),
            ),
            BundledTemplateParameter(
                name="name",
                description="Pipeline name override.",
                default="codex-fuzz-matrix-manifest-<shards>",
            ),
            BundledTemplateParameter(
                name="working_dir",
                description="Pipeline working directory override.",
                default="./codex_fuzz_matrix_manifest_<shards>",
            ),
        ),
        support_files=(_FUZZ_MATRIX_MANIFEST_SUPPORT_FILE,),
    ),
    BundledTemplate(
        name="codex-fuzz-matrix-manifest-128",
        example_name="fuzz/codex-fuzz-matrix-manifest-128.yaml",
        description="128-shard Codex fuzz matrix that loads its axes from `fanout.matrix_path` for easier maintainer edits.",
        support_files=("manifests/codex-fuzz-matrix-manifest-128.axes.yaml",),
    ),
    BundledTemplate(
        name="codex-fuzz-catalog",
        example_name="fuzz/codex-fuzz-catalog.yaml",
        description="Configurable Codex fuzz campaign backed by a CSV shard catalog; defaults to 128 shards and keeps per-shard labels and workdirs in the manifest.",
        parameters=(
            BundledTemplateParameter(
                name="shards",
                description="Number of catalog rows and Codex fuzz workers to render.",
                default=str(_DEFAULT_FUZZ_CATALOG_SHARDS),
            ),
            BundledTemplateParameter(
                name="concurrency",
                description="Maximum number of shards to run in parallel.",
                default=str(_DEFAULT_FUZZ_CATALOG_CONCURRENCY),
            ),
            BundledTemplateParameter(
                name="name",
                description="Pipeline name override.",
                default="codex-fuzz-catalog-<shards>",
            ),
            BundledTemplateParameter(
                name="working_dir",
                description="Pipeline working directory override.",
                default="./codex_fuzz_catalog_<shards>",
            ),
        ),
        support_files=(_FUZZ_CATALOG_SUPPORT_FILE,),
    ),
    BundledTemplate(
        name="codex-fuzz-swarm",
        example_name="fuzz/fuzz_codex_32.yaml",
        description="Configurable Codex fuzz swarm scaffold; defaults to 32 shards and scales cleanly to larger campaigns.",
        parameters=(
            BundledTemplateParameter(
                name="shards",
                description="Number of Codex fuzz workers to fan out.",
                default=str(_DEFAULT_FUZZ_SWARM_SHARDS),
            ),
            BundledTemplateParameter(
                name="concurrency",
                description="Maximum number of shards to run in parallel.",
                default=str(_DEFAULT_FUZZ_SWARM_CONCURRENCY),
            ),
            BundledTemplateParameter(
                name="name",
                description="Pipeline name override.",
                default="codex-fuzz-swarm-<shards>",
            ),
            BundledTemplateParameter(
                name="working_dir",
                description="Pipeline working directory override.",
                default="./codex_fuzz_swarm_<shards>",
            ),
        ),
    ),
    BundledTemplate(
        name="codex-fuzz-swarm-128",
        example_name="fuzz/fuzz_codex_128.yaml",
        description="128-shard Codex fuzzing swarm with init, retries, per-shard workdirs, and a merge reducer.",
    ),
    BundledTemplate(
        name="local-kimi-smoke",
        example_name="local-real-agents-kimi-smoke.yaml",
        description="Local Codex plus Claude-on-Kimi smoke DAG using `bootstrap: kimi`.",
    ),
    BundledTemplate(
        name="local-kimi-shell-init-smoke",
        example_name="local-real-agents-kimi-shell-init-smoke.yaml",
        description="Local Codex plus Claude-on-Kimi smoke DAG using explicit `shell_init: kimi`.",
    ),
    BundledTemplate(
        name="local-kimi-shell-wrapper-smoke",
        example_name="local-real-agents-kimi-shell-wrapper-smoke.yaml",
        description="Local Codex plus Claude-on-Kimi smoke DAG using an explicit `target.shell` Kimi wrapper.",
    ),
)

_BUNDLED_TEMPLATE_FILES = {template.name: template.example_name for template in _BUNDLED_TEMPLATES}
_BUNDLED_TEMPLATE_SUPPORT_FILES = {template.name: template.support_files for template in _BUNDLED_TEMPLATES}
_BUNDLED_TEMPLATE_RENDERERS = {
    "codex-fuzz-hierarchical-grouped": _render_codex_fuzz_hierarchical_grouped_template,
    "codex-fuzz-hierarchical-manifest": _render_codex_fuzz_hierarchical_template,
    "codex-fuzz-matrix-manifest": _render_codex_fuzz_matrix_manifest_template,
    "codex-fuzz-catalog": _render_codex_fuzz_catalog_template,
    "codex-fuzz-swarm": _render_codex_fuzz_swarm_template,
}

DEFAULT_PIPELINE_YAML = """name: parallel-code-orchestration
description: Codex plans, Claude implements, and Kimi reviews in parallel before a final Codex merge.
working_dir: .
concurrency: 3
nodes:
  - id: plan
    agent: codex
    model: gpt-5-codex
    tools: read_only
    capture: final
    retries: 1
    retry_backoff_seconds: 1
    prompt: |
      Inspect the repository and create a short implementation plan.

  - id: implement
    agent: claude
    model: claude-sonnet-4-5
    tools: read_write
    capture: final
    depends_on: [plan]
    prompt: |
      Use the plan below and implement the requested change.

      Plan:
      {{ nodes.plan.output }}

  - id: review
    agent: kimi
    model: kimi-k2-turbo-preview
    tools: read_only
    capture: trace
    depends_on: [plan]
    prompt: |
      Review the proposed implementation plan.

      Plan:
      {{ nodes.plan.output }}

  - id: merge
    agent: codex
    model: gpt-5-codex
    tools: read_only
    depends_on: [implement, review]
    success_criteria:
      - kind: output_contains
        value: success
    prompt: |
      Combine these two perspectives into a final release summary and include the word success.

      Implementation output:
      {{ nodes.implement.output }}

      Review trace:
      {{ nodes.review.output }}
"""


def load_default_pipeline_yaml() -> str:
    example_path = bundled_example_path("pipeline.yaml")
    if example_path.exists():
        return example_path.read_text(encoding="utf-8")
    return DEFAULT_PIPELINE_YAML


def bundled_example_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "examples" / name


def bundled_templates() -> tuple[BundledTemplate, ...]:
    return _BUNDLED_TEMPLATES


def bundled_template_names() -> tuple[str, ...]:
    return tuple(template.name for template in bundled_templates())


def bundled_template_path(name: str) -> Path:
    try:
        example_name = _BUNDLED_TEMPLATE_FILES[name]
    except KeyError as exc:
        available = ", ".join(f"`{template}`" for template in bundled_template_names())
        raise ValueError(
            f"unknown bundled template `{name}` (available: {available}; see `agentflow templates`)"
        ) from exc
    return bundled_example_path(example_name)


def bundled_template_support_files(name: str) -> tuple[str, ...]:
    try:
        return _BUNDLED_TEMPLATE_SUPPORT_FILES[name]
    except KeyError as exc:
        available = ", ".join(f"`{template}`" for template in bundled_template_names())
        raise ValueError(
            f"unknown bundled template `{name}` (available: {available}; see `agentflow templates`)"
        ) from exc


def render_bundled_template(name: str, values: Mapping[str, str] | None = None) -> RenderedBundledTemplate:
    template_values = dict(values or {})
    if name == "pipeline":
        if template_values:
            raise ValueError("template `pipeline` does not accept `--set` values")
        return RenderedBundledTemplate(yaml=load_default_pipeline_yaml())

    renderer = _BUNDLED_TEMPLATE_RENDERERS.get(name)
    if renderer is not None:
        return renderer(template_values)

    template_path = bundled_template_path(name)
    if template_values:
        raise ValueError(f"template `{name}` does not accept `--set` values")

    rendered_support_files = tuple(
        RenderedBundledTemplateFile(
            relative_path=relative_path,
            content=(template_path.parent / relative_path).resolve().read_text(encoding="utf-8"),
        )
        for relative_path in bundled_template_support_files(name)
    )
    return RenderedBundledTemplate(
        yaml=template_path.read_text(encoding="utf-8"),
        support_files=rendered_support_files,
    )


def load_bundled_template_yaml(name: str, values: Mapping[str, str] | None = None) -> str:
    return render_bundled_template(name, values=values).yaml


def default_smoke_pipeline_path() -> str:
    return str(bundled_example_path("local-real-agents-kimi-smoke.yaml"))
