#!/usr/bin/env bash
# Internal installer for Claude Provider Router. Use `bin/burnkit install router`.
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DRY_RUN=0

usage() {
    cat <<'EOF'
Claude Provider Router installer

Usage:
  bin/burnkit install router [--dry-run]

This creates tools/claude-provider-router/config.env from config.env.example
when it is missing. Existing config.env is preserved byte-for-byte.
It also installs a c shim into ~/.local/bin when that path is available.
EOF
}

log() {
    printf '%s\n' "$*"
}

ok() {
    printf 'OK  %s\n' "$1"
}

warn() {
    printf 'WARN %s\n' "$1"
}

die() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

require_file() {
    [ -f "$1" ] || die "missing file: $1"
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --dry-run) DRY_RUN=1 ;;
            -h|--help) usage; exit 0 ;;
            *) die "unknown router install option: $1" ;;
        esac
        shift
    done
}

install_c_shim() {
    local shim_dir="${BURNKIT_C_SHIM_DIR:-$HOME/.local/bin}"
    local shim="$shim_dir/c"
    local target="$SCRIPT_DIR/c"

    if [ -L "$shim" ] && [ "$(readlink "$shim")" = "$target" ]; then
        ok "c shim already installed: $shim"
        return 0
    fi

    if [ -e "$shim" ]; then
        warn "c command already exists and is not a managed shim: $shim"
        warn "skipping c shim; run directly with: $target"
        return 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        log "[dry-run] would install c shim: $shim -> $target"
        return 0
    fi

    mkdir -p "$shim_dir"
    ln -sf "$target" "$shim"
    ok "installed c shim: $shim -> $target"

    case ":${PATH}:" in
        *":$shim_dir:"*) ;;
        *) warn "add $shim_dir to PATH to use: c 0" ;;
    esac
}

install_settings() {
    local settings_file="$HOME/.claude/settings-c.json"
    local auth_helper="$SCRIPT_DIR/router-auth-helper.sh"

    if [ -f "$settings_file" ]; then
        ok "Claude settings already exists: $settings_file"
        return 0
    fi

    if [ ! -f "$auth_helper" ]; then
        warn "apiKeyHelper script not found: $auth_helper"
        return 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        log "[dry-run] would create $settings_file with apiKeyHelper: $auth_helper"
        return 0
    fi

    mkdir -p "$(dirname "$settings_file")"
    printf '{"apiKeyHelper": "%s"}\n' "$auth_helper" > "$settings_file"
    ok "created Claude settings: $settings_file"
}

ensure_shim_dir_in_path() {
    local shim_dir="${BURNKIT_C_SHIM_DIR:-$HOME/.local/bin}"

    case ":${PATH}:" in
        *":$shim_dir:"*) return 0 ;;
    esac

    local profile=""
    if [ "${SHELL:-}" = */zsh ]; then
        profile="$HOME/.zshrc"
    elif [ "${SHELL:-}" = */bash ]; then
        profile="$HOME/.bashrc"
    fi

    if [ -z "$profile" ] || [ ! -f "$profile" ]; then
        warn "add $shim_dir to PATH manually: export PATH=\"$shim_dir:\$PATH\""
        return 0
    fi

    if grep -q "$shim_dir" "$profile" 2>/dev/null; then
        return 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        log "[dry-run] would add $shim_dir to PATH in $profile"
        return 0
    fi

    printf '\nexport PATH="%s:$PATH"\n' "$shim_dir" >> "$profile"
    ok "added $shim_dir to PATH in $profile"
    warn "run 'source $profile' or open a new terminal to update PATH"
}

main() {
    parse_args "$@"

    local example="$SCRIPT_DIR/config.env.example"
    local target="$SCRIPT_DIR/config.env"
    require_file "$SCRIPT_DIR/c"
    require_file "$example"

    if [ -f "$target" ]; then
        ok "router config already exists: $target"
    elif [ "$DRY_RUN" -eq 1 ]; then
        log "[dry-run] would copy $example -> $target"
        log "[dry-run] would chmod 600 $target"
    else
        cp "$example" "$target"
        chmod 600 "$target"
        ok "created router config: $target"
        log "WARN edit $target before running: c 0"
    fi

    install_c_shim
    install_settings
    ensure_shim_dir_in_path
}

main "$@"
