# CLI and Operations

Install AgentFlow, scaffold pipelines, inspect runs, manage local readiness checks, and operate the web console.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

After installation, you can invoke the CLI either as `agentflow ...` or `python -m agentflow ...`.
If you are not inside an activated virtualenv, prefer `.venv/bin/python -m agentflow ...` from the repo root or `python3 -m agentflow ...`.

## Quick start

Scaffold a pipeline from the bundled templates:

```bash
agentflow templates
agentflow init > pipeline.yaml
agentflow init repo-sweep.yaml --template codex-fanout-repo-sweep
agentflow init repo-sweep-batched.yaml --template codex-repo-sweep-batched
agentflow init fuzz-matrix.yaml --template codex-fuzz-matrix
agentflow init fuzz-matrix-derived.yaml --template codex-fuzz-matrix-derived
agentflow init fuzz-matrix-curated.yaml --template codex-fuzz-matrix-curated
agentflow init fuzz-matrix-128.yaml --template codex-fuzz-matrix-128
agentflow init fuzz-hierarchical-grouped.yaml --template codex-fuzz-hierarchical-grouped
agentflow init fuzz-hierarchical-grouped-128.yaml --template codex-fuzz-hierarchical-grouped --set bucket_count=8 --set concurrency=32
agentflow init fuzz-hierarchical.yaml --template codex-fuzz-hierarchical-manifest
agentflow init fuzz-hierarchical-128.yaml --template codex-fuzz-hierarchical-manifest --set bucket_count=8 --set concurrency=32
agentflow init fuzz-hierarchical-128.yaml --template codex-fuzz-hierarchical-128
agentflow init fuzz-matrix-manifest.yaml --template codex-fuzz-matrix-manifest
agentflow init fuzz-matrix-manifest-128.yaml --template codex-fuzz-matrix-manifest --set bucket_count=8 --set concurrency=32
agentflow init fuzz-catalog.yaml --template codex-fuzz-catalog
agentflow init fuzz-catalog-grouped.yaml --template codex-fuzz-catalog-grouped
agentflow init fuzz-batched.yaml --template codex-fuzz-batched
agentflow init fuzz-swarm.yaml --template codex-fuzz-swarm
agentflow init fuzz-128.yaml --template codex-fuzz-swarm --set shards=128 --set concurrency=32
agentflow init kimi-smoke.yaml --template local-kimi-smoke
agentflow init kimi-shell-init-smoke.yaml --template local-kimi-shell-init-smoke
agentflow init kimi-shell-wrapper-smoke.yaml --template local-kimi-shell-wrapper-smoke
```

Use `agentflow templates` to list the bundled starters with short descriptions, example source files, and the matching `agentflow init --template ...` command.
The default `pipeline` template is a generic Codex/Claude/Kimi DAG. The `codex-fanout-repo-sweep` template is the smallest maintainer review fanout: one planning node, 8 Codex shards, and one final merge. The `codex-repo-sweep-batched` template is the large-maintainer alternative when repo sweeps should scale cleanly to 64 or 128 shards: it uses `node_defaults`, `agent_defaults`, and `fanout.batches` so the YAML stays compact and the final handoff stays readable. The `codex-fuzz-matrix` template shows `fanout.matrix`, which keeps target families and sanitizer/seed variants declarative instead of hand-enumerating every shard. The `codex-fuzz-matrix-derived` template adds `fanout.derive` on top of that flow so labels and workdirs can be computed once and reused across `target.cwd`, prompts, and reducers. The `codex-fuzz-matrix-curated` template takes the next step and shows how to keep a campaign in matrix form while excluding bad combinations and appending a few bespoke shards. The `codex-fuzz-matrix-128` template is the corresponding full-size 128-shard matrix reference when you want a realistic large heterogeneous campaign to clone. The `codex-fuzz-hierarchical-grouped` template makes the staged-reducer pattern easier to adapt when reducer families should come directly from the shard fanout: it keeps only the shard axes manifest, derives the per-family reducer roster with `fanout.group_by`, and gives each reducer scoped shard ids plus `current.members`. The `codex-fuzz-hierarchical-manifest` template remains the explicit-roster variant when reducer families should stay in their own sidecar manifest. The fixed `codex-fuzz-hierarchical-128` template remains the checked-in 128-shard hierarchical reference. The `codex-fuzz-matrix-manifest` template turns that `fanout.matrix_path` pattern into a configurable scaffold by rendering both the YAML and its sidecar axes manifest, which makes it the default adaptation path when reusable heterogeneous axes should live outside the main pipeline file. The fixed `codex-fuzz-matrix-manifest-128` template remains the checked-in 128-shard manifest reference. The `codex-fuzz-catalog` template does the same for `fanout.values_path`, but with a rendered CSV shard catalog for cases where each shard really needs explicit per-row metadata instead of derivable fields. The `codex-fuzz-catalog-grouped` template layers `fanout.group_by` on top of that CSV-backed path so maintainers can keep explicit per-row shard metadata and still get staged reducers with scoped dependencies. The `codex-fuzz-batched` template is the maintainable adaptation path for large homogeneous Codex swarms: it defaults to 128 shards and inserts `fanout.batches` reducers automatically so the final merge stays readable. The `codex-fuzz-swarm` template remains the simpler single-reducer path for homogeneous campaigns: it renders the checked-in 32-shard starter by default and accepts repeated `--set key=value` overrides such as `shards`, `concurrency`, `name`, and `working_dir` when you want to scale it up or down. The `codex-fuzz-swarm-128` template remains the fixed 128-shard homogeneous reference example. The `local-kimi-smoke` template is the same real-agent local Codex plus Claude-on-Kimi smoke DAG used by the repo's verification scripts, so it is a fast way to bootstrap a known-good local setup into your own workspace. When you want that same local smoke flow with explicit `shell: bash`, `shell_login: true`, `shell_interactive: true`, and `shell_init: kimi` wiring instead of the shorthand `bootstrap: kimi`, use the `local-kimi-shell-init-smoke` template. When you want the same flow with an explicit `target.shell: "bash -lic 'command -v kimi >/dev/null 2>&1 && kimi && {command}'"` wrapper, use the `local-kimi-shell-wrapper-smoke` template. For a compact selection table, see [Examples guide](examples.md).

