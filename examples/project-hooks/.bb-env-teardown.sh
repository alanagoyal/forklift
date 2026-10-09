#!/usr/bin/env bash
set -euo pipefail
# Stop foreground project commands before archiving the last BB thread.
forklift project recover
forklift project stop
