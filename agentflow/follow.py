from __future__ import annotations

import asyncio
from pathlib import Path

from agentflow.specs import RunEvent
from agentflow.store import RunStore


_TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


async def follow_run_events(
    store: RunStore,
    run_id: str,
    *,
    poll_interval: float = 0.1,
    refresh_run_state: bool = True,
):
    events_path = store.run_dir(run_id) / "events.jsonl"
    position = 0

    while True:
        if events_path.exists():
            with events_path.open("r", encoding="utf-8") as handle:
                handle.seek(position)
                while True:
                    line = handle.readline()
                    if not line:
                        break
                    position = handle.tell()
                    stripped = line.strip()
                    if not stripped:
                        continue
                    yield RunEvent.model_validate_json(stripped)

        run = store.refresh_run(run_id) if refresh_run_state else store.get_run(run_id)
        if run.status.value in _TERMINAL_RUN_STATUSES:
            break
        await asyncio.sleep(poll_interval)