Right-size a swarm for the host before you launch it:

```bash
agentflow init fuzz-swarm.yaml --template codex-fuzz-swarm
agentflow init fuzz-128.yaml --template codex-fuzz-swarm --set shards=128 --set concurrency=32
agentflow init fuzz-matrix-derived.yaml --template codex-fuzz-matrix-derived
agentflow init fuzz-matrix-curated.yaml --template codex-fuzz-matrix-curated
agentflow init fuzz-matrix-128.yaml --template codex-fuzz-matrix-128
agentflow init fuzz-hierarchical-grouped.yaml --template codex-fuzz-hierarchical-grouped
agentflow init fuzz-hierarchical-grouped-128.yaml --template codex-fuzz-hierarchical-grouped --set bucket_count=8 --set concurrency=32
agentflow init fuzz-hierarchical.yaml --template codex-fuzz-hierarchical-manifest
agentflow init fuzz-hierarchical-128.yaml --template codex-fuzz-hierarchical-manifest --set bucket_count=8 --set concurrency=32
agentflow init fuzz-hierarchical-128.yaml --template codex-fuzz-hierarchical-128
agentflow init fuzz-matrix-manifest.yaml --template codex-fuzz-matrix-manifest
agentflow init fuzz-matrix-manifest-128.yaml --template codex-fuzz-matrix-manifest --set bucket_count=8 --set concurrency=32
agentflow init fuzz-catalog.yaml --template codex-fuzz-catalog
agentflow init fuzz-catalog-grouped.yaml --template codex-fuzz-catalog-grouped
agentflow init fuzz-batched.yaml --template codex-fuzz-batched
agentflow inspect fuzz-128.yaml --output summary
```

Validate a pipeline:

```bash
agentflow validate examples/pipeline.yaml
agentflow validate examples/codex-fanout-repo-sweep.yaml
agentflow validate examples/codex-repo-sweep-batched.yaml
```

Inspect the resolved launch plan before running it:

```bash
agentflow inspect examples/local-real-agents-kimi-smoke.yaml
agentflow inspect examples/local-real-agents-kimi-smoke.yaml --output json-summary
agentflow inspect examples/pipeline.yaml --node review --output json
agentflow inspect examples/codex-repo-sweep-batched.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-matrix.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-matrix-derived.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-matrix-curated.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-matrix-128.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-hierarchical-grouped.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-hierarchical-manifest.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-hierarchical-128.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-matrix-manifest.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-matrix-manifest-128.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-catalog.yaml --output summary
agentflow inspect examples/fuzz/codex-fuzz-catalog-grouped.yaml --output summary
agentflow inspect examples/fuzz/fuzz_codex_32.yaml --output summary
agentflow inspect examples/fuzz/fuzz_codex_128.yaml --output summary
```

