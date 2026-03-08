from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any


_BASH_SUPPORTED_LONG_FLAGS = {
    "--debug",
    "--debugger",
    "--dump-po-strings",
    "--dump-strings",
    "--help",
    "--login",
    "--noediting",
    "--noprofile",
    "--norc",
    "--posix",
    "--pretty-print",
    "--restricted",
    "--verbose",
    "--version",
}
_BASH_LONG_FLAGS_WITH_VALUE = {"--init-file", "--rcfile"}
_BASH_UNSUPPORTED_LONG_FLAG_DETAILS = {
    "--command": "Bash does not support `--command`; use `-c` or omit it and let AgentFlow add `-c`.",
    "--interactive": "Bash does not support `--interactive`; use `-i` or set `target.shell_interactive: true`.",
}
_COMMAND_POSITION_PREFIX_TOKENS = {"builtin", "command", "env", "nohup", "sudo", "time"}
_ENV_ASSIGNMENT_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_CONTROL_TOKENS = {"&&", "||", "|", ";", "do", "then", "elif"}
_KIMI_SUBSTITUTION_CONSUMERS = {".", "eval", "source"}
_BASHRC_SOURCE_COMMANDS = {".", "source"}
_COMMAND_SUBSTITUTION_PATTERN = re.compile(r"(?:\$|<)\(([^()]*)\)")
_BACKTICK_COMMAND_SUBSTITUTION_PATTERN = re.compile(r"(?<!\\)`([^`]*)`")
_HOME_REFERENCE_PATTERN = re.compile(r"\$(?:\{HOME\}|HOME)")
_BASHRC_NONINTERACTIVE_GUARDS = (
    re.compile(r"case\s+\$-\s+in(?s:.*?)\*\)\s*return\s*;;"),
    re.compile(r"\[\[\s*\$-\s*!=\s*\*i\*\s*\]\]\s*&&\s*return"),
    re.compile(r"\[\s*-z\s+['\"]?\$PS1['\"]?\s*\]\s*&&\s*return"),
)
_EXPORT_STYLE_COMMANDS = {"declare", "typeset"}


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, dict):
        return target.get(key)
    return getattr(target, key, None)


def _split_shell_parts(command: str | None) -> list[str]:
    if not command:
        return []
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _is_command_flag(part: str) -> bool:
    return part == "--command" or (part.startswith("-") and not part.startswith("--") and "c" in part[1:])


def _looks_like_env_assignment(token: str) -> bool:
    return bool(_ENV_ASSIGNMENT_PATTERN.match(token))


def _token_resets_command_position(token: str) -> bool:
    stripped = token.strip()
    if stripped in _SHELL_CONTROL_TOKENS:
        return True
    return stripped.endswith((";", "&&", "||", "|"))


def shell_init_commands(shell_init: Any) -> tuple[str, ...]:
    if isinstance(shell_init, str):
        normalized = shell_init.strip()
        return (normalized,) if normalized else ()
    if isinstance(shell_init, (list, tuple)):
        return tuple(command.strip() for command in shell_init if isinstance(command, str) and command.strip())
    return ()


def render_shell_init(shell_init: Any) -> str | None:
    commands = shell_init_commands(shell_init)
    if not commands:
        return None
    return " && ".join(commands)


def shell_wrapper_requires_command_placeholder(shell: str | None) -> bool:
    if not isinstance(shell, str) or not shell.strip() or "{command}" in shell:
        return False

    parts = _split_shell_parts(shell)
    if not parts:
        return False

    for index, part in enumerate(parts[1:], start=1):
        if _is_command_flag(part):
            return index + 1 < len(parts)
    return False


def shell_init_uses_kimi_helper(shell_init: Any) -> bool:
    return any(shell_command_uses_kimi_helper(command) for command in shell_init_commands(shell_init))


def _looks_like_kimi_token(token: str) -> bool:
    stripped = _normalize_shell_token(token)
    if not stripped:
        return False
    return os.path.basename(stripped) == "kimi"


