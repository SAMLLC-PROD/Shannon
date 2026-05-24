#!/usr/bin/env bash
# ===========================================================================
# shannon-prefetch.sh — Fetch Shannon v2 context for local model injection
#
# Usage: shannon-prefetch.sh "topic"
#
# Tries server Shannon (192.168.0.71) first, falls back to local (localhost).
# Output: formatted context block ready for system prompt injection.
# ===========================================================================

SHANNON_SERVER="http://192.168.0.71:8765"
SHANNON_LOCAL="http://localhost:8765"
AGENT="${SHANNON_AGENT:-guy}"
LIMIT="${SHANNON_LIMIT:-4000}"
TOPIC="$1"

if [ -z "$TOPIC" ]; then
  echo "Usage: shannon-prefetch.sh <topic>" >&2
  exit 1
fi

ENCODED_TOPIC=$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))" "$TOPIC")

TMPFILE=$(mktemp)
SOURCE=""
for URL in "$SHANNON_SERVER" "$SHANNON_LOCAL"; do
  if curl -sf --connect-timeout 3 "${URL}/memory?agent=${AGENT}&topic=${ENCODED_TOPIC}&limit_tokens=${LIMIT}" -o "$TMPFILE" 2>/dev/null; then
    if [ -s "$TMPFILE" ]; then
      SOURCE="$URL"
      break
    fi
  fi
done

if [ -z "$SOURCE" ]; then
  echo "# Shannon unavailable (both server and local)" >&2
  rm -f "$TMPFILE"
  exit 1
fi

python3 -c "
import json, sys

topic = sys.argv[1]
source = sys.argv[2]

with open(sys.argv[3]) as f:
    data = json.load(f)

entries = data.get('entries', [])
total = data.get('returned_count', len(entries))
scored = data.get('scored_count', 0)

if not entries:
    print('# No Shannon results for this topic', file=sys.stderr)
    sys.exit(0)

print('## Shannon Memory Context')
print(f'_Topic: {topic} | {total} entries from {scored} scored | Source: {source}_')
print()

for i, e in enumerate(entries[:15], 1):
    body = e.get('body', '')
    tags = e.get('tags', [])
    session = e.get('session_id', '')
    score = e.get('score', 0)
    if len(body) > 600:
        body = body[:597] + '...'
    tag_str = ', '.join(tags[:5]) if tags else ''
    print(f'### [{i}] {session} (score: {score:.3f})')
    if tag_str:
        print(f'Tags: {tag_str}')
    print(body)
    print()
" "$TOPIC" "$SOURCE" "$TMPFILE"

rm -f "$TMPFILE"
