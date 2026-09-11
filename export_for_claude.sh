#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

files=(
  "$SCRIPT_DIR/celldeep-tool/schema.py"
  "$SCRIPT_DIR/celldeep-tool/pipeline.py"
  "$SCRIPT_DIR/celldeep-tool/generation_prompt.py"
  "$SCRIPT_DIR/celldeep-tool/template.py"
  "$SCRIPT_DIR/celldeep-tool/extraction_prompt.py"
)

cat <<'EOF'
# CellDeep Report Generator - Claude Review Export
# Generated from the current working tree. Review all five files below.

EOF

for file in "${files[@]}"; do
  rel_path="${file#$SCRIPT_DIR/}"
  printf '\n## %s\n\n```python\n' "$rel_path"
  cat "$file"
  printf '\n```\n'
done
