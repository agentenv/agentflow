"""Pure construction tests: no model or agent CLI is executed."""
import json
from pathlib import Path
import tomllib

from pydantic import BaseModel, ValidationError
import pytest

from agentflow import ActorNode, ActorRequirements, AgentProfile, ArtifactContract, Graph, InstructionBundle
from agentflow.agents.registry import AdapterRegistry
from agentflow.agents.codex import CodexAdapter
from agentflow.agents.claude import ClaudeAdapter
from agentflow.agents.pi import PiAdapter
from agentflow.agents.terminus import TerminusAdapter, HARBOR_VERSION
from agentflow.prepared import ExecutionPaths
from agentflow.profiles import BackendCapabilities, register_backend_capabilities, BACKEND_CAPABILITIES
from agentflow.specs import NodeSpec, SecretRef
from agentflow.tuned_agents import resolve_node_for_execution
from agentflow.traces import create_trace_parser


class Input(BaseModel):
    subject: str


class Output(BaseModel):
    artifact: str


class Writer(ActorNode[Input, Output]):
    actor_id = "example.writer"
    input_type = Input
    output_type = Output

    def instructions(self, inputs):
        return InstructionBundle(text=f"Write about {inputs.subject}", resources={"guide.txt": "Be precise."})

    def requirements(self):
        return ActorRequirements(capabilities={"read_write"})

    def output_artifacts(self, inputs):
        return [ArtifactContract(name="article", path="output/article.txt")]


def paths(tmp_path):
    return ExecutionPaths(tmp_path, tmp_path / "runtime", "/workspace", "/agentflow-runtime", tmp_path)


def test_actor_uses_existing_graph_edges_and_serialization():
    with Graph("actors") as graph:
        first = Writer().bind(node_id="first", inputs={"subject": "A"}, profile=AgentProfile(name="default", agent="codex"), target={"kind": "docker"})
        second = Writer().bind(node_id="second", inputs={"subject": "B"}, profile=AgentProfile(name="default", agent="claude"), target={"kind": "docker"})
        first >> second
    spec = graph.to_spec()
    assert spec.nodes[1].depends_on == ["first"]
    decoded = NodeSpec.model_validate_json(spec.nodes[1].model_dump_json())
    assert decoded.actor["id"] == "example.writer"
    assert decoded.actor["inputs"] == {"subject": "B"}
    assert decoded.actor["artifacts"][0]["path"] == "output/article.txt"
    assert decoded.agent_profile["agent"] == "claude"


def test_actor_validation_precedes_graph_registration():
    graph = Graph("invalid")
    with pytest.raises(ValidationError):
        Writer().bind(node_id="bad", graph=graph, inputs={}, profile=AgentProfile(name="x", agent="codex"), target={"kind": "docker"})
    assert graph._nodes == {}
    with pytest.raises(ValueError, match="cannot override"):
        Writer().bind(node_id="bad", graph=graph, inputs={"subject": "A"}, profile=AgentProfile(name="x", agent="codex"), target={"kind": "docker"}, model="replacement")


def test_bundle_is_immutable_content_addressed_and_rejects_escape(tmp_path):
    bundle = Writer().instructions(Input(subject="A"))
    identity = bundle.materialize(tmp_path / "bundle")
    assert identity == bundle.identity
    assert (tmp_path / "bundle" / "guide.txt").stat().st_mode & 0o222 == 0
    assert json.loads((tmp_path / "bundle" / "bundle.json").read_text())["sha256"] == identity
    with pytest.raises(FileExistsError):
        bundle.materialize(tmp_path / "bundle")
    with pytest.raises(ValidationError):
        InstructionBundle(text="x", resources={"../escape": "no"})


@pytest.mark.parametrize("backend", ["codex", "claude", "pi", "terminus"])
def test_model_only_profiles_disable_builtin_search(backend, tmp_path):
    profile = AgentProfile(name="default", agent=backend, model="openai/test", model_settings={"web_search": "disabled"})
    node = NodeSpec(id="run", agent=backend, prompt="hello", target={"kind": "docker"}, **profile.node_options())
    prepared = AdapterRegistry().get(backend).prepare(node, node.prompt, paths(tmp_path))
    if backend == "codex":
        assert tomllib.loads(prepared.runtime_files["codex_home/config.toml"])["web_search"] == "disabled"
    if backend == "claude":
        assert "WebSearch" not in " ".join(prepared.command)
        assert "WebFetch" not in " ".join(prepared.command)


