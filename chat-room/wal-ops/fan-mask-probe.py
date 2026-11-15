#!/usr/bin/env python3
"""Probe the fanotify mask/class matrix on THIS kernel, as root.

Answers, with rc/errno measured rather than cited:
  A. FAN_CLASS_NOTIF | FAN_REPORT_FID  + mark CREATE/DELETE/MOVE   -> ?
  B. FAN_CLASS_NOTIF (legacy)          + mark CREATE              -> ?
  C. FAN_CLASS_PRE_CONTENT | FAN_REPORT_FID (init)                -> ?
  D. FAN_CLASS_PRE_CONTENT (legacy)    + mark OPEN_PERM           -> ?
"""
import ctypes, ctypes.util, os, sys, tempfile

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libc.syscall.restype = ctypes.c_long
NR_init, NR_mark = 300, 301
FAN_CLOEXEC, FAN_NONBLOCK = 0x1, 0x2
FAN_CLASS_NOTIF, FAN_CLASS_PRE_CONTENT = 0x0, 0x8
FAN_REPORT_FID = 0x200
FAN_MARK_ADD, FAN_MARK_ONLYDIR = 0x1, 0x4
FAN_OPEN_PERM = 0x10000
FAN_CREATE, FAN_DELETE, FAN_MOVE, FAN_MOVED_FROM, FAN_MOVED_TO = 0x100, 0x200, 0x40, 0x40, 0x80
AT_FDCWD = -100


def init(flags):
    ctypes.set_errno(0)
    fd = libc.syscall(ctypes.c_long(NR_init), ctypes.c_uint(flags), ctypes.c_uint(0))
    return fd, ctypes.get_errno()


def mark(fd, mask, path):
    ctypes.set_errno(0)
    rc = libc.syscall(ctypes.c_long(NR_mark), ctypes.c_int(fd),
                      ctypes.c_uint(FAN_MARK_ADD | FAN_MARK_ONLYDIR),
                      ctypes.c_uint(mask), ctypes.c_int(AT_FDCWD),
                      ctypes.c_char_p(path.encode()))
    return rc, ctypes.get_errno()


if os.getuid() != 0:
    sys.exit("run as root: FAN_REPORT_FID/perm marks need CAP_SYS_ADMIN")

d = tempfile.mkdtemp(prefix="fan-probe-")

fd, e = init(FAN_CLASS_NOTIF | FAN_REPORT_FID | FAN_CLOEXEC | FAN_NONBLOCK)
print("A init NOTIF|FID: fd=%d errno=%d" % (fd, e))
if fd >= 0:
    rc, e2 = mark(fd, FAN_CREATE | FAN_DELETE | FAN_MOVED_FROM | FAN_MOVED_TO, d)
    print("A mark CREATE|DELETE|MOVED_*: rc=%d errno=%d" % (rc, e2))
    os.close(fd)

fd, e = init(FAN_CLASS_NOTIF | FAN_CLOEXEC | FAN_NONBLOCK)
print("B init NOTIF legacy: fd=%d errno=%d" % (fd, e))
if fd >= 0:
    rc, e2 = mark(fd, FAN_CREATE, d)
    print("B mark CREATE: rc=%d errno=%d" % (rc, e2))
    os.close(fd)

fd, e = init(FAN_CLASS_PRE_CONTENT | FAN_REPORT_FID | FAN_CLOEXEC | FAN_NONBLOCK)
print("C init PRE_CONTENT|FID: fd=%d errno=%d" % (fd, e))
if fd >= 0:
    os.close(fd)

fd, e = init(FAN_CLASS_PRE_CONTENT | FAN_CLOEXEC | FAN_NONBLOCK)
print("D init PRE_CONTENT legacy: fd=%d errno=%d" % (fd, e))
if fd >= 0:
    rc, e2 = mark(fd, FAN_OPEN_PERM, d)
    print("D mark OPEN_PERM: rc=%d errno=%d" % (rc, e2))
    os.close(fd)

os.rmdir(d)
