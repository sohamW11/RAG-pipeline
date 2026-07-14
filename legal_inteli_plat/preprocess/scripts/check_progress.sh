#!/usr/bin/env bash
# One-shot health/progress snapshot for the sharded corpus preprocessing run.
# Usage:
#   bash scripts/check_progress.sh          # single snapshot
#   watch -n 30 bash scripts/check_progress.sh   # live refresh every 30s
set -u
P=/home/choice/RAG_pipeline/legal_inteli_plat/preprocess
cd "$P"
TOTAL=3502

parsed_count() { ls parsed/*.json 2>/dev/null | grep -v manifest | wc -l; }

DONE=$(parsed_count)
REMAIN=$((TOTAL - DONE))
PCT=$(awk "BEGIN{printf \"%.1f\", ($DONE/$TOTAL)*100}")

echo "════════════════════════════════════════════════════════"
echo "  SEBI corpus preprocessing — $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════════"
echo "  Parsed: $DONE / $TOTAL  (${PCT}%)   Remaining: $REMAIN"

# --- processes ---
WORKERS=$(pgrep -f "run_corpus.py --shards" | wc -l)
if pgrep -f "supervise_corpus.sh" >/dev/null; then SUP="alive"; else SUP="DOWN"; fi
echo "  Workers alive: $WORKERS/4   Supervisor: $SUP"
if [ "$WORKERS" -lt 4 ] && [ "$REMAIN" -gt 0 ]; then
  echo "  ⚠  fewer than 4 workers running and run not complete —"
  echo "     supervisor should relaunch within 60s; if not, restart it:"
  echo "     setsid nohup bash scripts/supervise_corpus.sh >> logs/supervisor.log 2>&1 </dev/null &"
fi

# --- throughput / ETA from parsed-file mtimes in the last 10 min ---
RECENT=$(find parsed -maxdepth 1 -name '*.json' ! -name '*manifest*' -mmin -10 2>/dev/null | wc -l)
if [ "$RECENT" -gt 0 ] && [ "$REMAIN" -gt 0 ]; then
  RATE_MIN=$(awk "BEGIN{printf \"%.2f\", $RECENT/10}")           # docs per minute
  ETA_MIN=$(awk "BEGIN{printf \"%.0f\", $REMAIN/($RECENT/10)}")  # minutes remaining
  ETA_H=$(awk "BEGIN{printf \"%.1f\", $ETA_MIN/60}")
  echo "  Rate (last 10m): ${RATE_MIN} docs/min → ETA ~${ETA_H}h ($RECENT docs in 10m)"
elif [ "$REMAIN" -eq 0 ]; then
  echo "  ✅ Corpus complete."
else
  echo "  Rate: 0 docs in last 10m (warm-up, a large PDF, or stalled)"
fi

# --- per-shard latest status line from the logs ---
echo "  ── per shard (latest log line) ──"
for i in 0 1 2 3; do
  line=$(grep -h "\[shard $i\]" "logs/corpus_shard_$i.log" 2>/dev/null | tail -1)
  fin=$(grep -q "\[shard $i\] FINISHED" "logs/corpus_shard_$i.log" 2>/dev/null && echo " ✅FINISHED" || echo "")
  echo "    shard $i: ${line:-<no status yet>}$fin"
done

# --- quality signals from progress jsonl ---
FAILS=$(grep -h '"status": "fail"' parsed/_progress_shard_*.jsonl 2>/dev/null | wc -l)
WITHERR=$(grep -h '"status": "ok"' parsed/_progress_shard_*.jsonl 2>/dev/null | grep -v '"error_count": 0' | wc -l)
echo "  ── quality ──"
echo "    failed docs: $FAILS   |   ok-with-errors: $WITHERR"
if [ "$FAILS" -gt 0 ]; then
  echo "    recent failures:"
  grep -h '"status": "fail"' parsed/_progress_shard_*.jsonl 2>/dev/null | tail -3 | sed 's/^/      /'
fi
echo "════════════════════════════════════════════════════════"
