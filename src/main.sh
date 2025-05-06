
module bluetooth

main() {

  WRISH_DEVICE=ID115
  WRISH_MAC=BA:03:5C:0B:21:E1

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
      connect)
        wrish_bluetooth_connect "$WRISH_MAC"
        ;;
      login)
        mydev_login_host "$hosts" "$2"
        ;;
    esac



#
}
