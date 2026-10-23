"""
macOS counterparts of the Windows calls (the Mac port, 0.59). Windows code
keeps its own kernel32 / shell32 calls; every place that used to fall through
to "nothing" off Windows asks this module instead.

Volumes come from statfs(2): `f_flags & MNT_LOCAL` tells a share from a local
disk, `f_mntonname` is the volume's mount point (`/` or `/Volumes/<name>`).
A volume's identity (what Windows calls the serial) is its volume UUID from
`diskutil`, cached per mount point; other Unixes (tests on Linux CI) get the
device number instead, which is stable for as long as the volume is mounted.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache

IS_MAC = sys.platform == "darwin"

MNT_LOCAL = 0x00001000
MNT_DONTBROWSE = 0x00100000
VOLUMES = "/Volumes"

# Filesystems where Finder keeps a Trash; others (FAT/exFAT cards) delete for good.
TRASH_FS = ("apfs", "hfs")


class _Statfs(ctypes.Structure):
    # <sys/mount.h>, the 64-bit-inode layout (the only one on arm64).
    _fields_ = [("f_bsize", ctypes.c_uint32), ("f_iosize", ctypes.c_int32),
                ("f_blocks", ctypes.c_uint64), ("f_bfree", ctypes.c_uint64), ("f_bavail", ctypes.c_uint64),
                ("f_files", ctypes.c_uint64), ("f_ffree", ctypes.c_uint64),
                ("f_fsid", ctypes.c_int32 * 2), ("f_owner", ctypes.c_uint32), ("f_type", ctypes.c_uint32),
                ("f_flags", ctypes.c_uint32), ("f_fssubtype", ctypes.c_uint32),
                ("f_fstypename", ctypes.c_char * 16), ("f_mntonname", ctypes.c_char * 1024),
                ("f_mntfromname", ctypes.c_char * 1024), ("f_flags_ext", ctypes.c_uint32),
                ("f_reserved", ctypes.c_uint32 * 7)]


@lru_cache(maxsize=1)
def _statfs_fn():
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    try:
        fn = getattr(libc, "statfs$INODE64")          # Intel Macs
    except AttributeError:
        fn = libc.statfs
    fn.argtypes = [ctypes.c_char_p, ctypes.POINTER(_Statfs)]
    fn.restype = ctypes.c_int
    return fn


@dataclass(frozen=True)
class Volume:
    mount: str          # "/" or "/Volumes/<name>"
    fstype: str         # apfs, hfs, msdos, exfat, smbfs, nfs, afpfs, webdav...
    local: bool         # False = a network share
    browsable: bool     # False = a system volume Finder hides


def volume(path: str | os.PathLike) -> Volume | None:
    """The mounted volume holding `path` (None if it can't be reached / not a Mac)."""
    if not IS_MAC:
        return None
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:    # a folder about to be made
        p = os.path.dirname(p)
    buf = _Statfs()
    if _statfs_fn()(os.fsencode(p), ctypes.byref(buf)) != 0:
        return None
    return Volume(buf.f_mntonname.decode("utf-8", "replace"), buf.f_fstypename.decode("ascii", "replace"),
                  bool(buf.f_flags & MNT_LOCAL), not buf.f_flags & MNT_DONTBROWSE)


def is_network(path: str | os.PathLike) -> bool:
    v = volume(path)
    return v is not None and not v.local


def has_trash(path: str | os.PathLike) -> bool:
    """Whether a file moved to the Trash from here can still be put back."""
    v = volume(path)
    return v is not None and v.local and v.fstype in TRASH_FS


def mounted_volumes() -> list[str]:
    """Every volume in /Volumes the user would see in Finder, boot disk excluded
    (it appears there as a link to /)."""
    if not IS_MAC:
        return []
    out = []
    try:
        entries = sorted(os.scandir(VOLUMES), key=lambda e: e.name.lower())
    except OSError:
        return []
    for e in entries:
        if e.name.startswith(".") or e.is_symlink() or not e.is_dir():
            continue
        v = volume(e.path)
        if v is None or v.mount != e.path or not v.browsable:
            continue
        out.append(e.path)
    return out


def drive_kind(mount: str) -> str:
    """'network', 'fixed' (the boot disk) or 'removable' (anything else local:
    external disks, card readers, USB sticks)."""
    v = volume(mount)
    if v is None:
        return "removable"
    if not v.local:
        return "network"
    return "fixed" if v.mount == "/" else "removable"


def mount_point(path: str | os.PathLike) -> str:
    """The mount point of the volume a path is (or would be) on - any Unix.
    A path that doesn't exist yet counts from its nearest existing parent."""
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    try:
        dev = os.stat(p).st_dev
        while (parent := os.path.dirname(p)) != p and os.stat(parent).st_dev == dev:
            p = parent
    except OSError:
        pass
    return p


def split_mount(path: str) -> tuple[str, str]:
    """('/Volumes/Backup', 'Lunelis/2026') - the volume and the path inside it,
    so a drive that comes back under another name can be found again."""
    v = volume(path)
    mount = v.mount if v else "/"
    rest = os.path.relpath(os.path.abspath(path), mount)
    return mount, "" if rest == "." else rest


@lru_cache(maxsize=64)
def _diskutil(mount: str) -> tuple[str | None, str | None]:
    try:
        out = subprocess.run(["diskutil", "info", "-plist", mount], capture_output=True, timeout=10).stdout
        info = plistlib.loads(out)
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException, ValueError):
        return None, None
    uuid = info.get("VolumeUUID") or info.get("DiskUUID")
    return uuid, info.get("VolumeName") or None


def volume_info(path: str) -> tuple[str | None, str | None]:
    """(identity, label) of the volume holding `path` - the Mac's version of a
    Windows volume serial. Other Unixes: the device number, no label."""
    if IS_MAC:
        v = volume(path)
        if v is None:
            return None, None
        uuid, name = _diskutil(v.mount)
        return uuid, name or (os.path.basename(v.mount) or None)
    try:
        return f"{os.stat(path).st_dev:08X}", None
    except OSError:
        return None, None


def trash(path: str) -> None:
    """Move a file to the Trash (Finder's "Put Back" works). Raises OSError."""
    from PySide6.QtCore import QFile
    if not QFile.moveToTrash(os.path.abspath(path)):
        raise OSError("the Trash refused it")


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input (IOHIDSystem's HIDIdleTime)."""
    if not IS_MAC:
        return 0.0
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(m.group(1)) / 1e9 if m else 0.0


def keep_awake() -> subprocess.Popen | None:
    """Hold idle sleep back until the returned process is terminated
    (`caffeinate -i`, tied to our pid so it never outlives Lunelis)."""
    if not IS_MAC:
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

