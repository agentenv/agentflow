"""Protocol-level tests of the runnable example, without live web requests."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agentflow.loader import load_pipeline_from_path


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "parallel_search.py"


@pytest.mark.parametrize("failure", [None, "tool_error", "empty_search", "empty_fetch", "oversized_unicode"])
def test_parallel_search_example(monkeypatch, capsys, failure):
    pytest.importorskip("mcp")
    monkeypatch.setenv("AGENTFLOW_SEARCH_QUERY", "Python asyncio cancellation documentation")
    pipeline = load_pipeline_from_path(EXAMPLE)
    node = pipeline.nodes[0]
    assert str(node.agent) == "python"
    assert node.mcps == []
    assert node.provider is None
    assert node.timeout_seconds == 180
    monkeypatch.setenv("AGENTFLOW_SEARCH_QUERY", node.env["AGENTFLOW_SEARCH_QUERY"])
    requests = []

    def respond(request):
        assert str(request.url) == "https://search.parallel.ai/mcp"
        assert request.headers["User-Agent"] == "agentflow/0.1.0 (parallel-search-example)"
        assert "authorization" not in request.headers
        payload = json.loads(request.content)
        requests.append(payload)
        method = payload["method"]
        if "id" not in payload:
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "inputSchema": {"type": "object"}}
                                for name in ("web_search", "web_fetch")]}
        else:
            assert method == "tools/call"
            name = payload["params"]["name"]
            if failure == "tool_error":
                result = {"isError": True, "content": [{"type": "text", "text": "rate limit"}]}
            else:
                empty = failure == ("empty_search" if name == "web_search" else "empty_fetch")
                data = {"results": [] if empty else [{"url": "https://docs.python.org/3/library/asyncio-task.html",
                                                       "excerpts": ["TaskGroup waits for its tasks." + "x" * 20000, "y" * 20000]}]}
                if failure == "oversized_unicode":
                    data["results"][0]["excerpts"] = ["漢\u2028" * 12000]
                # Exercise both supported MCP result representations.
                result = {"content": [{"type": "text", "text": json.dumps(data)}]}
                if name == "web_search":
                    result["structuredContent"] = data
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    original_client = httpx.AsyncClient

    def client(**kwargs):
        return original_client(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    if failure:
        # The SDK wraps caller errors in AnyIO task groups.
        with pytest.raises(Exception) as caught:
            exec(compile(node.prompt, str(EXAMPLE), "exec"), {})
        expected = {"tool_error": "web_search failed", "empty_search": "Search returned no results",
                    "empty_fetch": "Fetch returned no content",
                    "oversized_unicode": "An excerpt exceeds the runner line limit"}[failure]

        def messages(error):
            return str(error) + " ".join(messages(e) for e in getattr(error, "exceptions", []))

        assert expected in messages(caught.value)
    else:
        exec(compile(node.prompt, str(EXAMPLE), "exec"), {})
        text = capsys.readouterr().out
        assert len(text.encode()) > 65536
        assert max(len(line.encode()) for line in text.split("\n")) < 65536
        output = json.loads(text)
        assert output["fetch"]["results"][0]["excerpts"]
        calls = [r["params"] for r in requests if r["method"] == "tools/call"]
        search, fetch = calls
        assert search["name"] == "web_search"
        assert search["arguments"]["search_queries"] == [node.env["AGENTFLOW_SEARCH_QUERY"]]
        assert fetch["name"] == "web_fetch"
        assert fetch["arguments"]["urls"] == [output["search"]["results"][0]["url"]]
        assert search["arguments"]["session_id"] == fetch["arguments"]["session_id"]
        assert len(search["arguments"]["session_id"]) == 32
