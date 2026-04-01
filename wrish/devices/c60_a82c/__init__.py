"""C60-A82C wristband device package."""

from ._bluez import DeviceError
from ._device import C60A82CDevice
from ._dialer import decode_dialer_symbols

__all__ = ["C60A82CDevice", "DeviceError", "decode_dialer_symbols"]