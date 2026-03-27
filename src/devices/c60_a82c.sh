
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

# Convert decimal byte array to bluetoothctl quoted hex string "0xXX 0xXX ..."
wrish_c60a82c_to_hex_str() {
    local result
    local b
    result=""
    for b in "$@"; do
        [ -n "$result" ] && result+=" "
        result+="$(printf '0x%02X' $(( b )))"
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
        | grep -oP 'value: \K[0-9a-f ]+' \
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

# Read battery level via vendor command on FF02, response on FF01.
# The C60-A82C responds to command 0x03 0x01 0x00 0x00 <ck> with battery % in byte[4].
# Args: [--mac <mac>]
wrish_c60a82c_battery() {
    local mac
    mac="${WRISH_MAC}"
    while [ $# -gt 0 ]; do
        case "$1" in
            --mac) mac="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; return 1 ;;
        esac
    done

    wrish_gatt_open "$mac" || return 1

    wrish_gatt_select "$C60_A82C_UUID_NOTIFY"
    wrish_gatt_notify_on

    wrish_gatt_select "$C60_A82C_UUID_WRITE"

    # Battery query command: 03 01 00 00 <checksum>
    # checksum([3,1,0,0]) = ((4*0x56)+0x5A)&0xFF = 0xB2
    wrish_gatt_send 'write "0x03 0x01 0x00 0x00 0xB2" 0 command'

    # Response on FF01: 83 02 00 <battery%> ...  (common pattern for keephealth devices)
    local response
    response=$(wrish_gatt_wait 'Value:' 8)

    wrish_gatt_close

    if [ -n "$response" ]; then
        # Extract the value bytes from the CHG line
        local value_hex
        value_hex=$(echo "$response" | grep -oP 'Value: \K[\da-f ]+' | head -1)
        echo "Battery response: ${value_hex}"
        # Battery % is typically in byte index 3 (0-indexed) for keephealth protocol
        local battery
        battery=$(echo "$value_hex" | awk '{print strtonum("0x" $4)}')
        if [ -n "$battery" ] && [ "$battery" -gt 0 ] 2>/dev/null; then
            echo "Battery: ${battery}%"
        fi
    else
        echo "No response from device for battery query" >&2
        return 1
    fi
}

# Send a notification to the bracelet via GATT coproc session with ACK waiting.
# Args: [--mac <mac>] [--app <name>] --title <text> --body <text>
wrish_c60a82c_notify() {
    local mac
    local app_name
    local title
    local body
    mac="${WRISH_MAC}"
    app_name="whatsapp"
    title=""
    body=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --mac)   mac="$2";      shift 2 ;;
            --app)   app_name="$2"; shift 2 ;;
            --title) title="$2";    shift 2 ;;
            --body)  body="$2";     shift 2 ;;
            *) echo "Unknown option: $1" >&2; return 1 ;;
        esac
    done

    local app_type
    local msg_type_bytes
    local title_bytes
    local body_bytes
    local hex

    app_type=$(wrish_c60a82c_app_type "$app_name")
    read -ra msg_type_bytes <<< "$(wrish_c60a82c_build_set_message_type "$app_type")"
    read -ra title_bytes    <<< "$(wrish_c60a82c_build_set_message2 1 "$title")"
    read -ra body_bytes     <<< "$(wrish_c60a82c_build_set_message2 2 "$body")"

    wrish_gatt_open "$mac" || return 1

    wrish_gatt_select "$C60_A82C_UUID_NOTIFY"
    wrish_gatt_notify_on
    wrish_gatt_select "$C60_A82C_UUID_WRITE"

    # Stage 0: setMessageType — wait for ACK 8A xx xx 00 xx xx
    hex=$(wrish_c60a82c_to_hex_str "${msg_type_bytes[@]}")
    echo "[notify] setMessageType (${app_name})" >&2
    wrish_gatt_write_wait_ack "$hex" 0

    # Stage 1: title
    hex=$(wrish_c60a82c_to_hex_str "${title_bytes[@]}")
    echo "[notify] title" >&2
    wrish_gatt_write_wait_ack "$hex" 1

    # Stage 2: body (may span multiple 20-byte chunks)
    echo "[notify] body" >&2
    local i n chunk_bytes chunk_hex chunk_size
    chunk_size=20
    i=0
    n=${#body_bytes[@]}
    while (( i < n )); do
        chunk_bytes=("${body_bytes[@]:$i:$chunk_size}")
        chunk_hex=$(wrish_c60a82c_to_hex_str "${chunk_bytes[@]}")
        if (( i + chunk_size >= n )); then
            wrish_gatt_write_wait_ack "$chunk_hex" 2
        else
            wrish_gatt_write_wait_ack "$chunk_hex"
        fi
        (( i += chunk_size ))
    done

    # Stage 3: END_MESSAGE
    echo "[notify] END_MESSAGE" >&2
    wrish_gatt_write_wait_ack "0x0A 0x01 0x00 0x03 0x0E" 3

    wrish_gatt_close
    echo "[notify] done" >&2
}

# Subscribe to heart rate notifications. Args: [--mac <mac>] [--duration <secs>]
wrish_c60a82c_heart_rate_monitor() {
    local mac
    local duration
    mac="${WRISH_MAC}"
    duration=30

    while [ $# -gt 0 ]; do
        case "$1" in
            --mac)      mac="$2";      shift 2 ;;
            --duration) duration="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; return 1 ;;
        esac
    done

    wrish_gatt_open "$mac" || return 1
    wrish_gatt_select "$C60_A82C_UUID_HR"
    wrish_gatt_notify_on
    wrish_gatt_wait 'Value:' "$duration" | grep -oP 'Value: \K[\da-f ]+' | while read -r raw; do
        local bpm
        bpm=$(printf '%d' "0x$(echo "$raw" | awk '{print $2}')" 2>/dev/null) || continue
        echo "Heart rate: ${bpm} bpm"
    done
    wrish_gatt_close
}
