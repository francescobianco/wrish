
module gatt

# Internal session state — one active session at a time
WRISH_GATT_FIFO=""
WRISH_GATT_LOG=""
WRISH_GATT_PID=""
WRISH_GATT_CMD_FD=""
WRISH_GATT_LOG_POS=0

# Ensure device is connected; reconnect with power cycle if needed. Args: <mac>
wrish_gatt_ensure_connected() {
    local mac
    local connected
    mac="$1"

    connected=$(echo -e "info ${mac}\nexit" | bluetoothctl 2>/dev/null | grep "Connected: yes")
    if [ -n "$connected" ]; then
        return 0
    fi

    echo "[gatt] device not connected, reconnecting..." >&2
    {
        echo "disconnect ${mac}"
        sleep 5
        echo "power off"
        sleep 5
        echo "power on"
        sleep 5
        echo "connect ${mac}"
        sleep 10
        echo "exit"
    } | bluetoothctl > /dev/null 2>&1

    connected=$(echo -e "info ${mac}\nexit" | bluetoothctl 2>/dev/null | grep "Connected: yes")
    if [ -z "$connected" ]; then
        echo "[gatt] ERROR: could not connect to ${mac}" >&2
        return 1
    fi
    echo "[gatt] connected to ${mac}" >&2
}

# Open a GATT session: bluetoothctl reads from a FIFO, writes to a log file.
# Connects to device, waits for ServicesResolved, enters GATT menu. Args: <mac>
wrish_gatt_open() {
    local mac
    mac="$1"

    WRISH_GATT_FIFO=$(mktemp -u /tmp/wrish-gatt-in-XXXXXX)
    WRISH_GATT_LOG=$(mktemp /tmp/wrish-gatt-out-XXXXXX)
    WRISH_GATT_LOG_POS=0

    mkfifo "$WRISH_GATT_FIFO"

    # Launch bluetoothctl: reads commands from FIFO, appends output to log
    bluetoothctl < "$WRISH_GATT_FIFO" >> "$WRISH_GATT_LOG" 2>&1 &
    WRISH_GATT_PID=$!

    # Open the FIFO for writing (keeps it open so bluetoothctl doesn't see EOF)
    exec {WRISH_GATT_CMD_FD}>"$WRISH_GATT_FIFO"

    # Connect and wait for connection + service resolution
    wrish_gatt_send "connect ${mac}"
    wrish_gatt_wait_log "Connection successful" 15 || {
        echo "[gatt] ERROR: connection failed" >&2
        wrish_gatt_close
        return 1
    }
    wrish_gatt_wait_log "ServicesResolved: yes" 10 || {
        echo "[gatt] WARNING: services may not be fully resolved" >&2
    }

    wrish_gatt_send "menu gatt"
    sleep 0.5
}

# Send a command to the open GATT session
wrish_gatt_send() {
    echo "$1" >&"${WRISH_GATT_CMD_FD}"
}

# Read new lines appended to the log since last call.
# Prints them and returns 0 if <pattern> found, 1 if timeout.
# Args: <pattern> [timeout_seconds=10]
wrish_gatt_wait_log() {
    local pattern
    local timeout
    local deadline
    local new_content
    pattern="$1"
    timeout="${2:-10}"
    deadline=$(( $(date +%s) + timeout ))

    while [ "$(date +%s)" -lt "$deadline" ]; do
        new_content=$(tail -c +$(( WRISH_GATT_LOG_POS + 1 )) "$WRISH_GATT_LOG" 2>/dev/null)
        if [ -n "$new_content" ]; then
            WRISH_GATT_LOG_POS=$(wc -c < "$WRISH_GATT_LOG")
            echo "$new_content"
            if echo "$new_content" | grep -qP "$pattern"; then
                return 0
            fi
        fi
        sleep 0.2
    done
    return 1
}

# Alias for use inside notify/battery functions
wrish_gatt_wait() {
    wrish_gatt_wait_log "$@"
}

# Select a GATT attribute by UUID
wrish_gatt_select() {
    wrish_gatt_send "select-attribute $1"
    sleep 0.3
}

# Enable notifications on the currently selected attribute
wrish_gatt_notify_on() {
    wrish_gatt_send "notify on"
    sleep 0.5
}

# Write hex string to current attribute and optionally wait for ACK on FF01.
# ACK pattern: line containing "Value:" followed by "8a" and the stage byte.
# Args: <hex_string> [ack_stage]  (ack_stage: 0=type, 1=title, 2=body, 3=end)
wrish_gatt_write_wait_ack() {
    local hex
    local stage
    local ack_pattern
    hex="$1"
    stage="${2:-}"

    wrish_gatt_send "write \"${hex}\" 0 command"

    if [ -n "$stage" ]; then
        ack_pattern="Value:.*8a.*0${stage}"
        wrish_gatt_wait_log "$ack_pattern" 10 > /dev/null || {
            echo "[gatt] WARNING: no ACK for stage ${stage}, continuing" >&2
        }
    else
        sleep 1
    fi
}

# Show basic device info via bluetoothctl (no session needed)
wrish_gatt_info() {
    local mac
    mac="$1"
    echo -e "info ${mac}\nexit" | bluetoothctl 2>/dev/null \
        | grep -E '^\s+(Name|Connected|Paired|Trusted|RSSI)' \
        | sed 's/^\s*//'
}

# Close the GATT session and clean up
wrish_gatt_close() {
    if [ -n "$WRISH_GATT_CMD_FD" ]; then
        wrish_gatt_send "back" 2>/dev/null || true
        sleep 0.2
        wrish_gatt_send "exit" 2>/dev/null || true
        exec {WRISH_GATT_CMD_FD}>&-
    fi
    [ -n "$WRISH_GATT_PID" ] && wait "$WRISH_GATT_PID" 2>/dev/null || true
    [ -n "$WRISH_GATT_FIFO" ] && rm -f "$WRISH_GATT_FIFO"
    [ -n "$WRISH_GATT_LOG" ]  && rm -f "$WRISH_GATT_LOG"
    WRISH_GATT_FIFO=""
    WRISH_GATT_LOG=""
    WRISH_GATT_PID=""
    WRISH_GATT_CMD_FD=""
    WRISH_GATT_LOG_POS=0
}