The default summary view now includes resolved per-node model, tools, capture, skills, MCP server names, provider details, and auth source hints when they are set, which makes it easier to verify mixed Codex, Claude, and Kimi launch configs before you execute a run.
On an interactive terminal, `agentflow inspect` now defaults to that human-readable summary; when stdout is redirected or piped, it falls back to the full JSON inspection payload so shell wrappers can parse launch details without adding `--output json`.
Those auth hints call out whether a node will rely on `node.env`, `provider.env`, the current environment, local shell bootstrap such as `target.bootstrap: kimi` or `target.shell_init: kimi`, or Codex CLI login fallback, so it is easier to spot hidden local prerequisites before launch.
When a local Claude node relies on Kimi's Anthropic-compatible bootstrap, the auth hint now keeps the Kimi helper first even if `ANTHROPIC_API_KEY` is already present in `node.env`, `provider.env`, or the current environment, because that helper runs last in the prepared shell and becomes the effective launch source.
Those same `inspect` and `doctor` auth probes also honor launch-only shell inputs from `node.env` and `provider.env` while checking login or interactive Bash startup, so env-gated bridges such as `~/.profile` sourcing a file named by `AGENTFLOW_KIMI_ENV_FILE` are recognized before launch instead of being reported as missing credentials.
They also recognize non-interactive Bash wrappers that preload credentials through `BASH_ENV`, such as `target.shell: env BASH_ENV=$HOME/.anthropic.env bash -c '{command}'`, as long as that file really exports the provider key.
They also follow exported variables that are set earlier in the wrapper before Bash starts, such as `target.shell: export OPENAI_API_KEY=... && bash -lc '{command}'`, `target.shell: export BASH_ENV=$HOME/.anthropic.env && bash -c '{command}'`, or `target.shell: export HOME=/tmp/demo-home && bash -lic '{command}'`.
For local Codex nodes that run through a `kimi` shell bootstrap, the same auth summary now also calls out that bootstrap even when `OPENAI_API_KEY` already comes from the environment, and it names that bootstrap as the Codex CLI login path when no key is injected.
When a resolved provider also needs to override conflicting shell values such as `ANTHROPIC_BASE_URL`, `inspect` now reports that expected launch pinning as a `Note:` and names whether that override came from `node.env`, `provider.env`, or provider-derived settings such as `provider.base_url`, while keeping real `Warning:` lines for inherited ambient routing or broken shell startup.
For local nodes, it also surfaces shell bootstrap details such as `bootstrap`, `shell`, login and interactive flags, the active bash login startup file (`~/.bash_profile`, `~/.bash_login`, or `~/.profile`), and `shell_init`, so Kimi-backed wrappers are easier to confirm without decoding the full launch command. Those login and interactive flags are inferred from direct shell wrappers such as `bash -lic` too, not only from structured `shell_login` and `shell_interactive` fields. The summary and `json-summary` forms now also include the bash login file presence matrix (`~/.bash_profile`, `~/.bash_login`, `~/.profile`) for the effective bootstrap home, so it is easier to confirm whether a node is really falling back through `~/.profile` or shadowing it with another login file. When a bash login wrapper is configured but no user startup file exists, `inspect` now shows `startup=none` and emits a warning so missing `~/.bash_profile` / `~/.profile` bridges are easier to spot before launch. When that login-shell startup is broken, `inspect` also includes the ready-to-paste shell bridge suggestion inline in summary output and as `shell_bridge` in JSON outputs, so you do not need to switch to `doctor` just to grab the fix even when auth already comes from the environment and the real risk is missing PATH or other bash startup exports. Inline secret assignments in `shell_init` or shell wrappers are redacted in both `inspect` output and persisted `launch.json` artifacts. Those auth hints also recognize sourced shell files such as `target.shell_init: source ~/.anthropic.env` or `target.shell: bash -lc 'source ~/.anthropic.env && {command}'` when that file exports the provider key.
That same warning and shell-bridge hint now also appear when a local login-shell node still expects provider auth from bash startup files and the active login file shadows the real `~/.bashrc` bridge, which makes plain Claude-on-Anthropic or local Kimi auth bootstraps easier to debug before launch.
When a local bash target overrides `HOME`, the inspect summary now also shows `Bootstrap home: ...` so the `startup=~/.profile -> ~/.bashrc` chain is grounded to the actual dotfiles directory AgentFlow will use at launch instead of your current shell home.
It also shows whether `agentflow run` or `agentflow smoke` will trigger the local doctor preflight automatically in the default `auto` mode, which helps you confirm bundled-smoke and Kimi-bootstrap detection before you launch anything.
When that auto preflight is enabled because of a local Kimi bootstrap, the inspect output now also names the matching nodes and whether the trigger came from `target.bootstrap`, `target.shell_init`, or `target.shell`, so it is easier to trust why the guard rail will run.
Use `--output json-summary` when you want the same compact information in a machine-readable format without the full prepared env and payload details from `--output json`.
For Kimi nodes, that inspect output also surfaces the effective default Moonshot provider even when you omit `provider:` from the pipeline, so the expected `KIMI_API_KEY` and base URL are visible before launch.
When a node launch will override current shell values such as `ANTHROPIC_BASE_URL` or `OPENAI_API_KEY`, that JSON summary also includes a structured `launch_env_overrides` list per node, including the override source, so wrappers can react without scraping warning text. Base-URL values are included verbatim; secret-like keys stay redacted.
That override summary also treats explicit empty-string base-URL values as intentional clears, so bundled smoke configs can show when they wipe an ambient relay such as `OPENAI_BASE_URL` before launch.
For local Claude-on-Kimi nodes in mixed-provider shells, that same summary now also includes `bootstrap_env_overrides` when the `kimi` helper will replace the current `ANTHROPIC_API_KEY` after the node switches away from another Anthropic-compatible base URL, so hidden auth shadowing is easier to spot before execution.
For local Codex or Claude nodes that still do not pin base-url routing explicitly, the same summary now also includes `launch_env_inheritances` when the launch will inherit an ambient `OPENAI_BASE_URL` or `ANTHROPIC_BASE_URL` from the current shell, even if you supplied a structured provider that sets auth but omits `base_url`, so hidden routing drift is visible before you run the DAG.

