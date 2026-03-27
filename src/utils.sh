
module utils

# Convert hex byte string "XX XX XX" to ASCII text, skipping null bytes.
# Args: <hex_bytes>
wrish_hex_to_ascii() {
    local bytes="$1"
    local byte
    local escape
    local out=""

    for byte in $bytes; do
        byte=$(printf '%s' "$byte" | tr '[:lower:]' '[:upper:]')
        case "$byte" in
            [0-9A-F][0-9A-F]) ;;
            *) continue ;;
        esac
        [ "$byte" = "00" ] && continue
        printf -v escape '\\x%s' "$byte"
        out="${out}$(printf '%b' "$escape")"
    done

    printf '%s\n' "$out"
}

# Convert hex byte string to uint16 little-endian values.
# Bytes taken in pairs: [low high] → value.  Prints as [v1, v2, ...].
# Args: <hex_bytes>
wrish_hex_to_uint16_le() {
    local bytes="$1"
    local filtered=()
    local byte
    local i
    local low
    local high
    local value
    local out=""

    for byte in $bytes; do
        byte=$(printf '%s' "$byte" | tr '[:lower:]' '[:upper:]')
        case "$byte" in
            [0-9A-F][0-9A-F]) filtered+=("$byte") ;;
        esac
    done

    if (( ${#filtered[@]} % 2 != 0 )); then
        echo "error: cannot decode uint16-le from odd number of bytes (${#filtered[@]})" >&2
        return 1
    fi

    i=0
    while [ "$i" -lt "${#filtered[@]}" ]; do
        low="${filtered[$i]}"
        high="${filtered[$((i + 1))]}"
        value=$(( 16#$high * 256 + 16#$low ))
        [ -n "$out" ] && out="$out, "
        out="${out}${value}"
        i=$(( i + 2 ))
    done

    printf '[%s]\n' "$out"
}

# Decode hex bytes according to mode.
# Args: <hex_bytes> <mode: raw|ascii|little-endian>
wrish_hex_decode() {
    local hex="$1"
    local mode="${2:-raw}"
    case "$mode" in
        ascii)         wrish_hex_to_ascii "$hex" ;;
        little-endian) wrish_hex_to_uint16_le "$hex" ;;
        *)             printf '%s\n' "$hex" ;;
    esac
}
