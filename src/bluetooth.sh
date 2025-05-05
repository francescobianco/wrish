

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