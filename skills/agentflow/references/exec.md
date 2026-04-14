# AgentFlow Exec & Inline Execution Reference

This reference covers running agents without creating pipeline files — the `exec` command for single agent calls, and inline/stdin modes for full pipelines.

## Table of Contents

- [exec command](#exec-command)
- [exec flags](#exec-flags)
- [exec output formats](#exec-output-formats)
- [exec examples](#exec-examples)
- [inline pipeline (run -e)](#inline-pipeline-run--e)
- [stdin pipeline (run -)](#stdin-pipeline-run--)
- [choosing exec vs pipeline](#choosing-exec-vs-pipeline)

---

## exec command

Run a single agent with a prompt. No files needed — prompt in, output out.

```
agentflow exec <agent> "<prompt>" [options]
```

**Agents:** `codex`, `claude`, `kimi`, `gemini`, `shell`, `python`

The simplest possible usage:

```bash
agentflow exec gemini "What's trending on GitHub?"
```

This prints the agent's response directly to stdout and exits.

## exec flags

| Flag | Short | Default | Description |
|---|---|---|---|
| `--model` | `-m` | agent default | Model override (e.g., `gemini-3-pro-preview`, `claude-sonnet-4-6`) |
| `--tools` | `-t` | `read_only` | Tool access: `read_only` (safe, no writes) or `read_write` (full access) |
| `--timeout` | | `1800` | Timeout in seconds |
| `--env` | | none | Environment variables as `KEY=value` (repeatable) |
| `--provider` | | none | Provider alias (e.g., `google`, `anthropic`, `openai`) |
| `--extra-arg` | | none | Extra CLI arguments passed to the agent (repeatable) |
| `--output` | | `auto` | Output format: `auto`, `text`, `json`, `json-summary`, `summary` |

### --tools

Controls what the agent can do on your machine:

- **`read_only`** (default): The agent can read files and search the web but cannot modify anything. Use this for questions, analysis, code review, and research.
- **`read_write`**: The agent can read, write, and execute. Use this for tasks that need to create or modify files, run commands, or make changes.

### --env

Pass environment variables to the agent process. Useful for API keys, config, or dynamic values:

```bash
agentflow exec shell 'echo $DB_HOST' --env DB_HOST=localhost --env DB_PORT=5432
```

Values with `=` signs work correctly — only the first `=` is used as the separator:

```bash
agentflow exec shell 'echo $CONN' --env 'CONN=host=db;port=5432'
```

### --model

Override the agent's default model. The available models depend on the agent:

```bash
agentflow exec gemini "Search for X" --model gemini-3-pro-preview
agentflow exec gemini "Quick question" --model gemini-3-flash-preview
agentflow exec claude "Explain this" --model claude-sonnet-4-6
```

## exec output formats

| Format | When to use | What it prints |
|---|---|---|
| `auto` (default) | Most cases | `text` on TTY, `json` when piped |
| `text` | You want just the response | Raw agent response, nothing else |
| `json` | Programmatic consumption | Full `RunRecord` with all metadata |
| `json-summary` | Compact programmatic use | Compact summary with status, duration, preview |
| `summary` | Pipeline-style overview | Human-readable run summary like `agentflow run` |

**Example 1:** Raw text output (great for piping to other tools)
```bash
agentflow exec gemini "Summarize this repo" --output text > summary.txt
```

**Example 2:** JSON output (great for parsing in scripts)
```bash
result=$(agentflow exec codex "List the top 5 bugs" --output json)
echo "$result" | jq '.nodes.exec.final_response'
```

**Example 3:** Auto mode (default)
```bash
# In a terminal: prints raw text
agentflow exec claude "Hello"

# Piped: prints JSON
agentflow exec claude "Hello" | jq .
```

## exec examples

### Quick questions

```bash
agentflow exec gemini "What is the latest version of Node.js?"
agentflow exec claude "Explain the difference between async and await"
```

### Web search (gemini has built-in Google Search)

```bash
agentflow exec gemini "Search the web for the top trending GitHub repos today" --model gemini-3-flash-preview
```

### Code review (read-only, safe)

```bash
agentflow exec claude "Review the code in src/api.py for security issues" --tools read_only
```

### Code modification (read-write, makes changes)

```bash
agentflow exec codex "Fix the failing test in tests/test_auth.py" --tools read_write
```

### Shell and Python (instant, no API calls)

```bash
agentflow exec shell "find . -name '*.py' | wc -l"
agentflow exec python "import json; print(json.dumps({'status': 'ok'}))"
```

### With environment variables

```bash
agentflow exec codex "Deploy to staging" --tools read_write --env DEPLOY_ENV=staging --env DRY_RUN=true
```

---

## Inline pipeline (run -e)

Run a full pipeline without creating a file. Pass JSON or Python directly:

### Inline JSON

```bash
agentflow run -e '{"name":"review","concurrency":2,"nodes":[
  {"id":"scan","agent":"codex","prompt":"List the top 3 files to review"},
  {"id":"review","agent":"claude","prompt":"Review: {{ nodes.scan.output }}","depends_on":["scan"],"tools":"read_only"}
]}'
```

### Inline Python

If the expression doesn't start with `{`, it's treated as Python code. The Python script must print pipeline JSON to stdout (same as `.py` pipeline files):

```bash
agentflow run -e 'from agentflow import Graph, codex, claude, fanout
with Graph("fan-review", concurrency=4) as g:
    scan = codex(task_id="scan", prompt="List 3 files to review")
    reviews = fanout(codex(task_id="review", prompt="Review {{ item.file }}"), [{"file":"api.py"},{"file":"auth.py"}])
    scan >> reviews
print(g.to_json())'
```

### How detection works

- Starts with `{` → parsed as JSON
- Anything else → executed as Python via `python -c`

---

## Stdin pipeline (run -)

Read a pipeline from stdin using `-` as the path:

```bash
# Pipe JSON directly
echo '{"name":"test","nodes":[{"id":"q","agent":"shell","prompt":"echo hello"}]}' | agentflow run -

# Pipe from a Python script
python3 my_pipeline.py | agentflow run -

# Pipe from curl or any other source
curl -s https://example.com/pipeline.json | agentflow run -
```

---

## Choosing exec vs pipeline

| Scenario | Use |
|---|---|
| Single question or task | `agentflow exec` |
| Web search | `agentflow exec gemini` |
| Quick shell/python command | `agentflow exec shell` or `agentflow exec python` |
| Two+ agents that depend on each other | `agentflow run -e` or a pipeline file |
| Parallel fan-out across many items | Pipeline file with `fanout()` |
| Iterative write-review loops | Pipeline file with `on_failure` cycles |
| Recurring or complex workflows | Pipeline `.py` or `.json` file |

**Rule of thumb:** If one agent can do the job, use `exec`. If you need multiple agents to coordinate, use a pipeline.