def _normalize_shell_token(token: str) -> str:
    return token.strip().lstrip("({[").rstrip(";|&)}]\n\r\t ")


def _looks_like_bashrc_path(token: str) -> bool:
    stripped = _normalize_shell_token(token)
    if not stripped:
        return False
    if stripped in {"~/.bashrc", "$HOME/.bashrc", "${HOME}/.bashrc"}:
        return True
    return os.path.basename(stripped) == ".bashrc"


def _env_assignment_name(token: str) -> str | None:
    normalized = _normalize_shell_token(token)
    if not _looks_like_env_assignment(normalized):
        return None
    return normalized.split("=", 1)[0]


def _shell_command_exports_env_var_before_target(command: str | None, env_var: str, target: str) -> bool:
    if not isinstance(command, str) or not command.strip() or not env_var or not target:
        return False

    tokens = _split_shell_parts(command)
    expects_command = True
    prefix_allows_options = False
    active_command: str | None = None
    declare_exports = False
    assigned_in_shell = False
    pending_assignment = False
    exported = False

    for index, token in enumerate(tokens):
        if index > 0 and _is_command_flag(tokens[index - 1]):
            if _shell_command_exports_env_var_before_target(token, env_var, target):
                return True

        normalized = _normalize_shell_token(token)
        if _token_resets_command_position(token):
            if expects_command and pending_assignment:
                assigned_in_shell = True
                pending_assignment = False
            expects_command = True
            prefix_allows_options = False
            active_command = None
            declare_exports = False
            continue

        if expects_command and normalized == target:
            return exported

        if expects_command:
            if token in _COMMAND_POSITION_PREFIX_TOKENS:
                prefix_allows_options = True
                continue
            if _looks_like_env_assignment(token):
                if _env_assignment_name(token) == env_var:
                    pending_assignment = True
                continue
            if prefix_allows_options and (token == "--" or token.startswith("-")):
                continue
            expects_command = False
            prefix_allows_options = False
            active_command = os.path.basename(token)
            declare_exports = False
            pending_assignment = False
            if normalized == target:
                return exported
            continue

        if active_command == "export":
            if normalized == "--" or normalized.startswith("-"):
                continue
            if _env_assignment_name(token) == env_var:
                exported = True
            if normalized == env_var and assigned_in_shell:
                exported = True
            continue

        if active_command in _EXPORT_STYLE_COMMANDS:
            if normalized.startswith("-"):
                if "x" in normalized.lstrip("-"):
                    declare_exports = True
                continue
            if declare_exports:
                if _env_assignment_name(token) == env_var:
                    exported = True
                if normalized == env_var and assigned_in_shell:
                    exported = True

    return False


def _shell_command_prefix_env_value_for_target(command: str | None, env_var: str, target: str) -> str | None:
    if not isinstance(command, str) or not command.strip() or not env_var or not target:
        return None

    tokens = _split_shell_parts(command)
    expects_command = True
    prefix_allows_options = False
    assigned_values: dict[str, str] = {}

    for index, token in enumerate(tokens):
        if index > 0 and _is_command_flag(tokens[index - 1]):
            nested = _shell_command_prefix_env_value_for_target(token, env_var, target)
            if nested is not None:
                return nested

        normalized = _normalize_shell_token(token)
        if _token_resets_command_position(token):
            expects_command = True
            prefix_allows_options = False
            assigned_values = {}
            continue

        if expects_command:
            if normalized == target:
                return assigned_values.get(env_var)
            if token in _COMMAND_POSITION_PREFIX_TOKENS:
                prefix_allows_options = True
                continue
            if _looks_like_env_assignment(token):
                name, value = normalized.split("=", 1)
                assigned_values[name] = value
                continue
            if prefix_allows_options and (token == "--" or token.startswith("-")):
                continue
            expects_command = False
            prefix_allows_options = False

    return None


