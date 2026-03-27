
module bluetooth
module gatt
module devices
module router

_wrish_load_rc() {
  local rc
  local key
  local value

  if [ -f ".wrishrc" ]; then
    rc=".wrishrc"
  elif [ -f "${HOME}/.wrishrc" ]; then
    rc="${HOME}/.wrishrc"
  else
    return
  fi

  while IFS='=' read -r key value; do
    # skip comments and empty lines
    case "$key" in
      \#*|"") continue ;;
    esac
    # only import WRISH_* variables
    case "$key" in
      WRISH_*) export "$key"="${value}" ;;
    esac
  done < "$rc"
}

main() {

  _wrish_load_rc

  WRISH_DEVICE="${WRISH_DEVICE:-C60-A82C}"
  WRISH_MAC="${WRISH_MAC:-A4:C1:38:9A:A8:2C}"

  local list

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
              echo "  -h, --help    Show this help message"
              echo ""
              echo "COMMANDS:"
              echo "  scan [secs]               Scan for nearby BT devices (default 10s)"
              echo "  connect                   Connect to the configured device"
              echo "  info                      Read device info (name, model from GATT 0x2A00)"
              echo "  notify [OPTIONS]          Send a notification to the bracelet"
              echo "    --app <name>            App type (whatsapp, telegram, instagram, ...)"
              echo "    --title <text>          Notification title (max 32 chars)"
              echo "    --body <text>           Notification body (max 128 chars)"
              echo "  heart-rate [OPTIONS]      Monitor heart rate"
              echo "    --duration <secs>       Listen duration (default 30s)"
              echo ""
              echo "DEVICE:"
              echo "  ${WRISH_DEVICE} — ${WRISH_MAC}"
              echo ""
              echo "CONFIG: .wrishrc (PWD or HOME) — WRISH_* variables"
              exit 0
              ;;
            --list)
              wrish_bluetooth_list
              exit
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

    if [ "$#" -eq 0 ]; then
      echo "No arguments supplied. Run 'wrish --help' for usage." >&2
      exit 1
    fi

    case "$1" in
      scan)
        shift
        wrish_bluetooth_scan "$@"
        ;;
      connect)
        wrish_bluetooth_connect "$WRISH_MAC"
        ;;
      info)
        wrish_router_info
        ;;
      notify)
        shift
        wrish_router_notify "$@"
        ;;
      heart-rate)
        shift
        wrish_router_heart_rate "$@"
        ;;
      *)
        echo "Unknown command: $1. Run 'wrish --help' for usage." >&2
        exit 1
        ;;
    esac

#
}