@pytest.mark.parametrize("backend", ["codex", "claude", "pi"])
def test_docker_preparation_never_materializes_host_keys(backend, tmp_path, monkeypatch):
    key = "sensitive-host-key-that-must-not-appear"
    monkeypatch.setenv("PROVIDER_KEY", key)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(key)
    monkeypatch.setenv("HOME", str(home))
    node = NodeSpec(id="safe", agent=backend, prompt="hello", target={"kind": "docker"}, provider={"name": "example", "base_url": "https://provider.invalid", "api_key_env": "PROVIDER_KEY"}, secret_env={"PROVIDER_KEY": SecretRef(path="/run/secrets/provider")})
    prepared = AdapterRegistry().get(backend).prepare(node, node.prompt, paths(tmp_path))
    assert key not in repr(prepared)
    assert key not in node.model_dump_json()
    assert not prepared.runtime_symlinks
    assert prepared.command[0] == "python3"
    assert "PROVIDER_KEY" not in prepared.env
    refs = json.loads(prepared.runtime_files["secret-references.json"])
    assert refs["files"] == {"PROVIDER_KEY": "/run/secrets/provider"}
    node.target.inherit_credentials = True
    with pytest.raises(ValueError, match="host credentials"):
        AdapterRegistry().get(backend).prepare(node, node.prompt, paths(tmp_path))


def test_generic_mcp_auth_timeouts_and_codex_model_rendering(tmp_path):
    node = NodeSpec(id="mcp", agent="codex", prompt="hello", target={"kind": "docker"}, model="chosen", provider={"name": "provider.with.dot", "wire_api": "responses"}, model_settings={"context_window": 12345, "max_output_tokens": 456, "reasoning_effort": "high"}, mcps=[{"name": "service.with.dot", "transport": "streamable_http", "url": "https://service.invalid", "startup_timeout_sec": 30, "tool_timeout_sec": 120, "env_http_headers": {"x-api-key": "SERVICE_KEY"}, "secret_env": {"SERVICE_KEY": {"path": "/run/secrets/service"}}}])
    prepared = CodexAdapter().prepare(node, node.prompt, paths(tmp_path))
    config = tomllib.loads(prepared.runtime_files["codex_home/agentflow.config.toml"])
    assert "profiles" not in config
    assert config["model_reasoning_effort"] == "high"
    assert config["model_max_output_tokens"] == 456
    assert config["model_context_window"] == 12345
    assert config["mcp_servers"]["service.with.dot"]["startup_timeout_sec"] == 30
    assert config["mcp_servers"]["service.with.dot"]["env_http_headers"] == {"x-api-key": "SERVICE_KEY"}


def test_capabilities_reject_incompatible_features_and_protocols():
    for agent in ("pi", "terminus"):
        profile = AgentProfile(name="invalid", agent=agent, mcps=[{"name": "example", "command": "example"}])
        with pytest.raises(ValueError, match="mcp:stdio"):
            profile.node_options()
    with pytest.raises(ValueError, match="model settings"):
        AgentProfile(name="invalid", agent="claude", model_settings={"context_window": 123}).node_options()
    with pytest.raises(ValueError, match="read_only"):
        AgentProfile(name="invalid", agent="kimi", tools="read_only").node_options()
    AgentProfile(name="valid", agent="kimi", tools="read_write").node_options(target_kind="local")
    with pytest.raises(ValueError, match="provider protocol"):
        AgentProfile(name="invalid", agent="pi", provider={"wire_api": "responses"}).node_options()
    with pytest.raises(ValueError, match="authentication"):
        AgentProfile(name="invalid", agent="codex", provider={"headers": {"Authorization": "secret"}})


def test_custom_backend_registry_round_trip(tmp_path):
    registry = AdapterRegistry()
    registry.register("example-backend", CodexAdapter())
    node = NodeSpec(id="custom", agent="example-backend", prompt="hello")
    resolution = resolve_node_for_execution(node, tmp_path, registry=registry)
    assert resolution.runtime_agent == "example-backend"
    event = create_trace_parser("example-backend", "custom").feed("message")[0]
    assert event.model_dump(mode="json")["agent"] == "example-backend"


def test_terminus_prepares_real_controller_with_reference_only_configuration(tmp_path):
    node = NodeSpec(id="t", agent="terminus", model="openai/example", tools="read_write", prompt="Do the task", target={"kind": "docker"}, provider={"api_key_env": "OPENAI_API_KEY", "wire_api": "responses"}, secret_env={"OPENAI_API_KEY": {"path": "/run/secrets/provider"}}, model_settings={"max_turns": 5, "max_output_tokens": 1000})
    prepared = TerminusAdapter().prepare(node, node.prompt, paths(tmp_path))
    config = json.loads(prepared.runtime_files["terminus-config.json"])
    assert config["harbor_version"] == HARBOR_VERSION
    assert config["options"]["max_turns"] == 5
    assert config["options"]["llm_call_kwargs"] == {"max_tokens": 1000}
    controller = prepared.runtime_files["terminus_controller.py"]
    assert "from harbor.agents.terminus_2.terminus_2 import Terminus2" in controller
    assert "await controller.run(" in controller
    assert 'options.setdefault("llm_kwargs"' not in controller
    assert "DockerClient" not in controller
    with pytest.raises(ValueError, match="target local"):
        TerminusAdapter().prepare(NodeSpec(id="t", agent="terminus", prompt="x", model="x"), "x", paths(tmp_path))
