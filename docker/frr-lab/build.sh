#!/bin/sh
# Build the frr-lab node image reproducibly, without leaving a committable
# public key in the source tree.
#
# Usage:  ./build.sh [FRR_VERSION] [PUBKEY_PATH]
#   FRR_VERSION  quay.io/frrouting/frr tag to build from   (default 10.7.1)
#   PUBKEY_PATH  SSH public key to bake into authorized_keys
#                (default ../../ansible/.ssh/id_ed25519.pub)
#
# The Dockerfile needs the pubkey inside its build context. Rather than copy it
# next to the Dockerfile (where .gitignore's `!*.pub` would make it committable),
# assemble a throwaway context dir, build from there, and delete it on exit.
set -eu

FRR_VERSION="${1:-10.7.1}"
cd "$(dirname "$0")"
PUBKEY="${2:-../../ansible/.ssh/id_ed25519.pub}"

if [ ! -f "$PUBKEY" ]; then
  echo "build.sh: public key not found: $PUBKEY" >&2
  exit 1
fi

ctx="$(mktemp -d)"
trap 'rm -rf "$ctx"' EXIT

cp Dockerfile entrypoint.sh daemons "$ctx/"
cp "$PUBKEY" "$ctx/id_ed25519.pub"

docker build -t frr-lab:local --build-arg FRR_VERSION="$FRR_VERSION" "$ctx"

echo "built frr-lab:local (FRR $FRR_VERSION)"
