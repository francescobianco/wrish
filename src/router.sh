
module router

# Route 'info' to the correct device handler
wrish_router_info() {
    case "${WRISH_DEVICE}" in
        C60-A82C) wrish_c60a82c_info "${WRISH_MAC}" ;;
        *) echo "Unknown device: ${WRISH_DEVICE}" >&2; return 1 ;;
    esac
}

# Route 'notify' to the correct device handler
wrish_router_notify() {
    case "${WRISH_DEVICE}" in
        C60-A82C) wrish_c60a82c_notify "$@" ;;
        *) echo "Unknown device: ${WRISH_DEVICE}" >&2; return 1 ;;
    esac
}

# Route 'heart-rate' to the correct device handler
wrish_router_heart_rate() {
    case "${WRISH_DEVICE}" in
        C60-A82C) wrish_c60a82c_heart_rate_monitor "$@" ;;
        *) echo "Unknown device: ${WRISH_DEVICE}" >&2; return 1 ;;
    esac
}
