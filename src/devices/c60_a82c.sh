
module c60_a82c

C60_A82C_MAC="A4:C1:38:9A:A8:2C"
C60_A82C_UUID_WRITE="0000ff02-0000-1000-8000-00805f9b34fb"
C60_A82C_UUID_NOTIFY="0000ff01-0000-1000-8000-00805f9b34fb"
C60_A82C_UUID_HR="00002a37-0000-1000-8000-00805f9b34fb"
C60_A82C_HANDLE_WRITE="0x0011"
C60_A82C_HANDLE_NOTIFY_CCCD="0x000f"
C60_A82C_HANDLE_HR="0x0014"
C60_A82C_HANDLE_HR_CCCD="0x0015"

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

# Output space-separated decimal bytes for setMessageType frame
# Args: <app_type_decimal>
wrish_c60a82c_build_set_message_type() {
    local app_type
    local bytes
    local chk
    app_type="$1"
    bytes=(10 2 0 0 "$app_type")
    chk=$(wrish_c60a82c_checksum "${bytes[@]}")
    echo "${bytes[*]} $chk"
}

# Output space-separated decimal bytes for setMessage2 frame (title or body)
# Args: <kind> <text>  — kind=1 for title (max 32 bytes), kind=2 for body (max 128 bytes)
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

# Output bluetoothctl write commands for a byte array, split into 20-byte chunks
# Args: decimal byte values
wrish_c60a82c_write_cmds() {
    local bytes
    local chunk_size
    local i
    local n
    local chunk
    local line
    local b
    bytes=("$@")
    chunk_size=20
    i=0
    n=${#bytes[@]}
    while (( i < n )); do
        chunk=("${bytes[@]:$i:$chunk_size}")
        line=""
        for b in "${chunk[@]}"; do
            [ -n "$line" ] && line+=" "
            line+="$(printf '0x%02X' $(( b )))"
        done
        echo "write \"${line}\""
        sleep 1
        (( i += chunk_size ))
    done
}

# Map app name (case-insensitive) to bracelet app type code
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

# Convert decimal byte array to bluetoothctl hex string "0xXX 0xXX ..."
# Args: decimal byte values
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

# Send a notification to the bracelet using the GATT wrapper (with ACK handling)
# Args: [--mac <mac>] [--app <app_name>] --title <title> --body <body>
wrish_c60a82c_notify() {
    local mac
    local app_name
    local title
    local body
    local app_type
    local msg_type_bytes
    local title_bytes
    local body_bytes
    local hex_type
    local hex_title
    local hex_body
    mac="$C60_A82C_MAC"
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

    echo "[notify] checking device info..." >&2
    wrish_gatt_info "$mac"

    app_type=$(wrish_c60a82c_app_type "$app_name")
    read -ra msg_type_bytes <<< "$(wrish_c60a82c_build_set_message_type "$app_type")"
    read -ra title_bytes    <<< "$(wrish_c60a82c_build_set_message2 1 "$title")"
    read -ra body_bytes     <<< "$(wrish_c60a82c_build_set_message2 2 "$body")"

    hex_type=$(wrish_c60a82c_to_hex_str "${msg_type_bytes[@]}")
    hex_title=$(wrish_c60a82c_to_hex_str "${title_bytes[@]}")
    hex_body=$(wrish_c60a82c_to_hex_str "${body_bytes[@]}")

    wrish_gatt_open "$mac" || return 1

    wrish_gatt_select "$C60_A82C_UUID_NOTIFY"
    wrish_gatt_notify_on

    wrish_gatt_select "$C60_A82C_UUID_WRITE"

    echo "[notify] → setMessageType (${app_name})" >&2
    wrish_gatt_write_wait_ack "$hex_type" 0

    echo "[notify] → title" >&2
    wrish_gatt_write_wait_ack "$hex_title" 1

    echo "[notify] → body" >&2
    # body may be split into chunks
    local i
    local n
    local chunk_bytes
    local chunk_hex
    local chunk_size
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

    echo "[notify] → END_MESSAGE" >&2
    wrish_gatt_write_wait_ack "0x0A 0x01 0x00 0x03 0x0E" 3

    wrish_gatt_close
    echo "[notify] done" >&2
}

# Subscribe to heart rate notifications (listens for 30 s by default)
# Args: [--mac <mac>] [--duration <seconds>]
wrish_c60a82c_heart_rate_monitor() {
    local mac
    local duration
    mac="$C60_A82C_MAC"
    duration=30
    while [ $# -gt 0 ]; do
        case "$1" in
            --mac)      mac="$2";      shift 2 ;;
            --duration) duration="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; return 1 ;;
        esac
    done
    {
        echo "connect ${mac}"
        sleep 3
        echo "menu gatt"
        echo "select-attribute ${C60_A82C_UUID_HR}"
        echo "notify on"
        sleep "$duration"
        echo "back"
        echo "disconnect ${mac}"
        echo "exit"
    } | bluetoothctl
}