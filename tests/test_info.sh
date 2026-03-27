#!/usr/bin/env bash
# Tests for: wrish info
# Verifies that the device name is read correctly from GATT 0x2A00

set -e

WRISH="${1:-./target/release/wrish}"
PASS=0
FAIL=0

_ok() {
    echo "  PASS: $1"
    PASS=$(( PASS + 1 ))
}

_fail() {
    echo "  FAIL: $1"
    echo "        expected: $2"
    echo "        got:      $3"
    FAIL=$(( FAIL + 1 ))
}

_assert_contains() {
    local desc="$1"
    local expected="$2"
    local actual="$3"
    if echo "$actual" | grep -qF "$expected"; then
        _ok "$desc"
    else
        _fail "$desc" "$expected" "$actual"
    fi
}

_assert_exit() {
    local desc="$1"
    local expected_code="$2"
    local actual_code="$3"
    if [ "$actual_code" -eq "$expected_code" ]; then
        _ok "$desc"
    else
        _fail "$desc" "exit $expected_code" "exit $actual_code"
    fi
}

echo "=== wrish info tests ==="
echo ""

# --- Test 1: wrish info reads 0x2A00 and returns the device name ---------------
echo "Test 1: info reads device name from 0x2A00"
output=$("$WRISH" info 2>/dev/null)
exit_code=$?
_assert_exit "exit code is 0" 0 "$exit_code"
_assert_contains "output contains '0x2A00'" "0x2A00" "$output"
_assert_contains "output contains device name 'C60-A82C'" "C60-A82C" "$output"
echo ""

# --- Test 2: .wrishrc in PWD sets WRISH_MAC -----------------------------------
echo "Test 2: .wrishrc in PWD is loaded"
tmpdir=$(mktemp -d)
cp "$WRISH" "$tmpdir/wrish"
# Copy notify.py alongside (needed at runtime for notify command)
[ -f "src/notify.py" ] && cp src/notify.py "$tmpdir/"
echo "WRISH_MAC=A4:C1:38:9A:A8:2C" > "$tmpdir/.wrishrc"
echo "WRISH_DEVICE=C60-A82C" >> "$tmpdir/.wrishrc"
rc_output=$(cd "$tmpdir" && ./wrish --help 2>/dev/null)
_assert_contains ".wrishrc WRISH_MAC shown in --help" "A4:C1:38:9A:A8:2C" "$rc_output"
_assert_contains ".wrishrc WRISH_DEVICE shown in --help" "C60-A82C" "$rc_output"
rm -rf "$tmpdir"
echo ""

# --- Test 3: wrish --help exits 0 and mentions 'info' command -----------------
echo "Test 3: --help mentions info command"
help_output=$("$WRISH" --help 2>/dev/null)
exit_code=$?
_assert_exit "--help exits 0" 0 "$exit_code"
_assert_contains "--help lists 'info' command" "info" "$help_output"
echo ""

# --- Test 4: wrish with no args exits non-zero --------------------------------
echo "Test 4: no arguments exits non-zero"
set +e
"$WRISH" > /dev/null 2>&1
no_args_exit=$?
set -e
if [ "$no_args_exit" -ne 0 ]; then
    _ok "no arguments exits non-zero (got $no_args_exit)"
else
    _fail "no arguments exits non-zero" "non-zero" "0"
fi
echo ""

# --- Summary ------------------------------------------------------------------
echo "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
