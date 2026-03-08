from __future__ import annotations

import pytest

from agentflow.local_shell import kimi_shell_init_requires_interactive_bash_warning, shell_command_uses_kimi_helper


@pytest.mark.parametrize(
    "command",
    [
        "bash -lc 'command -v kimi >/dev/null && {command}'",
        "bash -lc 'type kimi >/dev/null 2>&1; {command}'",
        "bash -lc 'which kimi >/dev/null; {command}'",
        "bash -lc 'builtin type kimi >/dev/null 2>&1; {command}'",
        "echo kimi",
        "printf '%s\\n' kimi",
        "bash -lc 'echo kimi && {command}'",
        "bash -lc 'printf kimi && {command}'",
    ],
)
def test_shell_command_uses_kimi_helper_ignores_probe_commands(command: str):
    assert shell_command_uses_kimi_helper(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "bash -lc 'command -v kimi >/dev/null && kimi && {command}'",
        "bash -lc 'type kimi >/dev/null 2>&1; kimi; {command}'",
        "bash -lc 'which kimi >/dev/null; kimi && {command}'",
    ],
)
def test_shell_command_uses_kimi_helper_detects_actual_bootstrap_after_probe(command: str):
    assert shell_command_uses_kimi_helper(command) is True


def test_kimi_shell_init_requires_interactive_bash_warning_ignores_probe_only_shell():
    target = {
        "kind": "local",
        "shell": "bash -lc 'command -v kimi >/dev/null && {command}'",
    }

    assert kimi_shell_init_requires_interactive_bash_warning(target) is None


def test_kimi_shell_init_requires_interactive_bash_warning_supports_shell_init_lists():
    target = {
        "kind": "local",
        "shell": "bash",
        "shell_init": ["command -v kimi >/dev/null 2>&1", "kimi"],
    }

    assert kimi_shell_init_requires_interactive_bash_warning(target) == (
        "`shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually "
        "unavailable. Set `target.shell_interactive: true` or use `bash -lic`."
    )


def test_kimi_shell_init_requires_interactive_bash_warning_ignores_plain_text_kimi_output():
    target = {
        "kind": "local",
        "shell": "bash",
        "shell_init": "echo kimi",
    }

    assert kimi_shell_init_requires_interactive_bash_warning(target) is None
