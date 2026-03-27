
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