def _shell_command_prefix_env_value(command: str | None, env_var: str) -> str | None:
    if not isinstance(command, str) or not command.strip() or not env_var:
        return None

    tokens = _split_shell_parts(command)
    expects_command = True
    prefix_allows_options = False
    assigned_values: dict[str, str] = {}

    for token in tokens:
        normalized = _normalize_shell_token(token)
        if _token_resets_command_position(token):
            expects_command = True
            prefix_allows_options = False
            assigned_values = {}
            continue

        if expects_command:
            if token in _COMMAND_POSITION_PREFIX_TOKENS:
                prefix_allows_options = True
                continue
            if _looks_like_env_assignment(token):
                name, value = normalized.split("=", 1)
                assigned_values[name] = value
                continue
            if prefix_allows_options and (token == "--" or token.startswith("-")):
                continue
            return assigned_values.get(env_var)

    return None


def shell_command_prefixes_env_var(command: str | None, env_var: str) -> bool:
    if not isinstance(command, str) or not command.strip() or not env_var:
        return False

    tokens = _split_shell_parts(command)
    expects_command = True
    prefix_allows_options = False

    for token in tokens:
        if _token_resets_command_position(token):
            expects_command = True
            prefix_allows_options = False
            continue

        if expects_command:
            if token in _COMMAND_POSITION_PREFIX_TOKENS:
                prefix_allows_options = True
                continue
            if _looks_like_env_assignment(token):
                continue
            if prefix_allows_options and (token == "--" or token.startswith("-")):
                continue
            target = _normalize_shell_token(os.path.basename(token))
            if not target:
                return False
            return _shell_command_prefix_env_value_for_target(command, env_var, target) is not None

    return False


def _resolve_shell_path(path: str, *, home: Path | None = None) -> Path:
    resolved_home = (home or Path.home()).expanduser()
    expanded = _HOME_REFERENCE_PATTERN.sub(str(resolved_home), path.strip())
    expanded = os.path.expanduser(expanded)
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate


def _read_shell_file_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _shell_text_returns_early_for_noninteractive_bash(text: str) -> bool:
    return any(pattern.search(text) for pattern in _BASHRC_NONINTERACTIVE_GUARDS)


def _shell_text_defines_function(text: str, function_name: str) -> bool:
    pattern = re.compile(
        rf"(?:^|[;\n])\s*(?:function\s+)?{re.escape(function_name)}(?:\s*\(\s*\))?\s*\{{"
    )
    return bool(pattern.search(text))


def _shell_file_defines_function(path: Path, function_name: str) -> bool:
    text = _read_shell_file_text(path)
    if text is None:
        return False
    return _shell_text_defines_function(text, function_name)


def _shell_command_loads_kimi_from_bash_env(command: str | None, *, home: Path | None = None) -> bool:
    bash_env = _shell_command_prefix_env_value_for_target(command, "BASH_ENV", "bash")
    if not bash_env:
        return False
    text = _read_shell_file_text(_resolve_shell_path(bash_env, home=home))
    if text is None:
        return False
    if _shell_text_returns_early_for_noninteractive_bash(text):
        return False
    return _shell_text_defines_function(text, "kimi")


def _shell_command_sources_bashrc_before_target(command: str | None, target: str) -> bool:
    if not isinstance(command, str) or not command.strip():
        return False

    tokens = _split_shell_parts(command)
    expects_command = True
    prefix_allows_options = False
    active_command: str | None = None
    sourced_bashrc = False
    for index, token in enumerate(tokens):
        if active_command in _BASHRC_SOURCE_COMMANDS and _looks_like_bashrc_path(token):
            sourced_bashrc = True

        if expects_command and _normalize_shell_token(token) == target:
            return sourced_bashrc

        if index > 0 and _is_command_flag(tokens[index - 1]) and _shell_command_sources_bashrc_before_target(token, target):
            return True

        if expects_command:
            if token in _COMMAND_POSITION_PREFIX_TOKENS:
                prefix_allows_options = True
                continue
            if _looks_like_env_assignment(token):
                continue
            if prefix_allows_options and (token == "--" or token.startswith("-")):
                continue
            expects_command = False
            prefix_allows_options = False
            active_command = os.path.basename(token)
        if _token_resets_command_position(token):
            expects_command = True
            prefix_allows_options = False
            active_command = None
    return False


