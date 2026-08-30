#!/bin/bash
set -euo pipefail

install_root=/usr/local/libexec/tbot-preflight
temporary_path=

cleanup() {
  if [[ -n "$temporary_path" && -e "$temporary_path" && ! -L "$temporary_path" ]]; then
    /bin/rm -f "$temporary_path"
  fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

usage() {
  /bin/cat <<'EOF'
Usage:
  sudo ./scripts/provision_course_mode_preflight_tools.sh \
    --docker-source /absolute/path/to/docker \
    --docker-sha256 <64-lowercase-hex> \
    --compose-source /absolute/path/to/docker-compose \
    --compose-sha256 <64-lowercase-hex>

This is an attended sudo operation. It installs only already hash-approved bytes:
  /usr/local/libexec/tbot-preflight/<sha256>/docker
  /usr/local/libexec/tbot-preflight/<sha256>/docker-compose

The script does not modify Docker.app, create a signing key, flash a device, or
start Docker services.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

is_sha256() {
  [[ "$1" =~ ^[0-9a-f]{64}$ ]]
}

docker_source=
docker_sha256=
compose_source=
compose_sha256=

while [[ $# -gt 0 ]]; do
  case "$1" in
    --docker-source) docker_source=${2-}; shift 2 ;;
    --docker-sha256) docker_sha256=${2-}; shift 2 ;;
    --compose-source) compose_source=${2-}; shift 2 ;;
    --compose-sha256) compose_sha256=${2-}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument" ;;
  esac
done

[[ $(/usr/bin/uname -s) == Darwin ]] || die "Darwin is required"
[[ ${EUID} -eq 0 && -n ${SUDO_USER-} && ${SUDO_USER} != root ]] ||
  die "run this script through an attended sudo session"
[[ "$docker_source" == /* && ! -L "$docker_source" && -f "$docker_source" && -x "$docker_source" ]] ||
  die "docker source must be an absolute executable regular file"
[[ "$compose_source" == /* && ! -L "$compose_source" && -f "$compose_source" && -x "$compose_source" ]] ||
  die "Compose source must be an absolute executable regular file"
is_sha256 "$docker_sha256" || die "invalid Docker SHA-256"
is_sha256 "$compose_sha256" || die "invalid Compose SHA-256"

# Open both sources once. All verification and copying below uses these FDs.
exec 8<"$docker_source"
exec 9<"$compose_source"

hash_fd() {
  local source_fd=$1
  /usr/bin/shasum -a 256 "/dev/fd/$source_fd" | /usr/bin/awk '{print $1}'
}

rewind_fd() {
  local source_fd=$1
  /usr/bin/perl -e 'sysseek(STDIN, 0, 0) == 0 or die "cannot rewind source FD\n"' <&"$source_fd"
}

[[ $(hash_fd 8) == "$docker_sha256" ]] || die "Docker source hash mismatch"
rewind_fd 8
[[ $(hash_fd 9) == "$compose_sha256" ]] || die "Compose source hash mismatch"
rewind_fd 9

verify_component() {
  local component=$1
  local expected_kind=$2
  [[ ! -L "$component" ]] || die "symlink is forbidden: $component"
  [[ -e "$component" ]] || die "missing installed component: $component"
  local owner_uid group_name mode kind
  owner_uid=$(/usr/bin/stat -f '%u' "$component")
  group_name=$(/usr/bin/stat -f '%Sg' "$component")
  mode=$(/usr/bin/stat -f '%Lp' "$component")
  kind=$(/usr/bin/stat -f '%HT' "$component")
  local acl_line_count
  acl_line_count=$(/bin/ls -lde "$component" | /usr/bin/wc -l | /usr/bin/tr -d ' ')
  [[ "$owner_uid" == "0" ]] || die "non-root owner: $component"
  [[ "$group_name" == "wheel" ]] || die "non-wheel group: $component"
  [[ "$acl_line_count" == "1" ]] || die "extended ACL is forbidden: $component"
  (( (8#$mode & 8#022) == 0 )) || die "mutable installed component: $component"
  if [[ "$expected_kind" == directory ]]; then
    [[ "$kind" == Directory ]] || die "not a directory: $component"
  else
    [[ "$kind" == "Regular File" && "$mode" == "555" ]] ||
      die "installed tool must be a 0555 regular file: $component"
  fi
}

ensure_directory() {
  local directory=$1
  if [[ ! -e "$directory" ]]; then
    /bin/mkdir "$directory"
    /bin/chmod -N "$directory"
    /usr/sbin/chown root:wheel "$directory"
    /bin/chmod 0555 "$directory"
  fi
  verify_component "$directory" directory
}

verify_component / directory
verify_component /usr directory
ensure_directory /usr/local
ensure_directory /usr/local/libexec
ensure_directory "$install_root"

install_tool() {
  local source_fd=$1
  local expected_sha=$2
  local basename=$3
  local digest_directory="$install_root/$expected_sha"
  local destination="$digest_directory/$basename"
  local temporary="$digest_directory/.${basename}.new.$$"
  temporary_path=$temporary

  ensure_directory "$digest_directory"
  if [[ -e "$destination" ]]; then
    verify_component "$destination" file
    [[ $(/usr/bin/shasum -a 256 "$destination" | /usr/bin/awk '{print $1}') == "$expected_sha" ]] ||
      die "existing installed tool hash mismatch: $destination"
    temporary_path=
    return
  fi

  /bin/cp "/dev/fd/$source_fd" "$temporary"
  /bin/chmod -N "$temporary"
  /usr/sbin/chown root:wheel "$temporary"
  /bin/chmod 0555 "$temporary"
  [[ $(/usr/bin/shasum -a 256 "$temporary" | /usr/bin/awk '{print $1}') == "$expected_sha" ]] ||
    die "copied tool hash mismatch: $basename"
  if ! /bin/ln "$temporary" "$destination"; then
    /bin/rm "$temporary"
    [[ -e "$destination" ]] || die "cannot publish installed tool: $destination"
    verify_component "$destination" file
    [[ $(/usr/bin/shasum -a 256 "$destination" | /usr/bin/awk '{print $1}') == "$expected_sha" ]] ||
      die "concurrent installed tool hash mismatch: $destination"
    temporary_path=
    return
  fi
  /bin/rm "$temporary"
  temporary_path=
  verify_component "$destination" file
  [[ $(/usr/bin/shasum -a 256 "$destination" | /usr/bin/awk '{print $1}') == "$expected_sha" ]] ||
    die "installed tool hash mismatch: $destination"
}

install_tool 8 "$docker_sha256" docker
install_tool 9 "$compose_sha256" docker-compose

echo "docker path: $install_root/$docker_sha256/docker"
echo "docker sha256: $docker_sha256"
echo "compose path: $install_root/$compose_sha256/docker-compose"
echo "compose sha256: $compose_sha256"
