
module c60_a82c

# Protocol UUIDs — constants of this device model, not per-device config
C60_A82C_UUID_WRITE="0000ff02-0000-1000-8000-00805f9b34fb"
C60_A82C_UUID_NOTIFY="0000ff01-0000-1000-8000-00805f9b34fb"
C60_A82C_UUID_HR="00002a37-0000-1000-8000-00805f9b34fb"

# Compute frame checksum: ((sum_of_preceding_bytes * 0x56) + 0x5A) & 0xFF
wrish_c60a82c_checksum() {
    local sum
    local b
    sum=0
    for b in "$@"; do
        sum=$(( (sum + b) & 0xFF ))
    done
    printf '%d' $(( ((sum * 0x56) + 0x5A) & 0xFF ))
}

# Build setMessageType frame. Args: <app_type_decimal>
wrish_c60a82c_build_set_message_type() {
    local bytes
    local chk
    bytes=(10 2 0 0 "$1")
    chk=$(wrish_c60a82c_checksum "${bytes[@]}")
    echo "${bytes[*]} $chk"
}

# Build setMessage2 frame. Args: <kind> <text>  (kind=1 title, kind=2 body)
wrish_c60a82c_build_set_message2() {
    local kind
    local text
    local max_len
    local text_bytes
    local i
    local byte
    local payload_len
    local len_lo
    local len_hi
    local frame
    local chk
    kind="$1"
    text="$2"
    [ "$kind" -eq 1 ] && max_len=32 || max_len=128
    text="${text:0:$max_len}"
    text_bytes=()
    for (( i=0; i<${#text}; i++ )); do
        byte=$(printf '%d' "'${text:$i:1}")
        text_bytes+=("$byte")
    done
    payload_len=$(( 1 + ${#text_bytes[@]} ))
    len_lo=$(( payload_len & 0xFF ))
    len_hi=$(( (payload_len >> 8) & 0xFF ))
    frame=(10 "$len_lo" "$len_hi" "$kind" "${text_bytes[@]}")
    chk=$(wrish_c60a82c_checksum "${frame[@]}")
    echo "${frame[*]} $chk"
}

# Convert decimal byte array to gatttool hex string "xx xx xx" (lowercase, no 0x).
wrish_c60a82c_to_gatttool_hex() {
    local result=""
    local b
    for b in "$@"; do
        [ -n "$result" ] && result+=" "
        result+="$(printf '%02x' $(( b )))"
    done
    echo "$result"
}

# Map app name to bracelet app type code
wrish_c60a82c_app_type() {
    case "${1,,}" in
        wechat)      echo 2  ;;
        qq)          echo 3  ;;
        facebook)    echo 4  ;;
        skype)       echo 5  ;;
        twitter)     echo 6  ;;
        whatsapp)    echo 7  ;;
        line)        echo 8  ;;
        linkedin)    echo 9  ;;
        instagram)   echo 10 ;;
        messenger)   echo 12 ;;
        vk)          echo 13 ;;
        viber)       echo 14 ;;
        telegram)    echo 16 ;;
        kakaotalk)   echo 18 ;;
        douyin)      echo 32 ;;
        kuaishou)    echo 33 ;;
        douyin_lite) echo 34 ;;
        maimai)      echo 52 ;;
        pinduoduo)   echo 53 ;;
        work_wechat) echo 54 ;;
        tantan)      echo 56 ;;
        taobao)      echo 57 ;;
        *)           echo 7  ;;
    esac
}

# Read device info — real GATT read of 0x2A00 via gatttool. Args: <mac>
wrish_c60a82c_info() {
    local mac
    mac="${1:-${WRISH_MAC}}"

    echo "[info] reading device name from GATT 0x2A00 on ${mac}..." >&2

    local raw
    raw=$(wrish_gatttool_read_uuid "$mac" "00002a00-0000-1000-8000-00805f9b34fb")

    # Extract hex bytes from: handle: 0x00XX   value: XX XX XX ...
    local hex_bytes
    hex_bytes=$(printf '%s\n' "$raw" \
        | sed 's/\x1b\[[0-9;]*m//g' \
        | grep -oE 'value: [0-9a-f ]+' | sed 's/value: //' \
        | head -1 \
        | sed 's/[[:space:]]*$//')

    if [ -z "$hex_bytes" ]; then
        echo "Error: could not read device name (not connected or no response)" >&2
        return 1
    fi

    local name
    name=$(wrish_hex_to_ascii "$hex_bytes")

    echo "Device Name (0x2A00): ${name}"
    echo "MAC:                  ${mac}"
}

