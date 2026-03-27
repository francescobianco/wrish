
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

    echo "[notify] device: ${mac}" >&2
    wrish_gatt_info "$mac"

    python3 - --app "$app_name" --title "$title" --body "$body" --mac "$mac" << 'PYEOF'
import argparse, sys, time, dbus, dbus.mainloop.glib
from gi.repository import GLib

APP_TYPES = {
    "wechat":2,"qq":3,"facebook":4,"skype":5,"twitter":6,"whatsapp":7,
    "line":8,"linkedin":9,"instagram":10,"messenger":12,"vk":13,"viber":14,
    "telegram":16,"kakaotalk":18,"douyin":32,"kuaishou":33,"douyin_lite":34,
    "maimai":52,"pinduoduo":53,"work_wechat":54,"tantan":56,"taobao":57,
}

def ck(bs): s=sum(bs)&0xFF; return ((s*0x56)+0x5A)&0xFF
def f_type(t): bs=[0x0A,0x02,0x00,0x00,t]; return bs+[ck(bs)]
def f_msg(k,text,mx):
    tb=list(text[:mx].encode("utf-8")); p=1+len(tb)
    bs=[0x0A,p&0xFF,(p>>8)&0xFF,k]+tb; return bs+[ck(bs)]
END=[0x0A,0x01,0x00,0x03,0x0E]

parser=argparse.ArgumentParser()
parser.add_argument("--app",default="whatsapp")
parser.add_argument("--title",default="")
parser.add_argument("--body",default="")
parser.add_argument("--mac",default="A4:C1:38:9A:A8:2C")
args=parser.parse_args()

mac=args.mac.replace(":","_")
dev_path=f"/org/bluez/hci0/dev_{mac}"
ff01_path=dev_path+"/service000c/char000d"
ff02_path=dev_path+"/service000c/char0010"

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus=dbus.SystemBus()
dev=bus.get_object("org.bluez",dev_path)
dev_props=dbus.Interface(dev,"org.freedesktop.DBus.Properties")

if not dev_props.Get("org.bluez.Device1","Connected"):
    print("[notify] connecting...",file=sys.stderr)
    dbus.Interface(dev,"org.bluez.Device1").Connect()
    for _ in range(20):
        time.sleep(0.5)
        if dev_props.Get("org.bluez.Device1","Connected"): break
    else:
        print("[notify] ERROR: could not connect",file=sys.stderr); sys.exit(1)

ff01_iface=dbus.Interface(bus.get_object("org.bluez",ff01_path),"org.bluez.GattCharacteristic1")
ff02_iface=dbus.Interface(bus.get_object("org.bluez",ff02_path),"org.bluez.GattCharacteristic1")

frames=[f_type(APP_TYPES.get(args.app.lower(),7)),f_msg(1,args.title,32),f_msg(2,args.body,128),END]
acks={}
loop=GLib.MainLoop()

def on_chg(iface,changed,inv,path=None):
    if "Value" not in changed or "char000d" not in str(path or""): return
    val=list(changed["Value"])
    if len(val)>=4 and val[0]==0x8A: acks[val[3]]=val

bus.add_signal_receiver(on_chg,signal_name="PropertiesChanged",
    dbus_interface="org.freedesktop.DBus.Properties",path_keyword="path")

def run():
    ff01_iface.StartNotify(); time.sleep(0.3)
    for stage,frame in enumerate(frames):
        for i in range(0,len(frame),20):
            ff02_iface.WriteValue(dbus.Array([dbus.Byte(b) for b in frame[i:i+20]],signature="y"),{})
            time.sleep(0.2)
        deadline=time.time()+8
        while stage not in acks and time.time()<deadline:
            GLib.MainContext.default().iteration(False); time.sleep(0.05)
        print(f"[notify] stage {stage} {'OK' if stage in acks else 'no ACK'}",file=sys.stderr)
    ff01_iface.StopNotify(); loop.quit()

GLib.timeout_add(200,run); loop.run()
sys.exit(0 if len(acks)==4 else 1)
PYEOF
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