Run a pipeline once:

```bash
agentflow run examples/pipeline.yaml
```

On an interactive terminal, `agentflow run` now defaults to the same compact per-node summary that `smoke` uses; when stdout is redirected or piped, it still defaults to the full JSON run record so scripts do not break. You can always force either shape with `--output summary`, `--output json-summary`, or `--output json`.
When a node fails with an upstream provider response such as `API Error: 402 ...`, those summary views now add a `Diagnosis:` line for common provider-side rejection patterns, so membership, billing, quota, or other upstream account-state failures are easier to distinguish from a broken AgentFlow launch path.
When you use `agentflow run` with the bundled real-agent smoke file, an explicit reference to that bundled file, or a custom local pipeline that either bootstraps local Codex/Claude/Kimi nodes through `kimi`, runs local `kimi` nodes, or routes local Claude nodes through Kimi-compatible provider settings, AgentFlow now runs the same local preflight as `agentflow smoke` by default. Use `--preflight never` when you intentionally want to bypass those readiness checks.
Add `--show-preflight` when you want `run` to print the successful local preflight summary before execution starts. That summary is written to stderr so `--output json` and `--output json-summary` remain machine-readable on stdout, and it now also explains why auto preflight ran plus the matching node bootstrap sources when available.

Inspect how a pipeline will resolve prompts, shell bootstrap, and launch commands without executing any agents:

```bash
agentflow inspect examples/local-real-agents-kimi-smoke.yaml
```

Like `run` and `doctor`, `inspect` now auto-selects a terminal-friendly summary on TTY stdout and full JSON when stdout is redirected. Use `--output summary`, `--output json-summary`, or `--output json` when you want to force a specific shape. When a local node's bash login startup is broken, `inspect` now prints the same ready-to-paste shell bridge snippet that `doctor` recommends, and the JSON forms expose it as a per-node `shell_bridge` object.

Run the bundled real-agent smoke check:

```bash
agentflow smoke
```

Run the same bundled local flow as a single readiness + execution check:

```bash
agentflow check-local
```

The bundled smoke now launches both `codex` and `claude` in parallel inside `bash -lic` so the default check covers scheduler fan-out as well as login-shell startup files for local CLI installs. The example also uses pipeline-level `local_target_defaults.bootstrap: kimi`, which keeps the default smoke aligned with shared Kimi bootstrap setups without repeating the same target block on every node.
The bundled Codex smoke node also clears any ambient `OPENAI_BASE_URL`, so a host-level relay or proxy does not silently hijack the default local smoke run.

`agentflow check-local` prints the Doctor report to stderr first and then launches the bundled smoke pipeline only when the local setup is ready enough to continue. Like `run`, it now defaults to the compact summary on a terminal and falls back to JSON when stdout is redirected or piped, so wrapper scripts can capture the run result without adding `--output json`. The repo-local `make check-local` shortcut uses that same single-pass CLI flow with `--output summary`, so maintainer runs stay compact even in non-TTY logs and you still do not pay for the Doctor preflight twice before the smoke run starts. `check-local` also accepts an optional custom pipeline path when you want the same doctor-then-run flow for another local Kimi-backed smoke DAG, and it now reuses that same validated pipeline snapshot for the launch step instead of reloading the file after Doctor succeeds. For wrapper parity with `run` and `smoke`, it also accepts `--show-preflight` plus `--preflight auto` or `--preflight always` without changing behavior; `check-local` still always runs Doctor first, so `--preflight never` remains invalid on purpose. When that custom path does not match the bundled smoke example and does not rely on a local `kimi` shell bootstrap, a local `kimi` node, or Kimi-compatible Claude provider routing, the Doctor step now skips the bundled smoke-only Kimi helper checks and sticks to the local agent checks that pipeline actually needs. When you pass `--output json` or `--output json-summary`, that preflight stderr payload is JSON so wrappers can parse readiness and run output separately.
For custom pipelines that still use a local `kimi` bootstrap, that preflight now keeps the lightweight `kimi_shell_helper` check but drops bundled smoke-only assumptions about default `codex` and `claude` commands, so custom executables and single-agent DAGs are not blocked by unrelated smoke prerequisites.
When those custom Kimi-backed preflights succeed, `doctor` and `check-local` also print the matching per-node local readiness probes such as `claude --version`, `codex --version`, and Codex auth readiness, so you can see that AgentFlow actually exercised the prepared shell bootstrap before the run starts. That `codex_auth` line now reports the effective source it found in the prepared shell, so it may name `OPENAI_API_KEY`, `codex login status`, or both when the prepared shell has an API key and a working Codex CLI login.

When you want to exercise that same bundled local Codex + Claude-on-Kimi pipeline through the main `run` command instead of `smoke` or `check-local`, run:

```bash
make run-local
```

