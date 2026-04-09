#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/shouc/agentflow.git"
INSTALL_DIR="${AGENTFLOW_DIR:-$HOME/.agentflow}"

echo "AgentFlow Installer"
echo "==================="
echo ""

# Check dependencies
for cmd in python3 git; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "Error: $cmd not found. Please install it first."
    exit 1
  fi
done

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  echo "Error: Python 3.11+ required (found $PY_VERSION)"
  exit 1
fi
echo "✓ Python $PY_VERSION"

# Clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating $INSTALL_DIR..."
  cd "$INSTALL_DIR" && git pull --quiet
else
  echo "Installing to $INSTALL_DIR..."
  git clone --quiet --depth 1 "$REPO" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
echo "✓ Repository ready"

# Create venv and install
if [ ! -d ".venv" ]; then
  echo "  Creating virtual environment..."
  python3 -m venv .venv
fi
echo "  Installing dependencies (this may take a minute)..."
.venv/bin/pip install -e ".[dev]" 2>&1 | tail -1
echo "✓ Package installed"

# Build frontend if node is available
if command -v npm &>/dev/null; then
  echo "  Building frontend dashboard..."
  (cd agentflow/web/frontend && npm install && npm run build) 2>&1 | tail -1
  echo "✓ Frontend built"
else
  echo "! Skipping frontend build (node/npm not found)"
fi

# Add to PATH
BINDIR="$INSTALL_DIR/.venv/bin"
SHELL_RC=""
if [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-}")" = "zsh" ]; then
  SHELL_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
  SHELL_RC="$HOME/.bashrc"
elif [ -f "$HOME/.profile" ]; then
  SHELL_RC="$HOME/.profile"
fi

if [ -n "$SHELL_RC" ] && ! grep -q "agentflow" "$SHELL_RC" 2>/dev/null; then
  printf '\n# AgentFlow\nexport PATH="%s:$PATH"\n' "$BINDIR" >> "$SHELL_RC"
  echo "✓ Added to PATH in $SHELL_RC"
fi

# Install skills (always -- user may install codex/claude later)
SKILL_SRC="$INSTALL_DIR/skills/agentflow/SKILL.md"

mkdir -p "$HOME/.codex/skills/agentflow"
cp "$SKILL_SRC" "$HOME/.codex/skills/agentflow/SKILL.md"
echo "✓ Codex skill installed"

mkdir -p "$HOME/.claude/skills/agentflow"
cp "$SKILL_SRC" "$HOME/.claude/skills/agentflow/SKILL.md"
echo "✓ Claude Code skill installed"

echo ""
echo "Done! Restart your shell, then:"
echo ""
echo "  agentflow init > pipeline.py"
echo "  agentflow run pipeline.py"
