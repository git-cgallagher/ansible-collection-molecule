#!/usr/bin/env bash
# image_collections.sh IMAGE DEST: copy the Ansible collections in IMAGE (an ansible-runtime
# image) into DEST/ansible_collections, for setup-molecule's collections-image input. That's
# every collection on the image's Python path, which is where ansible-core finds them and
# where ansible-runtime installs them, except git_cgallagher.molecule: the action installs
# that one from its own ref. Nothing here talks to Galaxy, and any failure fails the step.
set -eo pipefail

image=$1
dest=$2
if [ -z "$image" ] || [ -z "$dest" ]; then
  echo "usage: $0 IMAGE DEST" >&2
  exit 2
fi

# The caller normally pulled the image already (to read its constraints.txt). A stuck pull
# must not hang the job: a composite action's steps can't set timeout-minutes. (macOS has
# no timeout(1); there the pull isn't limited.)
if ! docker image inspect "$image" >/dev/null 2>&1; then
  limit=()
  if command -v timeout >/dev/null; then limit=(timeout --kill-after=30 600); fi
  if ! "${limit[@]}" docker pull --quiet "$image"; then
    echo "::error::Couldn't pull $image (10-minute limit)."
    exit 1
  fi
fi

# Ask the image where its collections are: the site-packages path moves with its Python version.
if ! src=$(docker run --rm "$image" python -c \
    'import ansible_collections; print(*ansible_collections.__path__, sep="\n")'); then
  echo "::error::$image has no ansible_collections directory on its Python path. Is it an ansible-runtime image?"
  exit 1
fi
if [ "$(printf '%s\n' "$src" | wc -l)" -ne 1 ]; then
  echo "::error::$image has more than one ansible_collections directory on its Python path: $(printf '%s ' "$src")"
  exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cid=$(docker create "$image")
trap 'docker rm "$cid" >/dev/null; rm -rf "$tmp"' EXIT
docker cp "$cid:$src/." "$tmp/"
# During a driver round the action's own ref is newer than the image's copy.
rm -rf "$tmp/git_cgallagher/molecule" "$tmp"/git_cgallagher.molecule-*.info

# Each collection (<namespace>/<name>) and its install record (<namespace>.<name>-<version>.info)
# replaces any copy already in DEST, so no stale file survives.
mkdir -p "$dest/ansible_collections"
for path in "$tmp"/*/*/ "$tmp"/*.info/; do
  [ -d "$path" ] || continue
  rel=${path#"$tmp"/}
  rel=${rel%/}
  rm -rf "${dest:?}/ansible_collections/$rel"
  mkdir -p "$(dirname "$dest/ansible_collections/$rel")"
  mv "$tmp/$rel" "$dest/ansible_collections/$rel"
done
echo "Copied the collections in $image ($src) to $dest/ansible_collections"
