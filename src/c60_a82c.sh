
module c60_a82c

C60_A82C_MAC="A4:C1:38:9A:A8:2C"
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
        line="write"
        for b in "${chunk[@]}"; do
            line+=" $(printf '0x%02X' $(( b )))"
        done
        echo "$line"
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

# Send a notification to the bracelet
# Args: [--mac <mac>] [--app <app_name>] --title <title> --body <body>
wrish_c60a82c_notify() {
    local mac
    local app_name
    local title
    local body
    local app_type
    local msg_type_frame
    local title_frame
    local body_frame
    local msg_type_bytes
    local title_bytes
    local body_bytes
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
    app_type=$(wrish_c60a82c_app_type "$app_name")
    msg_type_frame=$(wrish_c60a82c_build_set_message_type "$app_type")
    title_frame=$(wrish_c60a82c_build_set_message2 1 "$title")
    body_frame=$(wrish_c60a82c_build_set_message2 2 "$body")
    read -ra msg_type_bytes <<< "$msg_type_frame"
    read -ra title_bytes    <<< "$title_frame"
    read -ra body_bytes     <<< "$body_frame"
    {
        echo "connect ${mac}"
        sleep 3
        echo "menu gatt"
        echo "select-attribute ${C60_A82C_UUID_NOTIFY}"
        echo "notify on"
        sleep 1
        echo "select-attribute ${C60_A82C_UUID_WRITE}"
        wrish_c60a82c_write_cmds "${msg_type_bytes[@]}"
        sleep 2
        wrish_c60a82c_write_cmds "${title_bytes[@]}"
        sleep 2
        wrish_c60a82c_write_cmds "${body_bytes[@]}"
        sleep 2
        echo "write 0x0A 0x01 0x00 0x03 0x0E"
        sleep 2
        echo "back"
        echo "disconnect ${mac}"
        echo "exit"
    } | bluetoothctl
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