#!/usr/bin/env bash
set -euo pipefail

# Run the user-management utility on the Docker host through SSH.
# Override the defaults with CRYPTIS_SSH_TARGET and CRYPTIS_REMOTE_PROJECT.
target=${CRYPTIS_SSH_TARGET:-alex_a@debian}

quote_bash() {
  local quoted
  printf -v quoted '%q' "$1"
  printf '%s' "$quoted"
}

if [[ -n ${CRYPTIS_REMOTE_PROJECT:-} ]]; then
  remote_cd="cd $(quote_bash "$CRYPTIS_REMOTE_PROJECT")"
else
  remote_cd='cd "$HOME/CryptisDchat_project"'
fi

usage() {
  cat <<'EOF'
Usage:
  bash scripts/users_ssh.sh list
  bash scripts/users_ssh.sh check --username USERNAME --name "Display Name"
  bash scripts/users_ssh.sh delete --username USERNAME --name "Display Name"

Optional environment:
  CRYPTIS_SSH_TARGET=alex_a@debian
  CRYPTIS_REMOTE_PROJECT=/path/to/CryptisDchat_project
EOF
}

run_remote() {
  local remote_command=$1 quoted_command
  quoted_command=$(quote_bash "$remote_command")
  ssh "$target" "bash -lc $quoted_command"
}

if (($# == 0)); then
  usage >&2
  exit 2
fi

action=$1
shift
case "$action" in
  list)
    if (($# != 0)); then usage >&2; exit 2; fi
    remote_command="$remote_cd && exec python3 scripts/helpers/delete_user.py --list-users"
    ;;
  check|delete)
    username=
    display_name=
    while (($#)); do
      case "$1" in
        --username)
          if (($# < 2)) || [[ -n $username ]]; then usage >&2; exit 2; fi
          username=$2
          shift 2
          ;;
        --name)
          if (($# < 2)) || [[ -n $display_name ]]; then usage >&2; exit 2; fi
          display_name=$2
          shift 2
          ;;
        *)
          usage >&2
          exit 2
          ;;
      esac
    done
    if [[ -z $username || -z $display_name ]]; then usage >&2; exit 2; fi
    username_arg=$(quote_bash "$username")
    name_arg=$(quote_bash "$display_name")
    remote_command="$remote_cd && exec python3 scripts/helpers/delete_user.py --username $username_arg --name $name_arg"
    if [[ $action == check ]]; then
      remote_command+=" --dry-run"
    fi
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

run_remote "$remote_command"