def shell_command_sources_bashrc_before_kimi(command: str | None) -> bool:
    return _shell_command_sources_bashrc_before_target(command, "kimi")


def shell_command_sources_bashrc(command: str | None) -> bool:
    if not isinstance(command, str) or not command.strip():
        return False

    tokens = _split_shell_parts(command)
    expects_command = True
    prefix_allows_options = False
    active_command: str | None = None
    for index, token in enumerate(tokens):
        if active_command in _BASHRC_SOURCE_COMMANDS and _looks_like_bashrc_path(token):
            return True
        if index > 0 and _is_command_flag(tokens[index - 1]) and shell_command_sources_bashrc(token):
            return True
        if expects_command:
            if token in _COMMAND_POSITION_PREFIX_TOKENS:
                prefix_allows_options = True
                continue
            if _looks_like_env_assignment(token):
                continue
            if prefix_allows_options and (token == "--" or token.startswith("-")):
                continue
            expects_command = False
            prefix_allows_options = False
            active_command = os.path.basename(token)
        if _token_resets_command_position(token):
            expects_command = True
            prefix_allows_options = False
            active_command = None
    return False


def shell_template_sources_bashrc_before_command(shell: str | None) -> bool:
    if not isinstance(shell, str) or "{command}" not in shell:
        return False
    placeholder = "__AGENTFLOW_COMMAND_PLACEHOLDER__"
    return _shell_command_sources_bashrc_before_target(shell.replace("{command}", placeholder), placeholder)


def shell_init_sources_bashrc_before_kimi(shell_init: Any) -> bool:
    sourced_bashrc = False
    for command in shell_init_commands(shell_init):
        if shell_command_sources_bashrc_before_kimi(command):
            return True
        if shell_command_uses_kimi_helper(command):
            return sourced_bashrc
        if shell_command_sources_bashrc(command):
            sourced_bashrc = True
    return False


def shell_init_exports_env_var(shell_init: Any, env_var: str) -> bool:
    rendered = render_shell_init(shell_init)
    if not rendered:
        return False
    placeholder = "__AGENTFLOW_ENV_EXPORT_TARGET__"
    return _shell_command_exports_env_var_before_target(f"{rendered} && {placeholder}", env_var, placeholder)


def shell_template_exports_env_var_before_command(shell: str | None, env_var: str) -> bool:
    if not isinstance(shell, str) or not shell.strip():
        return False

    if shell_wrapper_requires_command_placeholder(shell):
        return False

    if _shell_command_prefix_env_value(shell, env_var) is not None:
        return True

    if "{command}" not in shell:
        return False

    placeholder = "__AGENTFLOW_ENV_EXPORT_TARGET__"
    return _shell_command_exports_env_var_before_target(shell.replace("{command}", placeholder), env_var, placeholder)


def _explicit_bashrc_kimi_warning(subject: str) -> str:
    return (
        f"`{subject}` sources `~/.bashrc` before `kimi`, but `~/.bashrc` returns early for non-interactive "
        "bash on this host, so helpers defined later still do not load. Add `-i`, set `target.shell_interactive: true`, "
        "use `bash -lic`, or move the bootstrap into a login-sourced file."
    )


def _explicit_bashrc_shell_init_warning(subject: str) -> str:
    return (
        f"`{subject}` sources `~/.bashrc` before `shell_init`, but `~/.bashrc` returns early for non-interactive "
        "bash on this host, so helpers defined later still do not load. Add `-i`, set `target.shell_interactive: true`, "
        "use `bash -lic`, or move the bootstrap into a login-sourced file."
    )


def bashrc_returns_early_for_noninteractive_shell(home: Path | None = None) -> bool:
    resolved_home = (home or Path.home()).expanduser()
    bashrc_path = resolved_home / ".bashrc"
    text = _read_shell_file_text(bashrc_path)
    if text is None:
        return False
    return _shell_text_returns_early_for_noninteractive_bash(text)


