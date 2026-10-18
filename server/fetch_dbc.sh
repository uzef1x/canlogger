#!/bin/sh
# Fetches the Leaf CAN databases used for decoding. They are GPL-3.0 licensed, so they are
# downloaded here (and git-ignored) instead of being copied into this repository.
set -e
cd "$(dirname "$0")"
mkdir -p dbc
if [ -d dbc/leaf_can_bus_messages/.git ]; then
  git -C dbc/leaf_can_bus_messages pull --ff-only
else
  git clone --depth 1 https://github.com/dalathegreat/leaf_can_bus_messages.git dbc/leaf_can_bus_messages
fi