That shortcut uses `agentflow run examples/local-real-agents-kimi-smoke.yaml --output summary`, so it keeps the bundled maintainer path human-readable while still going through the non-smoke CLI surface that external wrappers and higher-level tooling often call.
Use `make inspect-local-shell-init`, `make doctor-local-shell-init`, `make smoke-local-shell-init`, and `make run-local-shell-init` when you want to exercise the checked-in bundled variant that wires Kimi through explicit `shell: bash`, `shell_login: true`, `shell_interactive: true`, and `shell_init: kimi`.
Use `make inspect-local-shell-wrapper`, `make doctor-local-shell-wrapper`, `make smoke-local-shell-wrapper`, and `make run-local-shell-wrapper` when you want to exercise the checked-in bundled variant that wires Kimi through the explicit `target.shell: "bash -lic 'command -v kimi >/dev/null 2>&1 && kimi && {command}'"` wrapper.

To verify the broader "real project in another directory" path with the same local Codex + Claude-on-Kimi bootstrap, run:

```bash
make check-local-custom
```

That helper writes the shared temporary local Codex + Claude-on-Kimi pipeline outside this repo and runs `agentflow check-local` against it, which makes it easier to catch regressions in custom-pipeline path resolution and shared Kimi bootstrap handling before they reach users.

When you want to verify the standalone local readiness path for that same kind of external Codex + Claude-on-Kimi pipeline, run:

```bash
make doctor-local-custom
```

That helper writes the same temporary pipeline outside this repo, runs `agentflow doctor --output summary`, and validates that the external pipeline still reports the expected bash startup, Kimi helper, Codex readiness/auth, and launch-env override details before any run starts.
Use `make doctor-local-custom-shell-init` when you want the same external verification against the explicit `shell: bash`, `shell_login: true`, `shell_interactive: true`, `shell_init: kimi` form instead of `local_target_defaults.bootstrap: kimi`.
Use `make doctor-local-custom-shell-wrapper` when you want the same external verification against the explicit `target.shell: "bash -lic 'command -v kimi >/dev/null 2>&1 && kimi && {command}'"` wrapper form.

When you want to verify the pre-launch inspection path for that same kind of external Codex + Claude-on-Kimi pipeline, run:

```bash
make inspect-local-custom
```

That helper writes the same temporary pipeline outside this repo, runs `agentflow inspect --output summary`, and validates that the reported working directory, per-node `Cwd`, Kimi bootstrap wiring, and prepared Codex/Claude launch summaries all resolve against the external pipeline path.
Use `make inspect-local-custom-shell-init` when you want the same contract verified for the explicit `shell_init: kimi` wiring instead of the preset bootstrap shorthand.
Use `make inspect-local-custom-shell-wrapper` when you want the same contract verified for the explicit `target.shell` Kimi wrapper.

When you want to exercise the standalone `agentflow smoke` path against that same kind of external Codex + Claude-on-Kimi pipeline, run:

```bash
make smoke-local-custom
```

That helper writes the same temporary pipeline outside this repo, runs `agentflow smoke --output json-summary --show-preflight`, and validates the wrapper contract: smoke JSON stays on stdout, the successful preflight summary stays on stderr, and both local agent nodes complete with the expected previews. If the live run fails, it prints the captured stdout/stderr and keeps the temp directory path for debugging.
Use `make smoke-local-custom-shell-init` when you want the same stdout/stderr contract exercised for the explicit `shell_init: kimi` form too.
Use `make smoke-local-custom-shell-wrapper` when you want the same stdout/stderr contract exercised for the explicit `target.shell` Kimi wrapper too.

When you want to exercise the main `agentflow run` path against that same kind of external Codex + Claude-on-Kimi pipeline, run:

```bash
make run-local-custom
```

That helper also writes the same temporary pipeline outside this repo, but it runs `agentflow run --output json-summary --show-preflight` and validates the wrapper contract: run JSON stays on stdout, the successful preflight summary stays on stderr, and both local agent nodes complete with the expected previews. If the live run fails, it now prints the captured stdout/stderr and keeps the temp directory path for debugging.
Use `make run-local-custom-shell-init` when you want the same stdout/stderr contract exercised for the explicit `shell_init: kimi` form too.
Use `make run-local-custom-shell-wrapper` when you want the same stdout/stderr contract exercised for the explicit `target.shell` Kimi wrapper too.

When you want the full maintainer smoke sequence in one command, run:

```bash
make verify-local
```