def _token_uses_kimi_substitution(token: str) -> bool:
    for body in (*_COMMAND_SUBSTITUTION_PATTERN.findall(token), *_BACKTICK_COMMAND_SUBSTITUTION_PATTERN.findall(token)):
        if shell_command_uses_kimi_helper(body):
            return True
    return False


def invalid_bash_long_option_error(command: str | None) -> str | None:
    tokens = _split_shell_parts(command)
    for index, token in enumerate(tokens):
        if os.path.basename(token) != "bash":
            continue

        position = index + 1
        while position < len(tokens):
            arg = tokens[position]
            if arg == "--":
                return None
            if arg.startswith("--") and "=" in arg:
                option_name, _ = arg.split("=", 1)
                if option_name in _BASH_UNSUPPORTED_LONG_FLAG_DETAILS:
                    return _BASH_UNSUPPORTED_LONG_FLAG_DETAILS[option_name]
                if option_name in _BASH_LONG_FLAGS_WITH_VALUE:
                    return (
                        f"Bash does not support `{option_name}=...`; "
                        f"pass `{option_name}` and its value as separate arguments."
                    )
                if option_name in _BASH_SUPPORTED_LONG_FLAGS:
                    return f"Bash does not support `{option_name}=...`; use `{option_name}` without `=`."
            if arg in _BASH_UNSUPPORTED_LONG_FLAG_DETAILS:
                return _BASH_UNSUPPORTED_LONG_FLAG_DETAILS[arg]
            if arg in _BASH_LONG_FLAGS_WITH_VALUE:
                position += 2
                continue
            if arg in _BASH_SUPPORTED_LONG_FLAGS:
                position += 1
                continue
            if not arg.startswith("-") or arg == "-":
                return None
            if arg.startswith("--"):
                position += 1
                continue
            if "c" in arg[1:]:
                return None
            position += 1
        return None
    return None


def _is_kimi_probe_argument(tokens: list[str], index: int) -> bool:
    if index <= 0:
        return False

    previous = tokens[index - 1]
    if previous in {"type", "which", "hash"}:
        return True

    if index > 1 and previous.startswith("-") and tokens[index - 2] in {"type", "which", "hash"}:
        return True

    if previous in {"-v", "-V"} and index > 1 and tokens[index - 2] == "command":
        return True

    return False


def target_uses_bash(target: Any) -> bool:
    shell = _target_value(target, "shell")
    if not isinstance(shell, str) or not shell.strip():
        return False
    return any(os.path.basename(part) == "bash" for part in _split_shell_parts(shell))


def target_uses_interactive_bash(target: Any) -> bool:
    if bool(_target_value(target, "shell_interactive")):
        return True

    shell = _target_value(target, "shell")
    shell_parts = _split_shell_parts(shell if isinstance(shell, str) else None)
    if not shell_parts:
        return False

    for index, part in enumerate(shell_parts):
        if os.path.basename(part) != "bash":
            continue

        interactive = False
        position = index + 1
        while position < len(shell_parts):
            arg = shell_parts[position]
            if arg == "--":
                return interactive
            if arg.startswith("--"):
                if arg == "--command":
                    return interactive
                if arg in _BASH_LONG_FLAGS_WITH_VALUE:
                    position += 2
                    continue
                if any(arg.startswith(f"{option}=") for option in _BASH_LONG_FLAGS_WITH_VALUE):
                    position += 1
                    continue
                position += 1
                continue
            if not arg.startswith("-") or arg == "-":
                return interactive
            if "i" in arg[1:]:
                interactive = True
            if "c" in arg[1:]:
                return interactive
            position += 1
        return interactive

    return False


