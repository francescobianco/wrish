

wrish_bluetooth_scan() {
    rfkill unblock all
    {
        echo "scan on"
        sleep "${1:-10}"
        echo "scan off"
        echo "exit"
    } | bluetoothctl > /dev/null 2>&1
    wrish_bluetooth_list
}

wrish_bluetooth_list() {


    echo -e 'devices\nexit' | bluetoothctl | grep -E 'Device ([0-9A-F]{2}:){5}[0-9A-F]{2}' | awk '{print "[" $2 "] " $3}'

}

wrish_bluetooth_info() {
  local wrish_mac

  wrish_mac="$1"

  echo -e "info ${wrish_mac}\nexit" | bluetoothctl



  echo ""



  wrish_bluetooth_list_attributes "$wrish_mac" | while read -r line; do
    local attribute
    local value

    attribute=$(echo "$line" | awk '{print $1}')
    value=$(wrish_bluetooth_read_attribute "$attribute")

    echo -e "${attribute}: ${value}"

    sleep 5
  done

  #
}

wrish_bluetooth_list_attributes() {
  local wrish_mac

  wrish_mac="$1"


  echo -e "connect ${wrish_mac}\nmenu gatt\nlist-attributes ${wrish_mac}\nexit" | bluetoothctl | grep -P '^\t0000' | awk '{$1=$1; print}'
}

wrish_bluetooth_read_attribute() {
  local attribute

  attribute="$1"
  {
    echo -e "menu gatt"
    echo -e "attribute-info ${attribute}"
    echo -e "select-attribute ${attribute}"
    echo -e "read"
    sleep 5
    echo -e "exit"
  } | bluetoothctl

}

# Read all readable GATT characteristics on a device using the -- sentinel trick.
# For each characteristic: select-attribute → "--" → read → "--"
# The unknown command "--" produces an error line that isolates each read's output.
# Args: <mac>
wrish_bluetooth_deep_read() {
    local mac
    local raw_mode=0
    local raw_attrs
    local uuids
    local uuid
    local output
    local stripped
    local chunk
    local idx

    while [ $# -gt 0 ]; do
        case "$1" in
            --raw) raw_mode=1 ;;
            *) mac="$1" ;;
        esac
        shift
    done

    echo "[deep-read] listing attributes on ${mac}..." >&2

    # Step 1: collect all characteristic UUIDs in order
    raw_attrs=$(
        {
            echo "connect ${mac}"
            sleep 4
            echo "menu gatt"
            echo "list-attributes"
            sleep 2
            echo "exit"
        } | bluetoothctl 2>/dev/null \
          | sed 's/\x1b\[[0-9;]*m//g' \
          | grep -E $'^\t[0-9a-f]{8}-[0-9a-f]{4}' \
          | awk '{print $1}'
    )

    if [ -z "$raw_attrs" ]; then
        echo "[deep-read] no attributes found" >&2
        return 1
    fi

    mapfile -t uuids <<< "$raw_attrs"
    echo "[deep-read] found ${#uuids[@]} attributes" >&2

    # Step 2: one session per UUID using three named sentinels:
    #   --HEAD--  : before select-attribute (captures old prompt / context)
    #   --START-- : after select-attribute, before read (captures new prompt / attr path)
    #   --END--   : after read + sleep (captures the async value that arrives late)
    #
    # Real output order observed: read → second_sentinel → [VALUE] → next_select
    # So value is collected between --START-- and --END--, after read completes.
    output=$(
        {
            echo "connect ${mac}"
            sleep 4
            echo "menu gatt"
            for uuid in "${uuids[@]}"; do
                echo "--HEAD--"
                echo "select-attribute ${uuid}"
                echo "--START--"
                echo "read"
                sleep 1
                echo "--END--"
            done
            echo "exit"
        } | bluetoothctl 2>&1
    )

    if [ "$raw_mode" -eq 1 ]; then
        echo "$output"
        return 0
    fi

    # Step 3: parse using HEAD/START/END sentinels.
    # state=idle → HEAD sentinel → state=head (collect attr path from prompt)
    #           → START sentinel → state=read (collect raw read output)
    #           → END sentinel   → print result, state=idle
    local state="idle"
    local head_buf=""
    local read_buf=""
    local attr_path=""
    local uuid_idx=0

    while IFS= read -r line; do
        # Strip ANSI from this line for matching, but keep raw for buffers
        local clean
        clean=$(printf '%s' "$line" | sed 's/\x1b\[[0-9;]*m//g')

        if echo "$clean" | grep -q "Invalid command.*--HEAD--\|Unknown command.*--HEAD--"; then
            state="head"
            head_buf=""
            attr_path=""
        elif echo "$clean" | grep -q "Invalid command.*--START--\|Unknown command.*--START--"; then
            # Extract attribute path from last prompt line seen in head_buf
            attr_path=$(printf '%s\n' "$head_buf" \
                | grep -oE '\[C60-A82C:[^]]+\]' | tail -1 \
                | tr -d '[]')
            state="read"
            read_buf=""
        elif echo "$clean" | grep -q "Invalid command.*--END--\|Unknown command.*--END--"; then
            state="idle"
            local current_uuid="${uuids[$uuid_idx]:-?}"
            uuid_idx=$(( uuid_idx + 1 ))
            echo ""
            echo "UUID: ${current_uuid}"
            [ -n "$attr_path" ] && echo "Path: ${attr_path}"
            echo "--START--"
            printf '%s\n' "$read_buf"
            echo "--END--"
        elif [ "$state" = "head" ]; then
            head_buf+="${line}"$'\n'
        elif [ "$state" = "read" ]; then
            read_buf+="${line}"$'\n'
        fi
    done <<< "$output"
}

wrish_bluetooth_connect() {
  local wrish_mac
  local interface
  local interface_status

  interface=$(hciconfig | grep -o 'hci[0-9]*')
  interface_status=$(hciconfig "${interface}" | grep -o 'UP' | wc -l)

  if [ "$interface_status" -eq 0 ]; then
    echo "Bluetooth interface ${interface} is down. Type 'sudo hciconfig ${interface} up' to bring it up."
    exit 1
  fi

  echo "Connecting to ${interface}..."

  wrish_mac="$1"

  {
    echo "disconnect ${wrish_mac}"
    sleep 10
    echo "power off"
    sleep 10
    echo "power on"
    sleep 10
    echo "connect ${wrish_mac}"
    sleep 10
    echo "info ${wrish_mac}"
    sleep 10
    echo "exit"
  } | bluetoothctl
}

wrist_bluetooth_power() {
  {
    echo "power $1"
    echo "exit"
  } | bluetoothctl
}