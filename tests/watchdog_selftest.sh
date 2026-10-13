#!/bin/bash
# Run at image build time (see the Dockerfile): the watchdog must end when "ComfyUI" dies, and must pass through the
# exit status of /start.sh when it ends by itself.
set -u
W=/opt/memguard/watchdog.sh
tmp="$(mktemp -d)"

# 1) "ComfyUI" (a sleeping process) is killed after 2 s while the "handler" keeps running: exit status 1, quickly
cat > "$tmp/start_dies.sh" <<'EOF'
#!/bin/bash
sleep 300 &
echo $! > "$FAKE_PID_FILE"
( sleep 2; kill -9 "$(cat "$FAKE_PID_FILE")" ) &
sleep 300
EOF
chmod +x "$tmp/start_dies.sh"
began=$SECONDS
START_SCRIPT="$tmp/start_dies.sh" WATCHDOG_PID_FILE="$tmp/pid" FAKE_PID_FILE="$tmp/pid" WATCHDOG_INTERVAL_S=1 \
    timeout 60 "$W" > "$tmp/out1.txt" 2>&1
rc=$?
echo "case 1: exit status $rc after $((SECONDS - began)) s"; cat "$tmp/out1.txt"
[ "$rc" -eq 1 ] || { echo "FAIL: expected exit status 1"; exit 1; }
[ $((SECONDS - began)) -lt 30 ] || { echo "FAIL: took too long"; exit 1; }
grep -q "ComfyUI (pid .*) is gone" "$tmp/out1.txt" || { echo "FAIL: no watchdog message"; exit 1; }

# 2) the start script ends by itself with status 7 (no ComfyUI pid file): the status is passed through, no false alarm
cat > "$tmp/start_ends.sh" <<'EOF'
#!/bin/bash
sleep 2
exit 7
EOF
chmod +x "$tmp/start_ends.sh"
START_SCRIPT="$tmp/start_ends.sh" WATCHDOG_PID_FILE="$tmp/no_such_pid_file" WATCHDOG_INTERVAL_S=1 \
    timeout 60 "$W" > "$tmp/out2.txt" 2>&1
rc=$?
echo "case 2: exit status $rc"; cat "$tmp/out2.txt"
[ "$rc" -eq 7 ] || { echo "FAIL: expected exit status 7"; exit 1; }
echo "watchdog self-test passed"