def shell_command_uses_kimi_helper(command: str | None) -> bool:
    if not isinstance(command, str) or not command.strip():
        return False

    tokens = _split_shell_parts(command)
    expects_command = True
    prefix_allows_options = False
    active_command: str | None = None
    for index, token in enumerate(tokens):
        if active_command in _KIMI_SUBSTITUTION_CONSUMERS and _token_uses_kimi_substitution(token):
            return True
        if _looks_like_kimi_token(token) and not _is_kimi_probe_argument(tokens, index):
            if expects_command:
                return True
        if index > 0 and _is_command_flag(tokens[index - 1]) and shell_command_uses_kimi_helper(token):
            return True
        if expects_command:
            if token in _COMMAND_POSITION_PREFIX_TOKENS:
                prefix_allows_options = True
                continue
            if _looks_like_env_assignment(token):
                continue
            if prefix_allows_options and (token == "--" or token.startswith("-")):
                continue
            expects_command = False
            prefix_allows_options = False
            active_command = os.path.basename(token)
        if _token_resets_command_position(token):
            expects_command = True
            prefix_allows_options = False
            active_command = None
    return False


def _kimi_bootstrap_without_interactive_bash_warning(source: str) -> str:
    if source == "target.shell_init":
        return (
            "`shell_init: kimi` uses bash without interactive startup; helpers from `~/.bashrc` are usually "
            "unavailable. Set `target.shell_interactive: true` or use `bash -lic`."
        )
    return (
        "`target.shell` uses `kimi` with bash without interactive startup; helpers from `~/.bashrc` are usually "
        "unavailable. Add `-i`, set `target.shell_interactive: true`, or use `bash -lic`."
    )


def _shell_program(target: Any) -> str | None:
    shell = _target_value(target, "shell")
    shell_parts = _split_shell_parts(shell if isinstance(shell, str) else None)
    if not shell_parts:
        return None
    return os.path.basename(shell_parts[0]) or None


def kimi_shell_init_requires_bash_warning(target: Any) -> str | None:
    if target_uses_bash(target):
        return None

    target_shell = _shell_program(target) or "this shell"
    shell_init = _target_value(target, "shell_init")
    shell = _target_value(target, "shell")

    if shell_init_uses_kimi_helper(shell_init):
        return (
            f"`shell_init: kimi` requires bash-style shell bootstrap, but `target.shell` resolves to `{target_shell}`. "
            "Use `shell: bash` with `target.shell_login: true` and `target.shell_interactive: true`, "
            "use `bash -lic`, or export provider variables directly."
        )

    if shell_command_uses_kimi_helper(shell if isinstance(shell, str) else None):
        return (
            f"`target.shell` runs `kimi` through `{target_shell}` instead of bash, so shared helpers from bash startup "
            "files will usually not load. Use `bash -lic`, set `shell: bash` plus login and interactive startup, "
            "or export provider variables directly."
        )

    return None


def kimi_shell_init_requires_interactive_bash_warning(target: Any, *, home: Path | None = None) -> str | None:
    if not target_uses_bash(target):
        return None
    if target_uses_interactive_bash(target):
        return None

    shell_init = _target_value(target, "shell_init")
    shell = _target_value(target, "shell")
    if _shell_command_loads_kimi_from_bash_env(shell if isinstance(shell, str) else None, home=home):
        return None
    guarded_bashrc = bashrc_returns_early_for_noninteractive_shell(home)
    if shell_init_uses_kimi_helper(shell_init):
        if guarded_bashrc:
            if shell_template_sources_bashrc_before_command(shell if isinstance(shell, str) else None):
                return _explicit_bashrc_shell_init_warning("target.shell")
            if shell_init_sources_bashrc_before_kimi(shell_init):
                return _explicit_bashrc_kimi_warning("shell_init")
        return _kimi_bootstrap_without_interactive_bash_warning("target.shell_init")

    if shell_command_uses_kimi_helper(shell if isinstance(shell, str) else None):
        if guarded_bashrc and shell_command_sources_bashrc_before_kimi(shell):
            return _explicit_bashrc_kimi_warning("target.shell")
        return _kimi_bootstrap_without_interactive_bash_warning("target.shell")

    return None
