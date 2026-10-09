"""Search the web and fetch one result through anonymous Parallel Search MCP.

Install and run from the repository root in an activated virtual environment:

    pip install -e '.[parallel-search]'
    agentflow run examples/parallel_search.py --output summary

Set AGENTFLOW_SEARCH_QUERY to change the query. This is a deterministic Python
utility node, not an LLM agent loop. It requires network access but no API keys.
"""

from __future__ import annotations

import os

from agentflow import Graph, python_node

# Keep the node self-contained: the local runner executes it with python3.
SEARCH_CODE = r'''
import asyncio
import json
import os
from datetime import timedelta
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def research():
    query = os.environ["AGENTFLOW_SEARCH_QUERY"]
    session_id = uuid4().hex
    async with httpx.AsyncClient(
        headers={"User-Agent": "agentflow/0.1.0 (parallel-search-example)"},
        timeout=60,
    ) as http_client:
        async with streamable_http_client(
            "https://search.parallel.ai/mcp", http_client=http_client,
        ) as (read, write, _):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=90),
            ) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                if not {"web_search", "web_fetch"} <= tools:
                    raise RuntimeError("Parallel MCP is missing search or fetch tools")

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    if result.isError:
                        raise RuntimeError(f"{name} failed: {result.content}")
                    if result.structuredContent is not None:
                        return result.structuredContent
                    # Older MCP servers may return JSON as a text content block.
                    text = next(block.text for block in result.content if block.type == "text")
                    return json.loads(text)

                search = await call("web_search", {
                    "objective": query,
                    "search_queries": [query],
                    "session_id": session_id,
                })
                if not search.get("results"):
                    raise RuntimeError("Search returned no results; try another query")
                url = search["results"][0]["url"]
                fetched = await call("web_fetch", {
                    "urls": [url],
                    "session_id": session_id,
                })
                if not fetched.get("results"):
                    raise RuntimeError(f"Fetch returned no content: {fetched.get('errors', [])}")
                # Keep excerpt lines separate for runners with bounded line buffers.
                print(json.dumps({"search": search, "fetch": fetched}, ensure_ascii=False, indent=2))


asyncio.run(research())
'''

with Graph("parallel-search", working_dir="..") as dag:
    python_node(
        task_id="research",
        code=SEARCH_CODE,
        env={
            "AGENTFLOW_SEARCH_QUERY": os.environ.get(
                "AGENTFLOW_SEARCH_QUERY", "Python asyncio TaskGroup documentation"
            ),
        },
        timeout_seconds=180,
    )

if __name__ == "__main__":
    print(dag.to_json())
