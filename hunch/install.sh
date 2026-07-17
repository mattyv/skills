#!/bin/sh
# hunch installer — POSIX sh, stdlib-only Python check. No pip, no venv:
# hunch.py only ever touches the Python 3.9+ standard library.
set -eu

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
DEFAULT_TARGET="$HOME/.claude/skills/hunch"
TARGET=""
MODE="symlink"
FORCE=0

usage() {
    cat <<'EOF'
Usage: install.sh [--copy] [--force] [--target DIR]

  --copy         Copy the hunch/ directory instead of symlinking it.
  --force        Overwrite an existing non-symlink target directory.
  --target DIR   Install to DIR instead of ~/.claude/skills/hunch
                 (mainly useful for testing).
  -h, --help     Show this help.

No pip, no virtualenv: hunch.py is Python 3.9+ standard library only.
This script just finds a Python interpreter, links (or copies) hunch/
into place, and runs the built-in demo as a smoke test.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --copy)
            MODE="copy"
            shift
            ;;
        --force)
            FORCE=1
            shift
            ;;
        --target)
            TARGET="${2:-}"
            if [ -z "$TARGET" ]; then
                echo "hunch: --target requires an argument" >&2
                exit 1
            fi
            shift 2
            ;;
        --target=*)
            TARGET="${1#--target=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "hunch: unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ -z "$TARGET" ]; then
    TARGET="$DEFAULT_TARGET"
fi

# --- locate a suitable Python interpreter -----------------------------------
# stdlib-only: no pip, no venv, nothing to install for the Python side.

find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=""
if found=$(find_python); then
    PYTHON="$found"
fi

if [ -z "$PYTHON" ]; then
    echo "hunch: no suitable Python interpreter found (need Python 3.9 or newer)." >&2
    echo "" >&2
    case "$(uname -s 2>/dev/null || echo unknown)" in
        Linux*)
            echo "  Install one with your package manager, e.g.:" >&2
            echo "    sudo apt install python3      # Debian/Ubuntu" >&2
            echo "    sudo dnf install python3      # Fedora" >&2
            ;;
        Darwin*)
            echo "  Install one with Homebrew:" >&2
            echo "    brew install python3" >&2
            ;;
        MINGW*|MSYS*|CYGWIN*)
            echo "  You're on Windows (Git Bash). Install Python from:" >&2
            echo "    https://www.python.org/downloads/windows/" >&2
            echo "  or: winget install Python.Python.3" >&2
            ;;
        *)
            echo "  Install Python 3.9+ from https://www.python.org/downloads/" >&2
            ;;
    esac
    exit 1
fi

echo "hunch: using $PYTHON ($("$PYTHON" --version 2>&1))"
echo "hunch: stdlib only — no pip install, no virtualenv needed."

# --- link (or copy) hunch/ into place ---------------------------------------

PARENT_DIR=$(dirname "$TARGET")
mkdir -p "$PARENT_DIR"

# A directory this installer created previously (via --copy) looks like a
# hunch checkout itself, so re-running --copy is idempotent without needing
# --force. Anything else that happens to already live at TARGET is treated
# as foreign and left alone unless the caller passes --force.
looks_like_hunch_install() {
    [ -f "$1/hunch.py" ] && grep -q "prog='hunch'" "$1/hunch.py" >/dev/null 2>&1
}

# Everything a stock --copy install puts at TARGET. Anything else found
# there (most importantly a .hunch/ ledger a real session created) means
# this is no longer "just a checkout" and must not be rm -rf'd on a whim.
KNOWN_MANIFEST="hunch.py test_hunch.py SKILL.md README.md LICENSE install.sh references .gitignore __pycache__"

find_unexpected_entries() {
    dir="$1"
    for entry in "$dir"/* "$dir"/.[!.]* "$dir"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        base=$(basename "$entry")
        known=0
        for k in $KNOWN_MANIFEST; do
            if [ "$base" = "$k" ]; then
                known=1
                break
            fi
        done
        if [ "$known" -eq 0 ]; then
            printf '%s\n' "$base"
        fi
    done
}

# CRITICAL: never rm -rf the directory this script itself is running from.
# TARGET can alias SCRIPT_DIR either literally (README suggests
# `cd ~/.claude/skills/hunch && ./install.sh`) or via a relative/symlinked
# path — compare physical (symlink-resolved) paths, not the raw strings.
SCRIPT_DIR_PHYS=$(cd "$SCRIPT_DIR" && pwd -P)
TARGET_SELF=0
if [ -e "$TARGET" ]; then
    TARGET_PHYS=$(cd "$TARGET" 2>/dev/null && pwd -P || true)
    if [ -n "$TARGET_PHYS" ] && [ "$TARGET_PHYS" = "$SCRIPT_DIR_PHYS" ]; then
        TARGET_SELF=1
        echo "hunch: $TARGET is the directory this script is already running from — nothing to link/copy."
    fi
fi

if [ "$TARGET_SELF" -ne 1 ]; then
    if [ -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
        if [ "$FORCE" -ne 1 ]; then
            if ! looks_like_hunch_install "$TARGET"; then
                echo "hunch: $TARGET already exists and is not a symlink — refusing to overwrite." >&2
                echo "       Re-run with --force if you're sure, or remove it yourself first." >&2
                exit 1
            fi
            extras=$(find_unexpected_entries "$TARGET")
            if [ -n "$extras" ]; then
                echo "hunch: $TARGET looks like a hunch install but has extra files beyond a stock checkout — refusing without --force:" >&2
                printf '  %s\n' $extras >&2
                echo "       Re-run with --force if you're sure (this rm -rf's the whole directory, extras included)." >&2
                exit 1
            fi
        fi
        rm -rf "$TARGET"
    fi

    if [ -L "$TARGET" ]; then
        rm -f "$TARGET"
    fi

    if [ "$MODE" = "copy" ]; then
        rm -rf "$TARGET"
        cp -r "$SCRIPT_DIR" "$TARGET"
        rm -rf "$TARGET/__pycache__"
        echo "hunch: copied $SCRIPT_DIR -> $TARGET"
    else
        ln -s "$SCRIPT_DIR" "$TARGET"
        echo "hunch: symlinked $TARGET -> $SCRIPT_DIR"
    fi
fi

# --- smoke test --------------------------------------------------------------

echo "hunch: running smoke test ($PYTHON $TARGET/hunch.py demo)..."
set +e
DEMO_OUTPUT=$("$PYTHON" "$TARGET/hunch.py" demo 2>&1)
DEMO_STATUS=$?
set -e

if [ "$DEMO_STATUS" -eq 0 ] && printf '%s' "$DEMO_OUTPUT" | grep -q '"ok": true'; then
    echo "PASS: hunch installed at $TARGET"
    VERDICT_LINE=$(printf '%s' "$DEMO_OUTPUT" | grep -m1 '"verdict"' || true)
    if [ -n "$VERDICT_LINE" ]; then
        echo "  $(printf '%s' "$VERDICT_LINE" | sed 's/^ *//')"
    fi
    exit 0
else
    echo "FAIL: hunch.py demo did not report ok: true" >&2
    printf '%s\n' "$DEMO_OUTPUT" >&2
    exit 1
fi