That wrapper now runs both the raw shell-level Kimi toolchain check and the bundled `agentflow toolchain-local` / `check-local` commands, then runs `make probe-codex-local` plus `make probe-claude-local` to exercise minimal live Codex and Claude requests before it executes the checked-in bundled `bootstrap: kimi`, explicit `shell_init: kimi`, and explicit `target.shell` Kimi smoke examples through `inspect`, `doctor`, `smoke`, and `run`. Only after those bundled paths pass does it move on to the external custom-pipeline `doctor`, `inspect`, `smoke`, `check-local`, and `run` paths for those same three bootstrap shapes. The bundled `smoke` and `run` legs plus the external custom `smoke` / `run` legs specifically validate the stdout/stderr wrapper contract for all three shipped smoke bootstrap shapes, so regressions in `agentflow smoke` or `agentflow run` cannot hide behind a still-green generated custom pipeline or the baseline bundled file alone. It also prints whether `~/.bash_profile`, `~/.bash_login`, or `~/.profile` is supplying the bash login startup path before it verifies `kimi`, `codex`, and `claude`, reports whether Codex auth is coming from `OPENAI_API_KEY`, Codex CLI login, or both in that shared Kimi-backed shell, and shows the resolved `codex` / `claude` executable paths so it is easier to catch mixed Node/npm installs. When one of the live probes fails with a raw API error, the script now also prints a `Diagnosis:` line that tells you whether the CLI reached the upstream provider and was rejected there, which helps separate membership, billing, quota, or other provider-side failures from a broken local bash bootstrap.
Those maintainer scripts now apply the same timeout guard to every live `agentflow` invocation in the stack, including the bundled `toolchain-local`, `check-local`, and every bundled-example `inspect`, `doctor`, `smoke`, and `run` step plus the external `doctor`, `inspect`, `smoke`, `check-local`, and `run` paths. Override that stack-wide budget with `AGENTFLOW_LOCAL_VERIFY_TIMEOUT_SECONDS`; when it is unset, the helper falls back to `AGENTFLOW_DOCTOR_TIMEOUT_SECONDS` if you already exported it, otherwise it uses a 60-second default that is large enough for the real local Codex plus Claude-on-Kimi runs.
When you need the whole picture instead of the first failing step, run `AGENTFLOW_LOCAL_VERIFY_KEEP_GOING=1 make verify-local`. That mode still exits non-zero when any step fails, but it keeps going, prints a final failure summary, and labels provider-side API rejections separately from generic local failures so upstream account-state issues do not hide later orchestration regressions.

When you want that live Codex check by itself, run:

```bash
make probe-codex-local
```

That shortcut keeps the same `bash -lic` plus `kimi` bootstrap as the bundled smoke, clears any inherited `OPENAI_BASE_URL` to match the bundled Codex smoke node, and sends one minimal `codex exec` request so raw CLI or provider failures surface before you burn time on a larger smoke run. When Codex prints an `API Error: ...` response, the helper now adds a `Diagnosis:` line so you can tell whether the request reached the provider and failed upstream versus never making it past the local shell bootstrap.

When you want that live Claude-on-Kimi check by itself, run:

```bash
make probe-claude-local
```

That shortcut keeps the same `bash -lic` plus `kimi` bootstrap as the bundled smoke, but it sends a single minimal `claude -p` request with tools disabled so provider-side failures such as Kimi membership or billing errors surface immediately and with the raw API error text before you burn time on a larger smoke run. Those failures now also include the same `Diagnosis:` line, and membership-style `402` responses are called out explicitly as upstream account-state problems rather than local bootstrap regressions.

By default, `agentflow smoke` now prints a compact per-node summary instead of the full run record JSON. Use `agentflow smoke --output json-summary` when you want a compact machine-readable payload for scripts, or `agentflow smoke --output json` when you want the complete persisted run record with stdout, stderr, and trace details.
Those summary forms also add the same provider-side `Diagnosis:` hint when a node surfaces a recognizable upstream API rejection, so live smoke failures can call out account-state issues without switching to the full JSON or raw artifacts first.
Add `--show-preflight` when you want `smoke` to print the successful local readiness summary before the run starts. AgentFlow writes that extra summary to stderr so JSON stdout stays safe for wrappers and scripts, and it now includes the auto-preflight reason plus matched node bootstrap sources when available.

Manage persisted runs from the CLI without opening the web UI:

```bash
agentflow runs
agentflow show <run-id>
agentflow cancel <run-id>
agentflow rerun <run-id>
```

`agentflow runs` prints a compact summary of the most recent persisted runs under `--runs-dir` and defaults to the newest 20 entries so busy workspaces stay readable; pass `--limit 0` to show everything. Like `run`, `show`, `cancel`, and `rerun` now default to that human summary on a terminal and switch to full JSON automatically when stdout is redirected or piped, so wrappers can inspect persisted runs without remembering `--output json`. `show` still supports `--output json-summary` for the compact machine-readable form. `cancel` marks an active run as cancelling or cancels it immediately when it is still queued, and `rerun` re-executes the stored pipeline spec and waits for the fresh Codex/Claude/Kimi run to finish so the command behaves like a terminal-friendly replay.

