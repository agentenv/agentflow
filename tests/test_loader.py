from __future__ import annotations

from agentflow.loader import load_pipeline_from_data, load_pipeline_from_path, load_pipeline_from_text


def test_load_pipeline_from_path_expands_home_relative_working_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: home-working-dir
working_dir: ~/workspace
nodes:
  - id: plan
    agent: codex
    prompt: hi
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))

    pipeline = load_pipeline_from_path(pipeline_path)

    assert pipeline.working_dir == str((home / "workspace").resolve())


def test_load_pipeline_from_path_resolves_relative_cwd_from_expanded_home_working_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: home-working-dir-relative-cwd
working_dir: ~/workspace
nodes:
  - id: plan
    agent: codex
    prompt: hi
    target:
      kind: local
      cwd: task
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))

    pipeline = load_pipeline_from_path(pipeline_path)

    assert pipeline.nodes[0].target.cwd == str((home / "workspace" / "task").resolve())


def test_load_pipeline_from_path_expands_home_relative_local_cwds(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()

    pipeline_path = tmp_path / "pipeline.yaml"
    pipeline_path.write_text(
        """name: home-local-cwds
working_dir: .
local_target_defaults:
  cwd: ~/shared
nodes:
  - id: plan
    agent: codex
    prompt: hi
  - id: review
    agent: claude
    prompt: hi
    target:
      kind: local
      cwd: ~/task
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))

    pipeline = load_pipeline_from_path(pipeline_path)

    assert pipeline.local_target_defaults is not None
    assert pipeline.local_target_defaults.cwd == str((home / "shared").resolve())
    assert pipeline.nodes[1].target.cwd == str((home / "task").resolve())


def test_load_pipeline_from_text_resolves_relative_paths_from_explicit_base_dir(tmp_path):
    workspace = tmp_path / "workspace"
    pipeline = load_pipeline_from_text(
        """name: api-yaml
working_dir: .
local_target_defaults:
  cwd: shared
nodes:
  - id: plan
    agent: codex
    prompt: hi
    target:
      kind: local
      cwd: task
""",
        base_dir=workspace,
    )

    assert pipeline.working_dir == str(workspace.resolve())
    assert pipeline.local_target_defaults is not None
    assert pipeline.local_target_defaults.cwd == str((workspace / "shared").resolve())
    assert pipeline.nodes[0].target.cwd == str((workspace / "task").resolve())


def test_load_pipeline_from_data_resolves_relative_paths_from_explicit_base_dir(tmp_path):
    workspace = tmp_path / "workspace"
    pipeline = load_pipeline_from_data(
        {
            "name": "api-json",
            "working_dir": ".",
            "nodes": [
                {
                    "id": "plan",
                    "agent": "codex",
                    "prompt": "hi",
                    "target": {
                        "kind": "local",
                        "cwd": "task",
                    },
                }
            ],
        },
        base_dir=workspace,
    )

    assert pipeline.working_dir == str(workspace.resolve())
    assert pipeline.nodes[0].target.cwd == str((workspace / "task").resolve())


def test_load_pipeline_from_text_resolves_node_defaults_and_agent_defaults_relative_targets(tmp_path):
    workspace = tmp_path / "workspace"
    pipeline = load_pipeline_from_text(
        """name: node-default-targets
working_dir: .
node_defaults:
  target:
    kind: local
    shell: bash
agent_defaults:
  codex:
    target:
      kind: local
      cwd: shared
nodes:
  - id: plan
    agent: codex
    prompt: hi
  - id: review
    agent: codex
    prompt: hi
    target:
      kind: local
      cwd: task
""",
        base_dir=workspace,
    )

    assert pipeline.nodes[0].target.shell == "bash"
    assert pipeline.nodes[0].target.cwd == str((workspace / "shared").resolve())
    assert pipeline.nodes[1].target.shell == "bash"
    assert pipeline.nodes[1].target.cwd == str((workspace / "task").resolve())


def test_load_pipeline_from_text_expands_fanout_nodes_before_resolving_relative_cwds(tmp_path):
    workspace = tmp_path / "workspace"
    pipeline = load_pipeline_from_text(
        """name: fanout-loader
working_dir: .
nodes:
  - id: fuzz
    fanout:
      count: 2
      as: shard
    agent: codex
    prompt: shard {{ shard.number }}
    target:
      kind: local
      cwd: agents/agent_{{ shard.suffix }}
""",
        base_dir=workspace,
    )

    assert pipeline.fanouts == {"fuzz": ["fuzz_0", "fuzz_1"]}
    assert [node.id for node in pipeline.nodes] == ["fuzz_0", "fuzz_1"]
    assert pipeline.nodes[0].target.cwd == str((workspace / "agents" / "agent_0").resolve())
    assert pipeline.nodes[1].target.cwd == str((workspace / "agents" / "agent_1").resolve())


def test_load_pipeline_from_text_expands_fanout_values_before_resolving_relative_cwds(tmp_path):
    workspace = tmp_path / "workspace"
    pipeline = load_pipeline_from_text(
        """name: fanout-values-loader
working_dir: .
nodes:
  - id: fuzz
    fanout:
      as: shard
      values:
        - target: libpng
        - target: sqlite
    agent: codex
    prompt: shard {{ shard.target }}
    target:
      kind: local
      cwd: agents/{{ shard.target }}/{{ shard.suffix }}
""",
        base_dir=workspace,
    )

    assert pipeline.fanouts == {"fuzz": ["fuzz_0", "fuzz_1"]}
    assert [node.id for node in pipeline.nodes] == ["fuzz_0", "fuzz_1"]
    assert pipeline.nodes[0].target.cwd == str((workspace / "agents" / "libpng" / "0").resolve())
    assert pipeline.nodes[1].target.cwd == str((workspace / "agents" / "sqlite" / "1").resolve())


def test_load_pipeline_from_text_expands_fanout_derived_fields_before_resolving_relative_cwds(tmp_path):
    workspace = tmp_path / "workspace"
    pipeline = load_pipeline_from_text(
        """name: fanout-derived-loader
working_dir: .
nodes:
  - id: fuzz
    fanout:
      count: 2
      as: shard
      derive:
        workspace: agents/agent_{{ shard.suffix }}
    agent: codex
    prompt: shard {{ shard.workspace }}
    target:
      kind: local
      cwd: "{{ shard.workspace }}"
""",
        base_dir=workspace,
    )

    assert pipeline.fanouts == {"fuzz": ["fuzz_0", "fuzz_1"]}
    assert [node.id for node in pipeline.nodes] == ["fuzz_0", "fuzz_1"]
    assert pipeline.nodes[0].prompt == "shard agents/agent_0"
    assert pipeline.nodes[1].prompt == "shard agents/agent_1"
    assert pipeline.nodes[0].fanout_member is not None
    assert pipeline.nodes[0].fanout_member["workspace"] == "agents/agent_0"
    assert pipeline.nodes[1].fanout_member["workspace"] == "agents/agent_1"
    assert pipeline.nodes[0].target.cwd == str((workspace / "agents" / "agent_0").resolve())
    assert pipeline.nodes[1].target.cwd == str((workspace / "agents" / "agent_1").resolve())


def test_load_pipeline_from_text_expands_fanout_matrix_before_resolving_relative_cwds(tmp_path):
    workspace = tmp_path / "workspace"
    pipeline = load_pipeline_from_text(
        """name: fanout-matrix-loader
working_dir: .
nodes:
  - id: fuzz
    fanout:
      as: shard
      matrix:
        family:
          - target: libpng
          - target: sqlite
        variant:
          - sanitizer: asan
          - sanitizer: ubsan
    agent: codex
    prompt: shard {{ shard.target }} {{ shard.sanitizer }}
    target:
      kind: local
      cwd: agents/{{ shard.target }}/{{ shard.sanitizer }}/{{ shard.suffix }}
""",
        base_dir=workspace,
    )

    assert pipeline.fanouts == {"fuzz": ["fuzz_0", "fuzz_1", "fuzz_2", "fuzz_3"]}
    assert [node.id for node in pipeline.nodes] == ["fuzz_0", "fuzz_1", "fuzz_2", "fuzz_3"]
    assert pipeline.nodes[0].target.cwd == str((workspace / "agents" / "libpng" / "asan" / "0").resolve())
    assert pipeline.nodes[1].target.cwd == str((workspace / "agents" / "libpng" / "ubsan" / "1").resolve())
    assert pipeline.nodes[2].target.cwd == str((workspace / "agents" / "sqlite" / "asan" / "2").resolve())
    assert pipeline.nodes[3].target.cwd == str((workspace / "agents" / "sqlite" / "ubsan" / "3").resolve())


def test_load_pipeline_from_text_expands_grouped_fanout_before_resolving_relative_cwds(tmp_path):
    workspace = tmp_path / "workspace"
    pipeline = load_pipeline_from_text(
        """name: fanout-group-by-loader
working_dir: .
nodes:
  - id: fuzz
    fanout:
      as: shard
      matrix:
        family:
          - target: libpng
            corpus: png
          - target: sqlite
            corpus: sql
        variant:
          - sanitizer: asan
          - sanitizer: ubsan
    agent: codex
    prompt: shard {{ shard.target }} {{ shard.sanitizer }}
  - id: family_merge
    fanout:
      as: family
      group_by:
        from: fuzz
        fields: [target, corpus]
    agent: codex
    depends_on: [fuzz]
    prompt: merge {{ family.target }} {{ family.corpus }}
    target:
      kind: local
      cwd: reducers/{{ family.target }}/{{ family.suffix }}
""",
        base_dir=workspace,
    )

    assert pipeline.fanouts == {
        "fuzz": ["fuzz_0", "fuzz_1", "fuzz_2", "fuzz_3"],
        "family_merge": ["family_merge_0", "family_merge_1"],
    }
    assert [node.id for node in pipeline.nodes] == [
        "fuzz_0",
        "fuzz_1",
        "fuzz_2",
        "fuzz_3",
        "family_merge_0",
        "family_merge_1",
    ]
    assert pipeline.node_map["family_merge_0"].prompt == "merge libpng png"
    assert pipeline.node_map["family_merge_1"].prompt == "merge sqlite sql"
    assert pipeline.node_map["family_merge_0"].target.cwd == str((workspace / "reducers" / "libpng" / "0").resolve())
    assert pipeline.node_map["family_merge_1"].target.cwd == str((workspace / "reducers" / "sqlite" / "1").resolve())
    assert pipeline.node_map["family_merge_0"].fanout_member["member_ids"] == ["fuzz_0", "fuzz_1"]
    assert pipeline.node_map["family_merge_1"].fanout_member["member_ids"] == ["fuzz_2", "fuzz_3"]
    assert pipeline.node_map["family_merge_0"].depends_on == ["fuzz_0", "fuzz_1"]
    assert pipeline.node_map["family_merge_1"].depends_on == ["fuzz_2", "fuzz_3"]


def test_load_pipeline_from_text_expands_batched_fanout_before_resolving_relative_cwds(tmp_path):
    workspace = tmp_path / "workspace"
    pipeline = load_pipeline_from_text(
        """name: fanout-batches-loader
working_dir: .
nodes:
  - id: fuzz
    fanout:
      count: 5
      as: shard
      derive:
        workspace: agents/agent_{{ shard.suffix }}
    agent: codex
    prompt: shard {{ shard.number }} {{ shard.workspace }}
    target:
      kind: local
      cwd: "{{ shard.workspace }}"
  - id: batch_merge
    fanout:
      as: batch
      batches:
        from: fuzz
        size: 2
    agent: codex
    depends_on: [fuzz]
    prompt: batch {{ batch.start_number }}-{{ batch.end_number }} {{ batch.size }}
    target:
      kind: local
      cwd: reducers/{{ batch.suffix }}
""",
        base_dir=workspace,
    )

    assert pipeline.fanouts == {
        "fuzz": ["fuzz_0", "fuzz_1", "fuzz_2", "fuzz_3", "fuzz_4"],
        "batch_merge": ["batch_merge_0", "batch_merge_1", "batch_merge_2"],
    }
    assert [node.id for node in pipeline.nodes] == [
        "fuzz_0",
        "fuzz_1",
        "fuzz_2",
        "fuzz_3",
        "fuzz_4",
        "batch_merge_0",
        "batch_merge_1",
        "batch_merge_2",
    ]
    assert pipeline.node_map["fuzz_0"].target.cwd == str((workspace / "agents" / "agent_0").resolve())
    assert pipeline.node_map["batch_merge_0"].prompt == "batch 1-2 2"
    assert pipeline.node_map["batch_merge_1"].prompt == "batch 3-4 2"
    assert pipeline.node_map["batch_merge_2"].prompt == "batch 5-5 1"
    assert pipeline.node_map["batch_merge_0"].target.cwd == str((workspace / "reducers" / "0").resolve())
    assert pipeline.node_map["batch_merge_1"].target.cwd == str((workspace / "reducers" / "1").resolve())
    assert pipeline.node_map["batch_merge_2"].target.cwd == str((workspace / "reducers" / "2").resolve())
    assert pipeline.node_map["batch_merge_0"].depends_on == ["fuzz_0", "fuzz_1"]
    assert pipeline.node_map["batch_merge_1"].depends_on == ["fuzz_2", "fuzz_3"]
    assert pipeline.node_map["batch_merge_2"].depends_on == ["fuzz_4"]


def test_load_pipeline_from_path_expands_fanout_values_path_before_resolving_relative_cwds(tmp_path):
    workspace = tmp_path / "workspace"
    manifests = workspace / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "shards.csv").write_text(
        "target,seed\nlibpng,1001\nsqlite,2002\n",
        encoding="utf-8",
    )
    pipeline_path = workspace / "pipeline.yaml"
    pipeline_path.write_text(
        """name: fanout-values-path-loader
working_dir: .
nodes:
  - id: fuzz
    fanout:
      as: shard
      values_path: manifests/shards.csv
    agent: codex
    prompt: shard {{ shard.target }} {{ shard.seed }}
    target:
      kind: local
      cwd: agents/{{ shard.target }}/{{ shard.suffix }}
""",
        encoding="utf-8",
    )

    pipeline = load_pipeline_from_path(pipeline_path)

    assert pipeline.fanouts == {"fuzz": ["fuzz_0", "fuzz_1"]}
    assert [node.id for node in pipeline.nodes] == ["fuzz_0", "fuzz_1"]
    assert pipeline.nodes[0].prompt == "shard libpng 1001"
    assert pipeline.nodes[1].prompt == "shard sqlite 2002"
    assert pipeline.nodes[0].target.cwd == str((workspace / "agents" / "libpng" / "0").resolve())
    assert pipeline.nodes[1].target.cwd == str((workspace / "agents" / "sqlite" / "1").resolve())


def test_load_pipeline_from_path_expands_fanout_matrix_path_before_resolving_relative_cwds(tmp_path):
    workspace = tmp_path / "workspace"
    manifests = workspace / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "axes.yaml").write_text(
        """family:
  - target: libpng
  - target: sqlite
variant:
  - sanitizer: asan
  - sanitizer: ubsan
""",
        encoding="utf-8",
    )
    pipeline_path = workspace / "pipeline.yaml"
    pipeline_path.write_text(
        """name: fanout-matrix-path-loader
working_dir: .
nodes:
  - id: fuzz
    fanout:
      as: shard
      matrix_path: manifests/axes.yaml
    agent: codex
    prompt: shard {{ shard.target }} {{ shard.sanitizer }}
    target:
      kind: local
      cwd: agents/{{ shard.target }}/{{ shard.sanitizer }}/{{ shard.suffix }}
""",
        encoding="utf-8",
    )

    pipeline = load_pipeline_from_path(pipeline_path)

    assert pipeline.fanouts == {"fuzz": ["fuzz_0", "fuzz_1", "fuzz_2", "fuzz_3"]}
    assert [node.id for node in pipeline.nodes] == ["fuzz_0", "fuzz_1", "fuzz_2", "fuzz_3"]
    assert pipeline.nodes[0].prompt == "shard libpng asan"
    assert pipeline.nodes[1].prompt == "shard libpng ubsan"
    assert pipeline.nodes[2].prompt == "shard sqlite asan"
    assert pipeline.nodes[3].prompt == "shard sqlite ubsan"
    assert pipeline.nodes[0].target.cwd == str((workspace / "agents" / "libpng" / "asan" / "0").resolve())
    assert pipeline.nodes[3].target.cwd == str((workspace / "agents" / "sqlite" / "ubsan" / "3").resolve())
