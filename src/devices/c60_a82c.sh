
module c60_a82c

# Protocol UUIDs — constants of this device model, not per-device config
C60_A82C_UUID_WRITE="0000ff02-0000-1000-8000-00805f9b34fb"
C60_A82C_UUID_NOTIFY="0000ff01-0000-1000-8000-00805f9b34fb"
C60_A82C_UUID_HR="00002a37-0000-1000-8000-00805f9b34fb"
C60_A82C_UUID_DEVICE_NAME="00002a00-0000-1000-8000-00805f9b34fb"

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

# Read device name from GATT 0x2A00 via D-Bus ObjectManager
# Args: <mac>
wrish_c60a82c_info() {
    local mac
    mac="${1:-${WRISH_MAC}}"

    wrish_gatt_info "$mac"

    python3 - "$mac" << 'EOF'
import sys, dbus

mac = sys.argv[1].replace(":", "_")
dev_path = f"/org/bluez/hci0/dev_{mac}"

bus = dbus.SystemBus()
mgr = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
objects = mgr.GetManagedObjects()

for path, ifaces in objects.items():
    if "org.bluez.GattCharacteristic1" not in ifaces:
        continue
    if dev_path not in str(path):
        continue
    uuid = str(ifaces["org.bluez.GattCharacteristic1"].get("UUID", ""))
    if "00002a00" not in uuid:
        continue
    char = dbus.Interface(bus.get_object("org.bluez", path), "org.bluez.GattCharacteristic1")
    val = bytes(char.ReadValue({})).rstrip(b"\x00").decode("ascii", errors="replace")
    print(f"Device Name (0x2A00): {val}")
    sys.exit(0)

print("0x2A00 not found or not readable", file=sys.stderr)
sys.exit(1)
EOF
}

# Send a notification to the bracelet via D-Bus (with ACK handshake on FF01)
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

    python3 "${WRISH_NOTIFY_PY:-$(dirname "$(readlink -f "$0")")/notify.py}" \
        --mac "$mac" --app "$app_name" --title "$title" --body "$body"
}

# Subscribe to heart rate notifications (listens for 30 s by default)
# Args: [--mac <mac>] [--duration <seconds>]
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
