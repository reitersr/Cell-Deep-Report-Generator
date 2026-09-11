#!/usr/bin/env bash
set -euo pipefail

files=(
  celldeep-tool/schema.py
  celldeep-tool/pipeline.py
  celldeep-tool/generation_prompt.py
  celldeep-tool/template.py
  celldeep-tool/extraction_prompt.py
)

printf '%s\n' '# CellDeep Report Generator - Claude Review Export'
printf '%s\n' '# Generated from the current working tree. Review all five files below.'
printf '\n'

for file in "${files[@]}"; do
  printf '===== BEGIN %s =====\n' "$file"
  cat "$file"
  printf '\n===== END %s =====\n\n' "$file"
done
