"""hidraw ioctl request numbers.

Linux encodes ioctl requests as
``(dir << 30) | (size << 16) | (type << 8) | nr``.  The hidraw ioctls we need
are all direction read|write (``3``) on type ``'H'``, and their size is the
length of the report buffer, so each request number depends on the payload
length and has to be computed per call.
"""

_IOC_TYPE = ord("H")
_IOC_READ_WRITE = 3


def _ioc(nr: int, size: int) -> int:
    return (_IOC_READ_WRITE << 30) | (size << 16) | (_IOC_TYPE << 8) | nr


def hidiocsfeature(size: int) -> int:
    """SET_FEATURE - how the APIv5 keyboard is written."""
    return _ioc(0x06, size)


def hidiocgfeature(size: int) -> int:
    """GET_FEATURE - APIv5 readiness probe."""
    return _ioc(0x07, size)


def hidiocsoutput(size: int) -> int:
    """SET_OUTPUT - how the APIv4 chassis is written.

    Note this is a real SET_REPORT, not ``os.write``: writing to the fd sends
    to the interrupt endpoint instead, which the AW-ELC firmware accepts and
    then ignores, so colours never persist.
    """
    return _ioc(0x0B, size)