The bundled smoke preflight now matches that output mode too, so warning and failure reports stay in summary form by default and switch to JSON when you pass `--output json`.
When those preflight checks detect a bash login startup bridge problem, the same smoke or run command now includes the ready-to-paste shell bridge recommendation inline instead of making you rerun Doctor separately.
When that preflight loads the bundled smoke pipeline, it now also applies the same per-node shell-bootstrap and local agent readiness checks that custom Kimi-backed pipelines already use, so edits to the bundled example are still validated before launch.
In the default `auto` mode, AgentFlow runs that preflight for the bundled smoke path, for explicit references to the bundled smoke file, and for custom local smoke pipelines that either bootstrap local Codex/Claude/Kimi nodes through `kimi`, run local `kimi` nodes, or route local Claude nodes through Kimi-compatible provider settings. `agentflow run` now uses the same guard rails for those same pipeline shapes. Use `--preflight always` to force preflight for other custom smoke pipelines too; AgentFlow still keeps those checks pipeline-specific instead of falling back to bundled smoke-only prerequisites. Use `--preflight never` when you need to skip preflight.

Check the local Codex/Claude/Kimi smoke prerequisites without launching a run:

```bash
agentflow doctor
```

`agentflow doctor` now defaults to `summary` when stdout is a terminal, which makes local readiness checks easier to scan. It still defaults to JSON when stdout is redirected or piped so CI and wrapper scripts can parse it directly, and you can always force either mode with `--output summary` or `--output json`. `doctor` also accepts `--output json-summary`, which emits the compact machine-readable view with per-status counts and concise checks while leaving the full per-check `context` payload on `--output json`. That keeps one summary-shaped flag working consistently across `inspect`, `doctor`, `run`, `smoke`, and `check-local`. When you pass a custom pipeline path that is not the bundled smoke example and does not use a local `kimi` shell bootstrap, `doctor` now skips the bundled smoke-only helper checks so Codex-only or Claude-only local DAGs are not blocked by unrelated Kimi prerequisites.
Plain `agentflow doctor` now also loads the bundled smoke pipeline and applies those same node-level shell/bootstrap checks, not just the shared host-level helper checks, so the default smoke example stays self-validating. It also reports the bundled run/smoke auto-preflight reason and matched nodes by default, so the no-argument maintainer path explains the same guard rail that `check-local`, `run`, and `smoke --show-preflight` already use.
Those JSON checks also include per-check `context` when AgentFlow can explain a machine-readable cause, such as the node id and resolved values behind a `launch_env_override` report.
For bash login startup checks, that same JSON context now includes the active login file, `startup_chain`, `startup_summary`, the bash login file presence matrix, and any `shadowed_startup_chain`, while the human summary appends the same startup chain inline as `startup=~/.profile -> ~/.bashrc` plus the current `~/.bash_profile` / `~/.bash_login` / `~/.profile` presence summary so `doctor`, `check-local`, and `make toolchain-local` stay easier to compare.
For the bundled smoke pipeline and custom local Kimi-bootstrapped Codex/Claude/Kimi DAGs, Doctor now also reports when the current shell exports conflicting launch values such as `ANTHROPIC_BASE_URL` that a node will replace at launch, and it carries the same source hint into the human summary and JSON context. Overrides that come directly from explicit pipeline configuration such as `node.env`, `provider.env`, or `provider.base_url` stay informational in Doctor so the preflight does not go yellow for an intentional mixed-provider setup.
In that same mixed-provider Claude-on-Kimi case, Doctor also emits a `bootstrap_env_override` info check when the local `kimi` helper will replace the current `ANTHROPIC_API_KEY` after the node switches to Kimi's Anthropic-compatible base URL.
When `node.env` or `provider.env` explicitly clears a required provider key such as `ANTHROPIC_API_KEY`, Doctor now treats that as a real preflight failure unless a later local bootstrap replaces it, which makes launch-time credential shadowing visible before the DAG starts.

You can also point Doctor at a custom pipeline to surface the same pipeline-specific local shell bootstrap warnings that `run` and `smoke` preflight use:

```bash
agentflow doctor path/to/pipeline.yaml --output summary
```

