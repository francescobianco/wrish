#/bin/bash

source .wrishrc

mac=$WRISH_MAC
attribute=00001801-0000-1000-8000-00805f9b34fb

{
        echo "connect ${mac}"
        sleep 3
        echo "menu gatt"
        echo "list-attributes"
        echo "select-attribute ${attribute}"
        sleep 1
        echo "--"
        echo "read"
        echo "--"
        sleep 1
        echo "exit"
    } | bluetoothctl