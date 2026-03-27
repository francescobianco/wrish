
module gatttool

# Restart Bluetooth adapter to clear stale connections.
wrish_gatttool_restart() {
    bluetoothctl power off > /dev/null 2>&1
    bluetoothctl power on  > /dev/null 2>&1
}

# Read a single GATT characteristic by UUID. Args: <mac> <uuid>
# Returns raw gatttool output (includes echoed commands from PTY).
wrish_gatttool_read_uuid() {
    local mac="$1"
    local uuid="$2"
    (
        echo "connect ${mac}"
        sleep 10
        echo "char-read-uuid ${uuid}"
        sleep 3
    ) | script -q -c "gatttool -I" /dev/null 2>&1
}

# List all GATT characteristics. Args: <mac>
# Returns raw gatttool output.
wrish_gatttool_characteristics() {
    local mac="$1"
    (
        echo "connect ${mac}"
        sleep 10
        echo "characteristics"
        sleep 3
    ) | script -q -c "gatttool -I" /dev/null 2>&1
}

# Deep read: list all characteristics then read each UUID in one session.
# Uses --HEAD-- / --END-- echo-sentinels to delimit per-attribute blocks.
# Output per attribute:
#   UUID: <uuid>
#   --START--
#   <raw payload lines>
#   --END--
# Args: <mac> [--raw]
wrish_gatttool_deep_read() {
    echo "[deep-read] restarting bluetooth" >&2

    wrish_gatttool_restart

    local mac="$1"
    local raw_mode=0
    local decode_mode="raw"
    shift
    while [ $# -gt 0 ]; do
        case "$1" in
            --raw)           raw_mode=1 ;;
            --ascii)         decode_mode="ascii" ;;
            --little-endian) decode_mode="little-endian" ;;
        esac
        shift
    done

    echo "[deep-read] listing characteristics on ${mac}..." >&2

    # Phase 1: collect UUIDs
    local raw_chars
    raw_chars=$(wrish_gatttool_characteristics "$mac")

    local uuids=()
    mapfile -t uuids < <(
        printf '%s\n' "$raw_chars" \
          | sed 's/\x1b\[[0-9;]*m//g' \
          | grep -oP 'uuid: \K[0-9a-f-]+'
    )

    if [ ${#uuids[@]} -eq 0 ]; then
        echo "[deep-read] no characteristics found" >&2
        return 1
    fi
    echo "[deep-read] found ${#uuids[@]} characteristics" >&2

    # Phase 2: one session — each UUID wrapped by --HEAD-- / --END-- markers.
    # script provides a PTY so typed commands are echoed to output;
    # the echoed --HEAD-- / --END-- strings act as reliable delimiters.
    local output
    output=$(
        (
            echo "connect ${mac}"
            sleep 10
            for uuid in "${uuids[@]}"; do
                echo "--HEAD--"
                echo "char-read-uuid ${uuid}"
                sleep 1
                echo "--END--"
            done
        ) | script -q -c "gatttool -I" /dev/null 2>&1
    )

    if [ "$raw_mode" -eq 1 ]; then
        echo "$output"
        return 0
    fi

    # Phase 3: state-machine parser
    # idle → (line contains --HEAD--) → reading → (line contains --END--) → idle
    # On --END--: extract clean hex pairs from gatttool "handle: 0xXX   value: XX XX ..."
    local state="idle"
    local buf=""
    local uuid_idx=0

    while IFS= read -r line; do
        local clean
        clean=$(printf '%s' "$line" | sed 's/\x1b\[[0-9;]*m//g')

        if echo "$clean" | grep -qF -- "--HEAD--"; then
            state="reading"
            buf=""
        elif echo "$clean" | grep -qF -- "--END--"; then
            if [ "$state" = "reading" ]; then
                local current_uuid="${uuids[$uuid_idx]:-?}"
                uuid_idx=$(( uuid_idx + 1 ))

                # Extract hex pairs from: handle: 0xXXXX   value: XX XX XX ...
                local hex_val
                hex_val=$(printf '%s\n' "$buf" \
                    | sed 's/\x1b\[[0-9;]*m//g' \
                    | grep -oP 'value: \K[0-9a-f ]+' \
                    | head -1 \
                    | sed 's/[[:space:]]*$//')

                echo ""
                echo "UUID:  ${current_uuid}"
                if [ -n "$hex_val" ]; then
                    echo "hex:   ${hex_val}"
                    if [ "$decode_mode" != "raw" ]; then
                        local decoded
                        decoded=$(wrish_hex_decode "$hex_val" "$decode_mode")
                        [ -n "$decoded" ] && echo "${decode_mode}: ${decoded}"
                    fi
                else
                    # Show first meaningful non-empty, non-prompt error line
                    local err
                    err=$(printf '%s\n' "$buf" \
                        | sed 's/\x1b\[[0-9;]*m//g' \
                        | grep -ivE '^\s*$|^char-read-uuid|\[.*\]>' \
                        | head -1 \
                        | sed 's/^[[:space:]]*//')
                    echo "error: ${err:-(no response)}"
                fi
            fi
            state="idle"
        elif [ "$state" = "reading" ]; then
            buf+="${line}"$'\n'
        fi
    done <<< "$output"
}