# Deep read all GATT characteristics via gatttool. Args: [--raw]
wrish_c60a82c_deep_read() {
    wrish_gatttool_deep_read "${WRISH_MAC}" "$@"
}

# Send a single vendor command to FF02 and listen for response on FF01.
# Handles connect + CCCD enable + write + dump.
# Args: <label> <gatttool_hex_bytes>  e.g. "vibrate" "0a 01 00 04 64"
wrish_c60a82c_send_vendor_cmd() {
    local label="$1"
    local hex="$2"
    local mac="${WRISH_MAC}"

    echo "[${label}] checking connection..." >&2
    local attempt=1
    while ! wrish_gatttool_check_connected "$mac"; do
        if [ "$attempt" -ge 3 ]; then
            echo "[${label}] ERROR: not connected after ${attempt} attempts" >&2
            return 1
        fi
        echo "[${label}] not connected — power cycle attempt ${attempt}/3..." >&2
        wrish_gatttool_restart
        attempt=$(( attempt + 1 ))
    done

    echo "[${label}] resolving handles..." >&2
    local char_desc
    char_desc=$(wrish_gatttool_char_desc "$mac")

    local ff01_handle ff01_cccd ff02_handle
    ff01_handle=$(wrish_gatttool_find_handle "$char_desc" "$C60_A82C_UUID_NOTIFY")
    ff01_cccd=$(wrish_gatttool_find_cccd   "$char_desc" "$ff01_handle")
    ff02_handle=$(wrish_gatttool_find_handle "$char_desc" "$C60_A82C_UUID_WRITE")

    if [ -z "$ff01_handle" ] || [ -z "$ff02_handle" ]; then
        echo "[${label}] ERROR: could not resolve handles" >&2
        return 1
    fi
    echo "[${label}] FF01=${ff01_handle} CCCD=${ff01_cccd} FF02=${ff02_handle}" >&2
    echo "[${label}] sending: ${hex}" >&2

    local output
    output=$(
        (
            echo "connect ${mac}"
            sleep 10
            echo "char-write-req ${ff01_cccd} 01 00"
            sleep 2
            echo "char-write-req ${ff02_handle} ${hex}"
            sleep 10
        ) | script -q -c "gatttool -I" /dev/null 2>&1
    )

    echo "[${label}] session output:" >&2
    wrish_gatttool_dump_session "$output" "[${label}]"
}

# Vibrate the bracelet (keephealth vendor cmd 0x04 under 0x0A family).
# Frame: 0A 01 00 04 <chk>
wrish_c60a82c_vibrate() {
    local bytes=(10 1 0 4)
    local chk; chk=$(wrish_c60a82c_checksum "${bytes[@]}")
    local hex; hex=$(wrish_c60a82c_to_gatttool_hex "${bytes[@]}" "$chk")
    wrish_c60a82c_send_vendor_cmd "vibrate" "$hex"
}

# Find / ring the bracelet — validated via proxy on 2026-03-28.
# Fixed 20-byte frame (header 0x10, not the 0x0A family — no checksum byte).
# Response on FF01: 90 01 00 00 10
wrish_c60a82c_find() {
    wrish_c60a82c_send_vendor_cmd "find" \
        "10 08 00 00 00 00 00 01 00 00 00 c0 00 00 00 00 00 00 00 00"
}

# Send raw hex bytes to FF02 and listen on FF01. Args: <hex_bytes...>
# e.g.: wrish raw 0a 01 00 06 10
wrish_c60a82c_raw() {
    local hex="$*"
    wrish_c60a82c_send_vendor_cmd "raw" "$hex"
}

