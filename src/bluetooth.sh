

wrish_bluetooth_list() {


    echo -e 'devices\nexit' | bluetoothctl | grep Device | awk '{print "[" $2 "] " $3}'

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