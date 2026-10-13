#!/bin/bash
# Container entry point: the base image's /start.sh, plus a watchdog.
#
# /start.sh starts ComfyUI in the background and the RunPod handler in the foreground. When ComfyUI dies (the kernel's
# OOM killer, a crash), the handler stays up and answers every job with "ComfyUI HTTP unreachable" until someone
# deletes the worker by hand. This script ends the container as soon as ComfyUI is gone, so RunPod replaces the worker.
#
# Environment: WATCHDOG_INTERVAL_S (default 3), WATCHDOG_PID_FILE (default /tmp/comfyui.pid, written by /start.sh)

PID_FILE="${WATCHDOG_PID_FILE:-/tmp/comfyui.pid}"
INTERVAL="${WATCHDOG_INTERVAL_S:-3}"

alive() {   # running, and not a zombie (kill -0 succeeds on zombies)
    [ -d "/proc/$1" ] && ! grep -qE '^State:[[:space:]]+[ZX]' "/proc/$1/status" 2>/dev/null
}

"${START_SCRIPT:-/start.sh}" &
START_PID=$!

trap 'kill -TERM "$START_PID" 2>/dev/null; wait "$START_PID"; exit $?' TERM INT

comfy_pid=""
while alive "$START_PID"; do
    if [ -z "$comfy_pid" ] && [ -s "$PID_FILE" ]; then
        comfy_pid="$(tr -d '[:space:]' < "$PID_FILE")"
        echo "[watchdog] watching ComfyUI (pid $comfy_pid)"
    fi
    if [ -n "$comfy_pid" ] && ! alive "$comfy_pid"; then
        echo "[watchdog] ComfyUI (pid $comfy_pid) is gone: ending the container so that RunPod replaces this worker" >&2
        kill -TERM "$START_PID" 2>/dev/null
        sleep 1
        kill -KILL "$START_PID" 2>/dev/null
        exit 1
    fi
    sleep "$INTERVAL"
done

wait "$START_PID"
exit $?
