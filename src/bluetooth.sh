

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
    local raw_attrs
    local uuids
    local uuid
    local output
    local stripped
    local chunk
    local idx
    mac="$1"

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

    # Step 2: one session — for each UUID: select, --, read, --, sleep
    output=$(
        {
            echo "connect ${mac}"
            sleep 4
            echo "menu gatt"
            for uuid in "${uuids[@]}"; do
                echo "select-attribute ${uuid}"
                echo "--"
                echo "read"
                echo "--"
                sleep 1
            done
            echo "exit"
        } | bluetoothctl 2>&1
    )

    # Step 3: strip ANSI codes, then split on sentinel lines.
    # In the GATT submenu, an unknown command like "--" produces:
    #   "Invalid command in menu gatt: --"  (or "Unknown command: --" at top level)
    # We match both forms as sentinels around each read's output.
    stripped=$(echo "$output" | sed 's/\x1b\[[0-9;]*m//g')

    idx=0
    local sentinel_count
    sentinel_count=0
    local in_read=0
    local read_buf=""
    local uuid_idx=0

    while IFS= read -r line; do
        if echo "$line" | grep -qE "(Unknown|Invalid) command"; then
            sentinel_count=$(( sentinel_count + 1 ))
            if (( sentinel_count % 2 == 1 )); then
                # odd sentinel = start of read block
                in_read=1
                read_buf=""
            else
                # even sentinel = end of read block
                in_read=0
                local current_uuid="${uuids[$uuid_idx]:-?}"
                uuid_idx=$(( uuid_idx + 1 ))
                # Print result for this UUID
                local value
                value=$(echo "$read_buf" | grep -oE 'Value: [0-9a-f ]+' | head -1 | sed 's/Value: //')
                local read_err
                read_err=$(echo "$read_buf" | grep -i 'failed\|error\|not permitted' | head -1 | sed 's/^[[:space:]]*//')
                echo ""
                echo "UUID: ${current_uuid}"
                if [ -n "$value" ]; then
                    echo "  Value (hex): ${value}"
                    # Try ASCII decode
                    local ascii
                    ascii=$(echo "$value" | tr ' ' '\n' | while read -r h; do printf "\\x${h}"; done 2>/dev/null | tr -cd '[:print:]')
                    [ -n "$ascii" ] && echo "  Value (ascii): ${ascii}"
                elif [ -n "$read_err" ]; then
                    echo "  Error: ${read_err}"
                else
                    echo "  (no value returned)"
                fi
            fi
        elif [ "$in_read" -eq 1 ]; then
            read_buf+="${line}"$'\n'
        fi
    done <<< "$stripped"
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