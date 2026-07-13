#!/usr/bin/env bash
# Watchdog for the sharded corpus run: keeps all shards batching, relaunching any
# worker that dies (crash / OOM / reboot). Each relaunch resumes automatically
# because run_corpus.py skips docs already in parsed/. Exits when every shard has
# printed its FINISHED marker, then writes the final report.
#
# Run detached so it survives the session:
#   P=/home/choice/RAG_pipeline/legal_inteli_plat/preprocess
#   setsid nohup bash "$P/scripts/supervise_corpus.sh" > "$P/logs/supervisor.log" 2>&1 < /dev/null &
set -u
P=/home/choice/RAG_pipeline/legal_inteli_plat/preprocess
cd "$P"
SHARDS=4
THREADS=3

launch() {
  local i=$1
  echo "$(date -Is) supervisor: (re)launching shard $i"
  PYTHONPATH="$P" OMP_NUM_THREADS=$THREADS MKL_NUM_THREADS=$THREADS setsid nohup \
    ./.venv/bin/python scripts/run_corpus.py --shards $SHARDS --shard "$i" --threads $THREADS \
    >> "logs/corpus_shard_$i.log" 2>&1 < /dev/null &
}

echo "$(date -Is) supervisor: start (shards=$SHARDS threads=$THREADS)"
while true; do
  all_done=1
  for i in 0 1 2 3; do
    if grep -q "\[shard $i\] FINISHED" "logs/corpus_shard_$i.log" 2>/dev/null; then
      continue
    fi
    all_done=0
    if ! pgrep -f "run_corpus.py --shards $SHARDS --shard $i --threads" >/dev/null 2>&1; then
      launch "$i"
    fi
  done
  [ "$all_done" -eq 1 ] && break
  sleep 60
done

echo "$(date -Is) supervisor: ALL SHARDS FINISHED — writing report"
./.venv/bin/python scripts/build_corpus_report.py
echo "$(date -Is) supervisor: done"
