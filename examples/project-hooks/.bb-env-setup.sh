#!/usr/bin/env bash
set -euo pipefail
forklift project setup
# Optional: enable after adding a prepare command to .forklift.json.
# forklift project task prepare