# Brute-force scan all single-byte commands on FF02 (frame: 0A 01 00 <byte> <chk>).
# Uses --CMD-XX-- PTY echo sentinels to match each command to its FF01 response.
# Args: [--from <hex>] [--to <hex>] [--sleep <secs>]
wrish_c60a82c_scan_cmds() {
    local from=0 to=255 delay=1
    while [ $# -gt 0 ]; do
        case "$1" in
            --from)  from=$(( 16#${2#0x} )); shift 2 ;;
            --to)    to=$(( 16#${2#0x} )); shift 2 ;;
            --sleep) delay="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; return 1 ;;
        esac
    done

    local mac="${WRISH_MAC}"
    local total=$(( to - from + 1 ))
    local eta=$(( 10 + 2 + total * delay ))

    # connect + char-desc in one shot: if char-desc succeeds, we are connected
    echo "[scan-cmds] connecting and resolving handles..." >&2
    local char_desc attempt=1
    while true; do
        char_desc=$(wrish_gatttool_char_desc "$mac")
        if printf '%s\n' "$char_desc" | grep -q "handle:"; then
            break
        fi
        if [ "$attempt" -ge 3 ]; then
            echo "[scan-cmds] ERROR: could not connect after ${attempt} attempts" >&2; return 1
        fi
        echo "[scan-cmds] no handles found — power cycle attempt ${attempt}/3..." >&2
        wrish_gatttool_restart
        attempt=$(( attempt + 1 ))
    done
    local ff01_handle ff01_cccd ff02_handle
    ff01_handle=$(wrish_gatttool_find_handle "$char_desc" "$C60_A82C_UUID_NOTIFY")
    ff01_cccd=$(wrish_gatttool_find_cccd   "$char_desc" "$ff01_handle")
    ff02_handle=$(wrish_gatttool_find_handle "$char_desc" "$C60_A82C_UUID_WRITE")
    if [ -z "$ff01_handle" ] || [ -z "$ff02_handle" ]; then
        echo "[scan-cmds] ERROR: could not resolve handles" >&2; return 1
    fi
    echo "[scan-cmds] FF01=${ff01_handle} CCCD=${ff01_cccd} FF02=${ff02_handle}" >&2
    echo "[scan-cmds] scanning 0x$(printf '%02x' $from)..0x$(printf '%02x' $to)" \
         "(${total} cmds, ${delay}s/cmd, ~${eta}s total)" >&2

    local output
    output=$(
        (
            echo "connect ${mac}"
            sleep 10
            echo "char-write-req ${ff01_cccd} 01 00"
            sleep 2
            local b bytes chk hex step=0
            for (( b=from; b<=to; b++ )); do
                step=$(( b - from + 1 ))
                printf '[scan-cmds] %d/%d  cmd 0x%02x  frame: 0a 01 00 %02x ...\r' \
                    "$step" "$total" "$b" "$b" >&2
                bytes=(10 1 0 $b)
                chk=$(wrish_c60a82c_checksum "${bytes[@]}")
                hex=$(wrish_c60a82c_to_gatttool_hex "${bytes[@]}" "$chk")
                echo "--CMD-$(printf '%02x' $b)--"
                echo "char-write-cmd ${ff02_handle} ${hex}"
                sleep "$delay"
            done
            printf '\n' >&2
        ) | script -q -c "gatttool -I" /dev/null 2>&1
    )

    # Parse: --CMD-XX-- marker (echoed by PTY) → track current cmd
    # Notification handle line → print match
    echo ""
    echo "=== SCAN RESULTS (0x$(printf '%02x' $from)..0x$(printf '%02x' $to)) ==="
    local current_cmd=""
    local hits=0
    while IFS= read -r line; do
        local clean
        clean=$(printf '%s' "$line" | sed 's/\x1b\[[0-9;]*m//g')
        if printf '%s' "$clean" | grep -qE -- '--CMD-[0-9a-f]{2}--'; then
            current_cmd=$(printf '%s' "$clean" \
                | sed 's/.*--CMD-\([0-9a-f][0-9a-f]\)--.*/\1/')
        fi
        if printf '%s' "$clean" | grep -qi "notification handle" && [ -n "$current_cmd" ]; then
            local hex_val
            hex_val=$(printf '%s' "$clean" \
                | sed 's/.*value: \([0-9a-f ]*\)/\1/' \
                | sed 's/[[:space:]]*$//')
            printf 'CMD 0x%s  frame: 0a 01 00 %s ...  response: %s\n' \
                "$current_cmd" "$current_cmd" "$hex_val"
            hits=$(( hits + 1 ))
        fi
    done <<< "$output"
    echo "=== ${hits} command(s) responded ==="
}

# Read battery level via vendor command on FF02, response on FF01 notification.
# Query: CMD_GET_CURRENT_POWER = 27 00 00 74
# Response: 27 01 00 [percent%] [chk] — battery % is byte[3].
# Args: [--mac <mac>]
wrish_c60a82c_battery() {
    local mac="${WRISH_MAC}"
    while [ $# -gt 0 ]; do
        case "$1" in
            --mac) mac="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; return 1 ;;
        esac
    done

    echo "[battery] checking connection to ${mac}..." >&2
    local attempt=1
    while ! wrish_gatttool_check_connected "$mac"; do
        if [ "$attempt" -ge 3 ]; then
            echo "[battery] ERROR: device ${mac} not connected after ${attempt} attempts" >&2
            return 1
        fi
        echo "[battery] not connected — power cycle attempt ${attempt}/3..." >&2
        wrish_gatttool_restart
        attempt=$(( attempt + 1 ))
    done
    echo "[battery] connected (attempt ${attempt})" >&2

    echo "[battery] resolving handles..." >&2
    local char_desc
    char_desc=$(wrish_gatttool_char_desc "$mac")

    local ff01_handle ff01_cccd ff02_handle
    ff01_handle=$(wrish_gatttool_find_handle "$char_desc" "$C60_A82C_UUID_NOTIFY")
    ff01_cccd=$(wrish_gatttool_find_cccd "$char_desc" "$ff01_handle")
    ff02_handle=$(wrish_gatttool_find_handle "$char_desc" "$C60_A82C_UUID_WRITE")

    if [ -z "$ff01_handle" ] || [ -z "$ff02_handle" ]; then
        echo "error: could not resolve FF01/FF02 handles" >&2
        return 1
    fi
    echo "[battery] FF01=${ff01_handle} CCCD=${ff01_cccd} FF02=${ff02_handle}" >&2

    local output
    output=$(
        (
            echo "connect ${mac}"
            sleep 10
            echo "char-write-req ${ff01_cccd} 01 00"
            sleep 1
            echo "char-write-cmd ${ff02_handle} 27 00 00 74"
            sleep 5
        ) | script -q -c "gatttool -I" /dev/null 2>&1
    )

    local notif_line
    notif_line=$(printf '%s\n' "$output" \
        | sed 's/\x1b\[[0-9;]*m//g' \
        | grep -i "notification" \
        | head -1)

    if [ -z "$notif_line" ]; then
        echo "No response from device for battery query" >&2
        return 1
    fi

    local hex_bytes
    hex_bytes=$(printf '%s' "$notif_line" | grep -oE 'value: [0-9a-f ]+' | sed 's/value: //' | head -1)
    echo "Battery response: ${hex_bytes}"
    local battery
    battery=$(printf '%s\n' "$hex_bytes" | awk '{print strtonum("0x" $4)}')
    if [ -n "$battery" ] && [ "$battery" -gt 0 ] 2>/dev/null; then
        echo "Battery: ${battery}%"
    fi
}

# Send a notification to the bracelet via gatttool.
# Protocol: enable notifications on FF01 CCCD, write 4-stage frames to FF02.
# ACKs arrive as notifications on FF01; captured at end of session for display.
# Args: [--mac <mac>] [--app <name>] --title <text> --body <text>
wrish_c60a82c_notify() {
    local mac="${WRISH_MAC}"
    local app_name="whatsapp"
    local title=""
    local body=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --mac)   mac="$2";      shift 2 ;;
            --app)   app_name="$2"; shift 2 ;;
            --title) title="$2";    shift 2 ;;
            --body)  body="$2";     shift 2 ;;
            *) echo "Unknown option: $1" >&2; return 1 ;;
        esac
    done

    # Build frames
    local app_type
    app_type=$(wrish_c60a82c_app_type "$app_name")
    local msg_type_bytes title_bytes body_bytes
    read -ra msg_type_bytes <<< "$(wrish_c60a82c_build_set_message_type "$app_type")"
    read -ra title_bytes    <<< "$(wrish_c60a82c_build_set_message2 1 "$title")"
    read -ra body_bytes     <<< "$(wrish_c60a82c_build_set_message2 2 "$body")"

    local msg_type_hex title_hex
    msg_type_hex=$(wrish_c60a82c_to_gatttool_hex "${msg_type_bytes[@]}")
    title_hex=$(wrish_c60a82c_to_gatttool_hex "${title_bytes[@]}")

    # Body may span multiple 20-byte chunks
    local body_chunks=() i=0 n=${#body_bytes[@]}
    while (( i < n )); do
        body_chunks+=("$(wrish_c60a82c_to_gatttool_hex "${body_bytes[@]:$i:20}")")
        (( i += 20 ))
    done

    # Connection check with up to 3 power-cycle retries
    echo "[notify] checking connection to ${mac}..." >&2
    local attempt=1
    while ! wrish_gatttool_check_connected "$mac"; do
        if [ "$attempt" -ge 3 ]; then
            echo "[notify] ERROR: device ${mac} not connected after ${attempt} attempts" >&2
            return 1
        fi
        echo "[notify] not connected — power cycle attempt ${attempt}/3..." >&2
        wrish_gatttool_restart
        attempt=$(( attempt + 1 ))
    done
    echo "[notify] connected (attempt ${attempt})" >&2

    # Resolve GATT handles
    echo "[notify] resolving handles..." >&2
    local char_desc
    char_desc=$(wrish_gatttool_char_desc "$mac")
    echo "[notify] char-desc output:" >&2
    wrish_gatttool_dump_session "$char_desc" "[char-desc]"

    local ff01_handle ff01_cccd ff02_handle
    ff01_handle=$(wrish_gatttool_find_handle "$char_desc" "$C60_A82C_UUID_NOTIFY")
    ff01_cccd=$(wrish_gatttool_find_cccd   "$char_desc" "$ff01_handle")
    ff02_handle=$(wrish_gatttool_find_handle "$char_desc" "$C60_A82C_UUID_WRITE")

    if [ -z "$ff01_handle" ] || [ -z "$ff02_handle" ]; then
        echo "[notify] ERROR: could not resolve FF01/FF02 handles" >&2
        return 1
    fi
    echo "[notify] FF01=${ff01_handle} CCCD=${ff01_cccd} FF02=${ff02_handle}" >&2
    echo "[notify] frames:" >&2
    echo "[notify]   stage0 (setMessageType): ${msg_type_hex}" >&2
    echo "[notify]   stage1 (title):          ${title_hex}" >&2
    for i in "${!body_chunks[@]}"; do
        echo "[notify]   stage2 (body chunk $i):   ${body_chunks[$i]}" >&2
    done
    echo "[notify]   stage3 (END_MESSAGE):    0a 01 00 03 0e" >&2

    # One session: connect → enable notify → stage0..3.
    # Use char-write-req for FF02: gatttool waits for the GATT Write Response
    # before reading the next command, enforcing true stage sequencing.
    # ACK notifications arrive on FF01 (pattern: 8A 02 00 <stage> 04 <chk>).
    echo "[notify] starting session..." >&2
    local output
    output=$(
        (
            echo "connect ${mac}"
            sleep 10
            # Enable notifications on FF01 CCCD; wait for Write Response
            echo "char-write-req ${ff01_cccd} 01 00"
            sleep 2

            # Stage 0: setMessageType
            echo "char-write-req ${ff02_handle} ${msg_type_hex}"
            sleep 5

            # Stage 1: title
            echo "char-write-req ${ff02_handle} ${title_hex}"
            sleep 5

            # Stage 2: body chunks
            local chunk
            for chunk in "${body_chunks[@]}"; do
                echo "char-write-req ${ff02_handle} ${chunk}"
                sleep 2
            done
            sleep 5

            # Stage 3: END_MESSAGE — extra wait for final ACK
            echo "char-write-req ${ff02_handle} 0a 01 00 03 0e"
            sleep 15
        ) | script -q -c "gatttool -I" /dev/null 2>&1
    )

    # Dump full session output
    echo "[notify] full session output:" >&2
    wrish_gatttool_dump_session "$output" "[session]"

    # Parse ACK notifications (start with 8a, stage in byte[3])
    local stage_names=("setMessageType" "title" "body" "END_MESSAGE")
    local ack_found=0
    while IFS= read -r line; do
        local hex_val
        hex_val=$(printf '%s' "$line" | sed 's/\x1b\[[0-9;]*m//g' | grep -oE 'value: [0-9a-f ]+' | sed 's/value: //')
        [ -z "$hex_val" ] && continue
        local first_byte stage_byte
        first_byte=$(printf '%s\n' "$hex_val" | awk '{print $1}')
        stage_byte=$(printf '%s\n' "$hex_val" | awk '{print $4}')
        if [ "$first_byte" = "8a" ]; then
            local stage_idx
            stage_idx=$(( 16#${stage_byte:-ff} ))
            local stage_name="${stage_names[$stage_idx]:-stage${stage_idx}}"
            echo "[notify] ACK stage ${stage_idx} (${stage_name}): ${hex_val}" >&2
            ack_found=$(( ack_found + 1 ))
        fi
    done < <(printf '%s\n' "$output" \
        | sed 's/\x1b\[[0-9;]*m//g' \
        | grep -i "notification")

    if [ "$ack_found" -eq 0 ]; then
        echo "[notify] WARNING: no ACK notifications received" >&2
    else
        echo "[notify] done (${ack_found}/4 ACKs)" >&2
    fi
}

# Subscribe to heart rate notifications via gatttool. Args: [--mac <mac>] [--duration <secs>]
wrish_c60a82c_heart_rate_monitor() {
    local mac="${WRISH_MAC}"
    local duration=30

    while [ $# -gt 0 ]; do
        case "$1" in
            --mac)      mac="$2";      shift 2 ;;
            --duration) duration="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; return 1 ;;
        esac
    done

    echo "[heart-rate] resolving handles..." >&2
    local char_desc
    char_desc=$(wrish_gatttool_char_desc "$mac")

    local hr_handle hr_cccd
    hr_handle=$(wrish_gatttool_find_handle "$char_desc" "$C60_A82C_UUID_HR")
    hr_cccd=$(wrish_gatttool_find_cccd "$char_desc" "$hr_handle")

    if [ -z "$hr_handle" ]; then
        echo "error: could not resolve HR handle" >&2
        return 1
    fi
    echo "[heart-rate] HR=${hr_handle} CCCD=${hr_cccd} duration=${duration}s" >&2

    local output
    output=$(
        (
            echo "connect ${mac}"
            sleep 10
            echo "char-write-req ${hr_cccd} 01 00"
            sleep "$duration"
        ) | script -q -c "gatttool -I" /dev/null 2>&1
    )

    printf '%s\n' "$output" \
        | sed 's/\x1b\[[0-9;]*m//g' \
        | grep -i "notification" \
        | while IFS= read -r line; do
            local raw bpm
            raw=$(printf '%s' "$line" | grep -oE 'value: [0-9a-f ]+' | sed 's/value: //' | head -1)
            # GATT 0x2A37: byte[0]=flags, byte[1]=bpm (uint8 format when flags bit0=0)
            bpm=$(printf '%d' "0x$(printf '%s\n' "$raw" | awk '{print $2}')" 2>/dev/null) || continue
            echo "Heart rate: ${bpm} bpm"
        done
}