That keeps it easy to validate real Codex/Claude/Kimi DAGs before you launch them, especially when a node depends on a local `kimi` shell helper, the local Kimi bridge, or Kimi-compatible Claude routing. When you pass a pipeline path, Doctor now also reports whether `run` or `smoke` would auto-run the local preflight for that DAG and names the matching nodes and trigger sources, so you can confirm the guard rail from the same command. It also fails early when a pipeline node requires a provider API key such as `KIMI_API_KEY` or `ANTHROPIC_API_KEY` and that key is missing from the current environment, `node.env`, and `provider.env`, except when a local Claude-on-Kimi node already bootstraps those Anthropic credentials through `target.shell_init: kimi`, an equivalent `target.shell` wrapper, or an interactive/login Bash startup file reached by that wrapper. The same local-bootstrap shortcut applies to custom Claude provider objects when they still point at Kimi's Anthropic-compatible endpoint and use `ANTHROPIC_API_KEY`. For local Kimi nodes, Doctor now also probes the configured Python executable through the prepared local shell with `python -c 'import agentflow.remote.kimi_bridge'`, so broken wrappers and bad interpreter overrides fail before launch. That probe now uses the same default interpreter selection as a real Kimi run, including the repo-local `.venv/bin/python` fallback when the current Python is outside that virtualenv. For local Claude nodes that rely on the Kimi provider or a `kimi` shell bootstrap, Doctor now also runs `claude --version` through the node's prepared local shell bootstrap, so broken bash wrappers fail before launch instead of halfway through a run. For local Codex nodes, Doctor now also runs `codex --version` through the node's prepared local shell bootstrap, respecting `executable:` overrides so broken wrappers fail before launch, and it still checks `codex login status` through that same prepared shell whenever `OPENAI_API_KEY` is absent from the current environment, `node.env`, and `provider.env`. When a local Codex node already warns or fails because its `kimi` shell bootstrap is misconfigured, Doctor now reports that bootstrap issue once instead of stacking a second `codex_auth` failure on top. Direct local bootstrap exports such as `target.shell_init: export ANTHROPIC_API_KEY=...`, split assignment/export sequences like `ANTHROPIC_API_KEY=...` then `export ANTHROPIC_API_KEY`, sourced bootstrap files such as `source ~/.anthropic.env`, interactive Bash startup files reached through `target.shell`, and shell wrappers like `bash -lc 'export ANTHROPIC_API_KEY=... && {command}'` or `bash -lc 'source ~/.anthropic.env && {command}'` also satisfy that preflight, so you do not need to duplicate the same secret in YAML env blocks just to make Doctor happy.
Those local probes use a bounded timeout so broken shell wrappers or hung CLIs fail fast instead of hanging `doctor`, `check-local`, or auto-preflight forever. Override the default 15-second budget with `AGENTFLOW_DOCTOR_TIMEOUT_SECONDS` when a slower local bootstrap genuinely needs more time. The lighter-weight bash startup env probe that Doctor and `inspect` use to confirm credentials sourced from login or interactive Bash startup files also fails fast now; if that specific probe times out, Doctor reports it as a warning instead of a false missing-credentials failure, and you can raise its default 5-second budget with `AGENTFLOW_BASH_STARTUP_PROBE_TIMEOUT_SECONDS`.
Doctor also warns when a local Codex or Claude node would inherit an ambient `OPENAI_BASE_URL` or `ANTHROPIC_BASE_URL` from the current shell because the pipeline still does not pin that routing explicitly, including structured provider configs that set auth but omit `base_url`. That keeps shell-specific relay settings visible in preflight output instead of silently affecting the run.
Add `--shell-bridge` when you want Doctor to include a ready-to-paste login-shell bridge snippet for `~/.bash_profile`, `~/.bash_login`, or `~/.profile`.
Those Doctor and smoke/run preflight shell-bridge hints now also honor node-specific bash homes such as `target.shell: env HOME=/tmp/demo-home bash ...`, so the suggested bridge follows the login files that node will actually use instead of your current shell home.

Run the web console:

```bash
agentflow serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

When you validate or run inline YAML from the web console, the "Base dir for relative paths" field controls how relative `working_dir` and local `target.cwd` values resolve. It defaults to the server process working directory. API clients can send that same hint as `base_dir`, or skip inline YAML entirely and send `pipeline_path` to reuse the CLI's file-based path resolution.

## Runtime configuration

You can configure the runtime via CLI flags or environment variables.

- `AGENTFLOW_RUNS_DIR`: base directory for run state and artifacts
- `AGENTFLOW_MAX_CONCURRENT_RUNS`: maximum number of concurrently executing DAG runs

`AGENTFLOW_RUNS_DIR` and `--runs-dir` accept home-relative paths such as `~/.agentflow/runs`.

CLI equivalents:

```bash
agentflow serve --runs-dir .agentflow/runs --max-concurrent-runs 2
agentflow run examples/pipeline.yaml --runs-dir .agentflow/runs --max-concurrent-runs 2
agentflow run examples/pipeline.yaml --output summary
agentflow run examples/pipeline.yaml --output json-summary
```


## Web console

The frontend shows:

- current DAG state and node statuses
- live run timeline and parsed JSONL trace events
- per-node attempts and retry history
- final outputs plus launch, stdout, stderr, trace, and result artifacts
- controls to validate, launch, cancel, and rerun pipelines

Artifact files are persisted under `AGENTFLOW_RUNS_DIR/<run_id>/artifacts/<node_id>/`.
Each node now includes a redacted `launch.json` artifact that records the resolved command, working directory, selected runtime files, and any remote-runner payload metadata without storing secret env values, inline shell bootstrap assignments, or runtime file contents.
When a node retries, AgentFlow also keeps `launch-attempt-<n>.json` snapshots for each attempt so you can compare bootstrap, command, and payload changes without losing the final `launch.json` view that the web console and API already use.
For structured agent streams such as Codex and Claude JSON output, `stdout.log` keeps the full raw stream while `result.json` and downstream node context omit parser-ignored control chatter such as startup hooks and suppressed warnings.

## API surface

The FastAPI app exposes:

- `POST /api/runs/validate`
- `POST /api/runs`
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`
- `GET /api/runs/{run_id}/stream`
- `GET /api/runs/{run_id}/artifacts/{node_id}/{name}`
- `POST /api/runs/{run_id}/cancel`
- `POST /api/runs/{run_id}/rerun`
- `GET /api/health`
