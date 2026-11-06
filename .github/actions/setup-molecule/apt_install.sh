#!/usr/bin/env bash
# apt_install.sh list|install DIR PACKAGE...: install Ubuntu packages on a GitHub runner without
# letting one slow mirror fail the job, for setup-molecule's kvm input (QEMU).
#   list     apt-get update, write the files apt would download for PACKAGE... to DIR/files,
#            and output key=<cache key for those files> to $GITHUB_OUTPUT (stdout outside
#            Actions); the key is empty when there's nothing to download.
#   install  install PACKAGE... from the files in DIR/debs (restored from the cache) and the
#            mirrors for the rest, then copy the files this runner needed into DIR/debs (saved
#            to the cache after a miss).
# Every apt-get call has its own time limit: a composite action's steps can't set
# timeout-minutes. An update or a download that times out gets one more try on the next mirror.
set -eo pipefail

cmd=$1
dir=$2
shift 2 || true
if [ -z "$cmd" ] || [ -z "$dir" ] || [ $# -eq 0 ]; then
  echo "usage: $0 list|install DIR PACKAGE..." >&2
  exit 2
fi

# The runner's Ubuntu sources (mirror+file:) take their mirrors from this list, lowest
# priority:N first (apt's methods/mirror.cc), and move to the next one when a download fails,
# not when it crawls: from westus2 the Azure mirror has served .debs at 40 kB/s.
mirrors=/etc/apt/apt-mirrors.txt

# apt_get SECONDS ARGS...: apt-get, stopped at the limit (rc 124, or 137 if it ignored SIGTERM).
apt_get() {
  local limit=$1
  shift
  sudo timeout --kill-after=30 "$limit" apt-get "$@"
}

# drop_first_mirror: take the mirrors apt tries first out of the list, if others remain. Once per
# job: the runner's other two, archive.ubuntu.com and security.ubuntu.com, are the same Canonical
# servers. The package lists stay valid: apt names them after the list's path, not its mirrors.
drop_first_mirror() {
  local kept
  if [ ! -f "$mirrors" ] || [ -f "$dir/apt-mirrors.txt.orig" ]; then
    echo "Trying the same mirrors again."
    return 0
  fi
  kept=$(awk '{
      p = match($0, /priority:[0-9]+/) ? substr($0, RSTART + 9, RLENGTH - 9) + 0 : -1
      line[NR] = $0; prio[NR] = p
      if (p >= 0 && (min == "" || p < min)) min = p
    }
    END { for (i = 1; i <= NR; i++) if (min == "" || prio[i] != min) print line[i] }' "$mirrors")
  if [ -z "$kept" ] || [ "$kept" = "$(cat "$mirrors")" ]; then
    echo "No other mirror in $mirrors: trying the same ones again."
    return 0
  fi
  mkdir -p "$dir"
  cp "$mirrors" "$dir/apt-mirrors.txt.orig"
  echo "Dropping the first mirror from $mirrors:"
  grep -vxF "$kept" "$mirrors" | sed 's/^/  /' || true
  printf '%s\n' "$kept" | sudo tee "$mirrors" >/dev/null
}

# fetch WHAT FIRST SECOND ARGS...: an apt-get that downloads, given FIRST seconds, then SECOND
# more on the next mirror. The second try keeps what the first one downloaded.
fetch() {
  local what=$1 first=$2 second=$3 rc=0
  shift 3
  apt_get "$first" "$@" || rc=$?
  if [ "$rc" -ne 124 ] && [ "$rc" -ne 137 ]; then
    return "$rc"
  fi
  echo "::warning::$what didn't finish in $first seconds; trying the next mirror."
  drop_first_mirror
  rc=0
  apt_get "$second" "$@" || rc=$?
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    echo "::error::$what didn't finish in $first seconds, nor in $second more on a second try (a slow network?); re-run the job."
  fi
  return "$rc"
}

case $cmd in
  list)
    fetch "apt-get update" 60 60 update
    mkdir -p "$dir"
    # One line per file apt would download: 'URI' FILE SIZE SHA256:HASH, from the signed package
    # lists. None if PACKAGE... are installed.
    apt-get install --print-uris -qq -o Acquire::ForceHash=SHA256 --no-install-recommends "$@" > "$dir/files"
    key=
    if [ -s "$dir/files" ]; then
      # The files and their hashes, not the URIs, which name the mirror.
      key=setup-molecule-apt-${ImageOS:-linux}-$(cut -d' ' -f2- "$dir/files" | sort | sha256sum | cut -c1-16)
    fi
    echo "Files to download: $(wc -l < "$dir/files"), cache key: ${key:-none}"
    echo "key=$key" >> "${GITHUB_OUTPUT:-/dev/stdout}"
    ;;
  install)
    debs=$dir/debs
    # apt takes a file in its archives directory on its size alone (apt-pkg/acquire-item.cc), so
    # each cached file must match its SHA256 from the signed package lists to get there. apt
    # downloads the rest.
    cached=()
    while read -r _ file _ hash; do
      [ -f "$debs/$file" ] || continue
      if echo "${hash#SHA256:}  $debs/$file" | sha256sum -c --status; then
        cached+=("$debs/$file")
      else
        echo "::warning::$file from the cache doesn't match its SHA256 in the package lists; downloading it."
      fi
    done < "$dir/files"
    if [ "${#cached[@]}" -gt 0 ]; then
      echo "From the cache: ${#cached[@]} of $(wc -l < "$dir/files") files"
      sudo cp "${cached[@]}" /var/cache/apt/archives/
    fi
    fetch "The download of $*" 120 240 install --download-only -y --no-install-recommends "$@"
    # Before the install: an apt configured to clean up after dpkg would delete them.
    mkdir -p "$debs"
    while read -r _ file _; do
      cp "/var/cache/apt/archives/$file" "$debs/"
    done < "$dir/files"
    rc=0
    apt_get 120 install --no-download -y --no-install-recommends "$@" || rc=$?
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
      echo "::error::apt-get install didn't finish in 2 minutes with everything downloaded (dpkg stuck?); re-run the job."
    fi
    exit "$rc"
    ;;
  *)
    echo "usage: $0 list|install DIR PACKAGE..." >&2
    exit 2
    ;;
esac
