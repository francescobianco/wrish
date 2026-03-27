
module bluetooth
module gatt
module devices

main() {

  WRISH_DEVICE=C60-A82C
  WRISH_MAC=A4:C1:38:9A:A8:2C

  local list
  local info

    while [ $# -gt 0 ]; do
      case "$1" in
        -*)
          case "$1" in
            --help|-h)
              echo "wrish — wristband control CLI"
              echo ""
              echo "USAGE:"
              echo "  wrish [OPTIONS] <COMMAND>"
              echo ""
              echo "OPTIONS:"
              echo "  --list        List all known Bluetooth devices"
              echo "  --info        Show info and attributes of the connected device"
              echo "  -h, --help    Show this help message"
              echo ""
              echo "COMMANDS:"
              echo "  scan [secs]               Scan for nearby BT devices (default 10s)"
              echo "  connect                   Connect to the configured device (${WRISH_DEVICE})"
              echo "  notify [OPTIONS]          Send a notification to the bracelet"
              echo "    --app <name>            App type (whatsapp, telegram, instagram, ...)"
              echo "    --title <text>          Notification title (max 32 chars)"
              echo "    --body <text>           Notification body (max 128 chars)"
              echo "  heart-rate [OPTIONS]      Monitor heart rate"
              echo "    --duration <secs>       Listen duration (default 30s)"
              echo ""
              echo "DEVICE:"
              echo "  ${WRISH_DEVICE} — ${WRISH_MAC}"
              exit 0
              ;;
            --list)
              list=true
              shift
              ;;
            --info)
              info=true
              shift
              ;;
            -o|--output)
              echo "Handling $1 with value: $2"
              shift
              ;;
            *)
              echo "Unknown option: $1" >&2
              exit 1
              ;;
        esac
          ;;
        *)
          break
          ;;
      esac
      shift
    done || true

    if [ -n "$list" ]; then
      wrish_bluetooth_list
      exit
    fi

    if [ -n "$info" ]; then
      wrish_bluetooth_info "$WRISH_MAC"
      exit
    fi

    if [ "$#" -eq 0 ]; then
      echo "No arguments supplied"
    fi

    case "$1" in
      scan)
        shift
        wrish_bluetooth_scan "$@"
        ;;
      connect)
        wrish_bluetooth_connect "$WRISH_MAC"
        ;;
      login)
        mydev_login_host "$hosts" "$2"
        ;;
      notify)
        shift
        wrish_c60a82c_notify "$@"
        ;;
      heart-rate)
        shift
        wrish_c60a82c_heart_rate_monitor "$@"
        ;;
    esac



#
}
