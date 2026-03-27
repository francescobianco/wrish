
module gatt

# Global coproc file descriptors for the active GATT session
WRISH_GATT_FD_IN=""
WRISH_GATT_FD_OUT=""
WRISH_GATT_PID=""

# Ensure device is connected, with power cycle + retry if needed
# Args: <mac>
wrish_gatt_ensure_connected() {
    local mac
    local connected
    mac="$1"

    connected=$(echo -e "info ${mac}\nexit" | bluetoothctl | grep "Connected: yes")
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

    connected=$(echo -e "info ${mac}\nexit" | bluetoothctl | grep "Connected: yes")
    if [ -z "$connected" ]; then
        echo "[gatt] ERROR: could not connect to ${mac}" >&2
        return 1
    fi

    echo "[gatt] connected to ${mac}" >&2
}

# Open a persistent GATT session to a device
# Args: <mac>
wrish_gatt_open() {
    local mac
    mac="$1"

    wrish_gatt_ensure_connected "$mac" || return 1

    # Launch bluetoothctl as a coproc
    coproc WRISH_BT { bluetoothctl 2>&1; }
    WRISH_GATT_PID=$WRISH_BT_PID
    WRISH_GATT_FD_IN=${WRISH_BT[1]}
    WRISH_GATT_FD_OUT=${WRISH_BT[0]}

    # Enter GATT menu
    wrish_gatt_send "menu gatt"
    sleep 1
}

# Send a command to the open GATT session
# Args: <command>
wrish_gatt_send() {
    echo "$1" >&"${WRISH_GATT_FD_IN}"
}

# Read session output until pattern matches or timeout expires
# Prints all lines seen. Returns 0 on match, 1 on timeout.
# Args: <pattern> [timeout_seconds=10]
wrish_gatt_wait() {
    local pattern
    local timeout
    local line
    pattern="$1"
    timeout="${2:-10}"

    while IFS= read -r -t "$timeout" line <&"${WRISH_GATT_FD_OUT}"; do
        echo "$line"
        if echo "$line" | grep -qP "$pattern"; then
            return 0
        fi
    done
    return 1
}

# Select a GATT attribute by UUID
# Args: <uuid>
wrish_gatt_select() {
    wrish_gatt_send "select-attribute $1"
    sleep 0.5
}

# Enable notifications on the currently selected attribute
wrish_gatt_notify_on() {
    wrish_gatt_send "notify on"
    sleep 0.5
}

# Write hex bytes to the currently selected attribute and wait for ACK on FF01
# ACK pattern: line containing Value: followed by 8A at start
# Args: <hex_string>  e.g. "0x0A 0x02 0x00 0x00 0x07 0xBC"
# Optional second arg: ACK stage byte to wait for (0=type, 1=title, 2=body, 3=end)
wrish_gatt_write_wait_ack() {
    local hex
    local stage
    local ack_pattern
    hex="$1"
    stage="${2:-}"

    wrish_gatt_send "write \"${hex}\""

    if [ -n "$stage" ]; then
        # ACK lines look like: [CHG] Attribute ... Value: 8a 02 00 NN ...
        ack_pattern="Value:.*8a.*0${stage}"
        wrish_gatt_wait "$ack_pattern" 10
    else
        sleep 1
    fi
}

# Close the GATT session
wrish_gatt_close() {
    wrish_gatt_send "back"
    sleep 0.3
    wrish_gatt_send "exit"
    wait "$WRISH_GATT_PID" 2>/dev/null
    WRISH_GATT_PID=""
    WRISH_GATT_FD_IN=""
    WRISH_GATT_FD_OUT=""
}

# Show device info and verify connection
# Args: <mac>
wrish_gatt_info() {
    local mac
    mac="$1"
    echo -e "info ${mac}\nexit" | bluetoothctl | grep -E '(Name|Connected|Paired|Trusted|RSSI)'
}
