#!/usr/bin/env python3
"""
DynamicEnabler

Enables the Dynamic Island of macOS 27.3 beta on MacBook Pro models with a display notch, and
integrates it with the physical notch.

Overview
  1. Activation
     MenuBarAgent reads two preferences that control the Dynamic Island, EmulateCenterHUD and
     ShowSoftwareNotch. Each one is guarded by an IsAppleInternalBuild() check, so both are
     ignored on customer builds. The script sets both preferences and replaces the two guarding
     branches (cbz) with nop instructions.

  2. Notch integration (default layout "notch")
     The stock implementation draws the island around a virtual 59 x 34 pt sensor area 7 pt
     below the menu bar. The script replaces that sensor area with the real notch rectangle:
       - Compact and Minimal presentations sit on either side of the notch.
       - Expanded presentations start at the top edge of the screen and wrap around the notch.
       - iPhone-mirrored Live Activities are given a wider fixed width.
       - The square "software notch" window that ShowSoftwareNotch draws over the notch is
         reduced to zero size, so its corners no longer show around the rounded notch.
     Layout "below" keeps the stock 59 pt sensor hidden inside the notch and moves expanded
     presentations below the notch instead. Layout "classic" only performs the activation; it is
     used automatically when no display with a notch is active.
     With the island at the top edge, a small module loaded into MenuBarAgent also makes the
     topmost row of pixels clickable (see "Top edge clicks" below).

  3. Expanded widths in client processes (layout "notch" only)
     The width of each expanded presentation is fixed in the process that renders it (for
     example 260 pt for timers and 286 pt for Now Playing), which leaves too little room once
     the notch occupies the middle. The script hooks the SwiftUI frame initializers in those
     processes and replaces widths in the expanded range:
       - single-row presentations (height 60 pt): notch width + 2 x 100.5 pt
       - taller presentations:                    notch width + 2 x 72 pt
     A few clients size their panels without .frame() and receive dedicated patches (AirDrop,
     and the battery banners shown by ControlCenter). A background helper patches clients
     that start later, such as the timer process when a timer is first started.

  4. Top edge clicks (layouts "notch" and "below")
     WindowServer routes clicks on the transparent island window by the shape of its layers. It
     hit-tests only fully opaque content exactly; for all other content the shape loses its top
     1 pt, so a click with the pointer pushed against the top of the screen went to the menu bar.
     LLDB compiles a small native module directly into process memory using debugging rights.
     It places an opaque black layer at the bottom of every island hit-test layer, clipped by
     the island's own rounded container, so the clickable shape equals the visible black
     island. A 1 pt opaque strip over the flat part of the painted top row also closes the
     narrow dead band left by the rounded hit shape. Because a filter on an ancestor also
     disqualifies opaque content, it removes the
     container's "colorInvertDisplayAware" filter while Invert Colors is off, where that filter
     has no effect; with Invert Colors on, the stock layers are left untouched. WindowServer
     sometimes keeps a stale click shape after the island's layers change, so every 0.5 s the
     module asks WindowServer which window owns the top row of each island and, only when that
     check fails, sets the container's filters again, which makes WindowServer recompute it.
     From macOS 27.3 beta 2 on, two more corrections are needed for that row. WindowServer
     delivers most real clicks there to the menu bar window, which belongs to the same process,
     so the module hands a click on the flat top row of an island to the island window. And the
     island's content ignores a mouse event that lies exactly on the window's top edge, which
     is where the pointer is when it is pushed against the top of the screen; such events are
     moved 0.5 pt into the island window.

Requirements
  - macOS 27.3 beta (tested on builds 26C5035r and 26C5047e). The patch sites are located by
    code signature in the installed binaries, so other builds work as long as every signature
    still matches exactly once; otherwise nothing is changed. Every patch site is compared
    with its original bytes before writing.
  - Any Mac. The layouts "notch" and "below" need an active display with a notch (a notched
    MacBook with the lid open); without one the script uses layout "classic" instead. The island
    is shown on the main display; --displays all shows it on every active display and --displays
    secondary on every display except the main one (see "Multi-display patch").
    On any display other than the built-in one it keeps the stock ("classic") position. The background helper reapplies the patches when the built-in
    display's resolution changes or the lid is opened or closed.
  - SIP's debugging restriction lifted ("csrutil enable --without debug" in Recovery), or SIP
    fully disabled. lldb cannot attach to system processes otherwise.
  - The latest Xcode beta, or Command Line Tools that match macOS 27 (for lldb, clang and
    swift). Older tools crash when lldb attaches.
  - Password-free debugging: "sudo DevToolsSecurity -enable" and membership in the _developer
    group. The script tests an lldb attach before it changes anything.

Notes
  - All changes are in-memory patches. They are lost when MenuBarAgent or the Mac restarts.
    Enable "apply automatically at login" to reapply them at every login.
  - Siri AI (which also draws Spotlight) connects to the island only when it starts. If it was
    already running, it stays out of the island until it restarts; --restart-siri quits it once
    the patches are in place (macOS starts it again when it is needed). If Siri AI is off or not
    available on the Mac, this makes no difference.
  - The script never modifies SIP or files on the system volume. Runtime copies live under
    ~/Library/Application Support/DynamicEnabler. The top edge module lives only in process
    memory; --disable removes the runtime copies and restarts MenuBarAgent to clear the module.

Usage
  python3 enable_dynamic_island.py                      interactive setup
  python3 enable_dynamic_island.py --enable [--autostart | --no-autostart] [--layout notch|below|classic]
                                   [--restart-siri | --no-restart-siri] [--displays main|all|secondary]
  python3 enable_dynamic_island.py --disable
"""

import argparse
import json
import os
import plistlib
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import uuid

# ---------------------------------------------------------------------------------------------
# General configuration
# ---------------------------------------------------------------------------------------------

MENUBARAGENT = "/System/Library/CoreServices/MenuBarAgent.app/Contents/MacOS/MenuBarAgent"
AIRDROPUI = "/System/Library/CoreServices/AirDropUI.app/Contents/MacOS/AirDropUI"
DOMAIN = "com.apple.MenuBarAgent"
DEFAULTS = ("EmulateCenterHUD", "ShowSoftwareNotch")

SCRIPT_NAME = "enable_dynamic_island.py"
SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/DynamicEnabler")
LOG_DIR = os.path.expanduser("~/Library/Logs")
WATCHER_LOG = os.path.join(LOG_DIR, "dynamic_island_watcher.log")
AUTOSTART_LOG = os.path.join(LOG_DIR, "dynamic_island_autostart.log")
WATCHER_LABEL = "local.dynamic-island.watcher"
REAPPLY_LABEL = "local.dynamic-island.reapply"
AUTOSTART_LABEL = "local.dynamic-island.autostart"
AUTOSTART_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{AUTOSTART_LABEL}.plist")
LAYOUTS = ("notch", "below", "classic")
# launchd jobs use the Apple-signed interpreter from the Command Line Tools. macOS lists background
# items under the signer of their executable; a python.org build would appear under its developer.
PYTHON = "/usr/bin/python3"


def run(*cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def fail(message):
    sys.exit("Error: " + message)


def gui_domain():
    return f"gui/{os.getuid()}"


def menubaragent_pid():
    job = run("launchctl", "print", f"{gui_domain()}/{DOMAIN}")
    match = re.search(r"^\s*pid = (\d+)$", job.stdout, re.M)
    return int(match.group(1)) if job.returncode == 0 and match else 0


# ---------------------------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------------------------

def check_sip():
    """Only SIP's debugging restriction blocks lldb; the rest of SIP can stay on."""
    status = run("csrutil", "status").stdout
    if re.search(r"status:\s*disabled", status, re.I) or \
            re.search(r"Debugging Restrictions:\s*disabled", status, re.I):
        return
    sys.exit(
        "System Integrity Protection (SIP) is enabled.\n"
        "This script attaches to MenuBarAgent and other system processes with lldb, which SIP's\n"
        "debugging restriction does not allow. No further steps can be performed until it is lifted.\n\n"
        "To lift only the debugging restriction (recommended; the rest of SIP stays on):\n"
        "  1. Shut down the Mac.\n"
        "  2. Press and hold the power button until \"Loading startup options\" appears.\n"
        "  3. Select Options, then choose Utilities > Terminal.\n"
        "  4. Run: csrutil enable --without debug\n"
        "  5. Restart the Mac and run this script again.\n\n"
        "Fully disabled SIP (csrutil disable) also works. Run \"csrutil enable\" from\n"
        "Recovery to restore full protection.")


def check_environment():
    if not os.path.exists(MENUBARAGENT):
        fail("MenuBarAgent was not found. This script requires macOS 27.3 beta or later.")
    if run("xcode-select", "-p").returncode or run("xcrun", "--find", "lldb").returncode:
        fail("Xcode is required. Install the latest Xcode beta from https://developer.apple.com/download/all/\n"
             "and select it with: sudo xcode-select -s /Applications/Xcode-beta.app/Contents/Developer\n"
             "(Command Line Tools that match macOS 27 also work; older versions crash when lldb attaches.)")


def check_debug_access():
    """lldb needs debugging rights without a password prompt; otherwise every attach waits for
    the "Developer Tools Access" dialog (and, at login, for nobody) until it times out."""
    in_group = "_developer" in run("id", "-Gn").stdout.split()
    dev_mode = "currently enabled" in run("DevToolsSecurity", "-status").stdout
    if in_group and dev_mode:
        return
    fail("lldb does not have permission to debug other processes without asking for a password.\n"
         "Without it, attaching to MenuBarAgent waits for the \"Developer Tools Access\" dialog\n"
         "and times out, and the dialog appears again after every restart.\n\n"
         "Run these commands, then log out and back in (or restart) and run this script again:\n"
         + ("" if dev_mode else "  sudo DevToolsSecurity -enable\n")
         + ("" if in_group else '  sudo dseditgroup -o edit -a "$USER" -t user _developer\n')
         + "\nNo changes were made.")
    check_lldb_attach()


def check_lldb_attach():
    """Attaches lldb to a short-lived child process. An outdated lldb crashes on attach under
    macOS 27 (SIGILL), and a missing debug right makes it wait for a dialog; both would otherwise
    only surface later as an unconfirmed MenuBarAgent patch."""
    target = subprocess.Popen(["/bin/sleep", "60"])
    try:
        try:
            probe = subprocess.run(
                ["xcrun", "lldb", "--batch", "-o", f"process attach --pid {target.pid}", "-o", "detach"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
                start_new_session=True)
        except subprocess.TimeoutExpired:
            fail("lldb did not attach to a test process within 30 seconds. It may be waiting for the\n"
                 "\"Developer Tools Access\" dialog. Check that \"DevToolsSecurity -status\" reports\n"
                 "enabled and that your user is in the _developer group. No changes were made.")
        if probe.returncode == 0 and "detached" in probe.stdout:
            return
        version = run("xcrun", "lldb", "--version").stdout.strip().splitlines()
        details = "\n".join(line for line in probe.stdout.strip().splitlines()[-5:] if line)
        if probe.returncode < 0:
            details += f"\n(lldb was terminated by signal {-probe.returncode})"
        fail("lldb could not attach to a test process:\n"
             f"{details}\n\n"
             f"Developer directory: {run('xcode-select', '-p').stdout.strip()}\n"
             f"lldb version: {version[0] if version else 'unknown'}\n\n"
             "This usually means that the developer tools are older than macOS 27. Install the\n"
             "latest Xcode beta from https://developer.apple.com/download/all/ and select it with:\n"
             "  sudo xcode-select -s /Applications/Xcode-beta.app/Contents/Developer\n"
             "Then check that \"xcrun lldb --version\" reports a newer version and run this script\n"
             "again. No changes were made.")
    finally:
        target.kill()
        target.wait()


# ---------------------------------------------------------------------------------------------
# Notch geometry
# ---------------------------------------------------------------------------------------------

def notch_geometry():
    """Returns (notch width, notch height, screen width, height below the menu bar) of the
    built-in display in points, or None. The last two identify that screen at runtime."""
    swift = r'''
import AppKit
for s in NSScreen.screens where s.safeAreaInsets.top > 0 {
    if let l = s.auxiliaryTopLeftArea, let r = s.auxiliaryTopRightArea {
        print(r.minX - l.maxX, s.safeAreaInsets.top, s.frame.width, s.frame.height - s.safeAreaInsets.top); break
    }
}
'''
    out = run("xcrun", "swift", "-e", swift).stdout.split()
    if len(out) != 4:
        return None
    # Rounded to 0.5 pt so that every derived constant can be loaded exactly with movz/movk.
    return tuple(round(float(v) * 2) / 2 for v in out)


# ---------------------------------------------------------------------------------------------
# ARM64 encoding helpers
# ---------------------------------------------------------------------------------------------

def enc(word):
    return struct.pack("<I", word)


def bl(pc, target):
    return 0x94000000 | (((target - pc) >> 2) & 0x3FFFFFF)


def b(pc, target):
    return 0x14000000 | (((target - pc) >> 2) & 0x3FFFFFF)


def load_double(xreg, value):
    """movz/movk sequence that loads the bit pattern of a double into an x register."""
    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    words = [0xD2800000 | (3 << 21) | (((bits >> 48) & 0xFFFF) << 5) | xreg]
    for hw in (2, 1, 0):
        words.append(0xF2800000 | (hw << 21) | (((bits >> (16 * hw)) & 0xFFFF) << 5) | xreg)
    return words


def mov_double(reg, value):
    """movz reg,#x,lsl#32 ; movk reg,#y,lsl#48 (valid when the low 32 bits are zero)."""
    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    assert bits & 0xFFFFFFFF == 0
    return (enc(0xD2C00000 | (((bits >> 32) & 0xFFFF) << 5) | reg).hex(),
            enc(0xF2E00000 | (((bits >> 48) & 0xFFFF) << 5) | reg).hex())


def fmov_imm(d, value):
    """fmov dD, #imm for values representable as (16..31)/16 x 2^(-3..4)."""
    for exp in range(-3, 5):
        frac = value / 2.0 ** exp * 16 - 16
        if 0 <= frac < 16 and frac == int(frac):
            e = exp + 3
            imm8 = ((0 if e & 4 else 1) << 6) | ((e & 3) << 4) | int(frac)
            return 0x1E601000 | (imm8 << 13) | d
    raise ValueError(value)


def adrp(pc, page, rd):
    imm = (page - (pc & ~0xFFF)) >> 12
    return 0x90000000 | ((imm & 3) << 29) | (((imm >> 2) & 0x7FFFF) << 5) | rd


def adrp_page(pc, word):
    """Target page of an adrp instruction at pc."""
    imm = ((word >> 3) & 0x1FFFFC) | ((word >> 29) & 3)
    imm -= (imm & 0x100000) << 1
    return (pc & ~0xFFF) + (imm << 12)


def fmov_d_x(d, x): return 0x9E670000 | (x << 5) | d
def fabs(d, n): return 0x1E60C000 | (n << 5) | d
def fcmp(n, m): return 0x1E602000 | (m << 16) | (n << 5)
def fadd(d, n, m): return 0x1E602800 | (m << 16) | (n << 5) | d
def fsub(d, n, m): return 0x1E603800 | (m << 16) | (n << 5) | d
def fmul(d, n, m): return 0x1E600800 | (m << 16) | (n << 5) | d
def dup_hi(d, n): return 0x5E180400 | (n << 5) | d          # mov dD, vN.d[1]
def ins_hi_v(d, n): return 0x6E180400 | (n << 5) | d        # mov vD.d[1], vN.d[0]
def ins_hi_x(d, n): return 0x4E181C00 | (n << 5) | d        # mov vD.d[1], xN
def ins_lo_x(d, n): return 0x4E081C00 | (n << 5) | d        # mov vD.d[0], xN


NOP = 0xD503201F
FMOV_D3_HALF = 0x1E6C1003            # fmov d3, #0.5
MOVI_V1_ZERO = 0x6F00E401            # movi.2d v1, #0
PROLOGUE = [0xA9BF7BFD, 0x910003FD]  # stp x29, x30, [sp, #-16]! ; mov x29, sp
EPILOGUE = [0xA8C17BFD, 0xD65F03C0]  # ldp x29, x30, [sp], #16 ; ret

# ---------------------------------------------------------------------------------------------
# MenuBarAgent patch sites
# ---------------------------------------------------------------------------------------------
# The sites are located by code signature in the MenuBarAgent binary of the running system (see
# locate_sites), so the script does not depend on the addresses of one build:
#   gates               the two `cbz w0` after IsAppleInternalBuild() in front of EmulateCenterHUD
#                       and ShowSoftwareNotch, with their original bytes
#   leaf                builds the virtual sensor rectangle (stock: 59 x 34, 7 pt below the menu bar)
#   rect_init           MacSystemUI.Rect.init(rawValue:) stub
#   getter_a, getter_b  sensor rectangle getters; sites_a, sites_b are their call sites
#   expanded_sites      getter A calls in the expanded layout (layout "below")
#   no_obstruction      b.ne -> nop: expanded scenes report "no obstruction" (layout "below")
#   to_screen           converts a rectangle to screen coordinates
#   notch_window_sites  the frame of the square "software notch" window passes through to_screen
#                       at two call sites (creation and update); the cave zeroes the size of the
#                       rectangle that equals the notch
#   remote_width_site   iPhone-mirrored ("remote") Live Activities are rendered at a fixed width of
#                       374 pt and scaled by 0.8. The four instructions here return that width for
#                       both the expanded and the list item metrics; the cave returns a width that
#                       leaves REMOTE_SIDE pt on each side of the notch. Clients request new content
#                       after the change, which takes a few seconds.
#   caves               seven code caves in the zero padding at the end of the __TEXT segment
#
# Only the island on the built-in display fits the notch. The island is shown on the main display,
# which may be an external display or a Sidecar iPad. The leaf receives the area of that screen
# below the menu bar, (x, menu bar height, width, height); cave6 compares it with the built-in
# screen and runs the stock leaf for any other screen. Only notch rectangles have y = 0, so the
# getter caves change those and pass stock rectangles through unchanged.

PACIBSP = 0xD503237F
STOCK_SENSOR_W = 59.0
# The gap that the system reports between the two menu bar areas is slightly wider than the
# visible notch. Island geometry is narrowed by this margin on each side so that the idle pill
# does not show a 1 px edge next to the notch.
NOTCH_MARGIN = 1.5
REMOTE_WIDTH_ORIG = ("010080d2", "02008052", "0000ccd2", "e00ee8f2")
REMOTE_SCALE, REMOTE_SIDE = 0.8, 72.0
CAVE_SLOTS = (16, 24, 24, 24, 48, 16, 48)       # instructions per cave
CAVE_OFFSETS = tuple(sum(CAVE_SLOTS[:i]) * 4 for i in range(len(CAVE_SLOTS)))

# Builds on which the result was tested, by the UUID of the arm64e MenuBarAgent image. On these
# the located sites must equal the addresses below; other builds rely on the signatures alone.
VERIFIED_BUILDS = {
    "DE472913-A81D-35BE-9584-EC597987E015": ("26C5035r", dict(
        gates=[0x10057FD98, 0x10057A138], leaf=0x1001F7A98, rect_init=0x1005A3E08,
        getter_a=0x1001F5644, getter_b=0x1001F567C, calls=(60, 10),
        expanded_sites=(0x1001EE4C4, 0x1001EE4F4, 0x1001EE560, 0x1001EE7F4), no_obstruction=0x1003C9650,
        to_screen=0x10044B200, notch_window_sites=(0x1002F1B48, 0x1002AE284),
        remote_width_site=0x1003CFB30, caves=0x1006A2700)),
    "5DA23C12-9FC3-3E00-88CC-F873E76F7CBC": ("26C5047e", dict(
        gates=[0x10059260C, 0x10058C970], leaf=0x100202D1C, rect_init=0x1005B84B4,
        getter_a=0x1002007EC, getter_b=0x1002008A8, calls=(61, 10),
        expanded_sites=(0x1001F94A8, 0x1001F94D8, 0x1001F9544, 0x1001F97D8), no_obstruction=0x1003D07B0,
        to_screen=0x100454368, notch_window_sites=(0x1002F62E0, 0x1002B0540),
        remote_width_site=0x1003D6CAC, caves=0x1006B8A00)),
}


def arm64e_slice(fat, path="MenuBarAgent"):
    nfat = struct.unpack(">I", fat[4:8])[0]
    for i in range(nfat):
        cpu, sub, off, size, _ = struct.unpack(">iiIII", fat[8 + 20 * i: 28 + 20 * i])
        if cpu == 0x0100000C and (sub & 0xFF) == 2:
            return fat[off: off + size]
    fail(f"{path} has no arm64e slice.")


class SignatureError(Exception):
    """A patch site could not be located unambiguously."""


class MachO:
    """Minimal reader for one thin arm64e Mach-O image whose __TEXT segment starts at file offset 0."""

    def __init__(self, data):
        self.data = data
        self.segments, self.sections = {}, {}
        p = 32
        for _ in range(struct.unpack_from("<I", data, 16)[0]):
            cmd, size = struct.unpack_from("<II", data, p)
            if cmd == 0x19:                                         # LC_SEGMENT_64
                name = data[p + 8: p + 24].rstrip(b"\0").decode()
                self.segments[name] = struct.unpack_from("<QQ", data, p + 24)       # vmaddr, vmsize
                for i in range(struct.unpack_from("<I", data, p + 64)[0]):
                    s = p + 72 + 80 * i
                    addr, length = struct.unpack_from("<QQ", data, s + 32)
                    self.sections[data[s: s + 16].rstrip(b"\0").decode()] = (
                        addr, length, *struct.unpack_from("<II", data, s + 68))     # + reserved1, reserved2
            elif cmd == 0x2:                                        # LC_SYMTAB
                self.symoff, self.nsyms, self.stroff, _ = struct.unpack_from("<IIII", data, p + 8)
            elif cmd == 0xB:                                        # LC_DYSYMTAB
                self.indirectoff = struct.unpack_from("<I", data, p + 56)[0]
            elif cmd == 0x1B:                                       # LC_UUID
                self.uuid = str(uuid.UUID(bytes=data[p + 8: p + 24])).upper()
            p += size
        self.base = self.segments["__TEXT"][0]
        self.t0, length = self.sections["__text"][:2]
        self.t1 = self.t0 + length
        self.text = data[self.t0 - self.base: self.t1 - self.base]
        self.words = struct.unpack("<%dI" % (length // 4), self.text)

    def raw(self, addr, n):
        return self.data[addr - self.base: addr - self.base + n]

    def w(self, addr):
        return self.words[(addr - self.t0) >> 2] if self.t0 <= addr < self.t1 else 0

    def stub(self, symbol):
        """Address of the __auth_stubs entry of an imported symbol."""
        addr, length, first, stride = self.sections["__auth_stubs"]
        for i in range(length // stride):
            index = struct.unpack_from("<I", self.data, self.indirectoff + 4 * (first + i))[0]
            if index < self.nsyms:
                s = self.stroff + struct.unpack_from("<I", self.data, self.symoff + 16 * index)[0]
                if self.data[s: self.data.index(b"\0", s)].decode() == symbol:
                    return addr + stride * i
        raise SignatureError(f"import {symbol} not found")

    def cstring(self, text):
        addr, length = self.sections["__cstring"][:2]
        i = self.data.find(b"\0" + text.encode() + b"\0", addr - self.base, addr - self.base + length)
        if i < 0:
            raise SignatureError(f'string "{text}" not found')
        return self.base + i + 1

    def branch_target(self, addr):
        off = (self.w(addr) & 0x3FFFFFF) << 2
        return addr + off - ((off & 0x8000000) << 1)

    def adrp_page(self, addr):
        """(page address, register) of an adrp instruction, or None."""
        w = self.w(addr)
        if w & 0x9F000000 != 0x90000000:
            return None
        imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
        return (addr & ~0xFFF) + ((imm - ((imm & 0x100000) << 1)) << 12), w & 31

    def adrp_add(self, addr):
        page, w = self.adrp_page(addr), self.w(addr + 4)
        if page and w & 0xFFC00000 == 0x91000000 and (w >> 5) & 31 == page[1]:
            return page[0] + ((w >> 10) & 0xFFF)
        return None

    def fp_literal(self, addr):
        """Doubles loaded by `adrp ; ldr d/q, [reg, #imm]` at addr, or None."""
        page, w = self.adrp_page(addr), self.w(addr + 4)
        if not page or (w >> 5) & 31 != page[1]:
            return None
        for opcode, size in ((0xFD400000, 8), (0x3DC00000, 16)):
            if w & 0xFFC00000 == opcode:
                return struct.unpack("<%dd" % (size // 8), self.raw(page[0] + ((w >> 10) & 0xFFF) * size, size))
        return None

    def find(self, words):
        """Addresses where the exact instruction words occur."""
        pattern, out = b"".join(enc(w) for w in words), []
        i = self.text.find(pattern)
        while i >= 0:
            if i % 4 == 0:
                out.append(self.t0 + i)
            i = self.text.find(pattern, i + 1)
        return out

    def match(self, addr, masked):
        """masked: [(value, mask)] compared with the instructions starting at addr."""
        return all(self.w(addr + 4 * i) & m == v for i, (v, m) in enumerate(masked))

    def function_start(self, addr):
        while addr > self.t0 and self.w(addr) != PACIBSP:
            addr -= 4
        return addr


def one(name, hits):
    hits = sorted(set(hits))
    if len(hits) != 1:
        raise SignatureError(f"{name}: {len(hits)} matches instead of 1")
    return hits[0]


def locate_sites(image):
    """Locates every MenuBarAgent patch site in a thin arm64e image by its code signature.

    Returns a namespace with the fields described under "MenuBarAgent patch sites". Every
    signature must match exactly once; otherwise SignatureError is raised and nothing is patched.
    """
    m = MachO(image)
    s = types.SimpleNamespace(uuid=getattr(m, "uuid", ""))

    s.rect_init = m.stub("_$s11MacSystemUI4RectV8rawValueACs5SIMD4VySdG_tcfC")
    calls, tails = {}, []               # bl target -> call sites; tail calls (b) to Rect.init
    for i, w in enumerate(m.words):
        op = w >> 26
        if op == 0x25:
            addr = m.t0 + 4 * i
            calls.setdefault(m.branch_target(addr), []).append(addr)
        elif op == 0x05:
            addr = m.t0 + 4 * i
            if m.branch_target(addr) == s.rect_init:
                tails.append(addr)

    # Gates: `bl _IsAppleInternalBuild ; cbz w0, ...`, told apart by the preference key that is
    # referenced right after the branch or at the top of a function called right after it.
    guards = [a + 4 for a in calls.get(m.stub("_IsAppleInternalBuild"), []) if m.w(a + 4) & 0xFF00001F == 0x34000000]
    s.gates = []
    for key in DEFAULTS:
        string = m.cstring(key)

        def refers(g):
            if any(m.adrp_add(a) == string for a in range(g + 4, g + 40, 4)):
                return True
            for a in range(g + 4, g + 16, 4):
                if m.w(a) >> 26 == 0x25:
                    t = m.branch_target(a)
                    if any(m.adrp_add(x) == string for x in range(t, t + 32, 4)):
                        return True
            return False
        gate = one(f"{key} gate", [g for g in guards if refers(g)])
        s.gates.append((gate, m.raw(gate, 4).hex()))

    # Leaf: loads -59 and (59, 34), computes the rectangle and tail-calls Rect.init.
    leaf_body = [0x1E622821, 0x1E6C1002, 0x1E620821, 0x6F00F782, 0x6E080422, 0x4E62D400]
    s.leaf = one("sensor rectangle leaf", [
        t - 40 for t in tails
        if m.fp_literal(t - 40) == (-STOCK_SENSOR_W,) and m.fp_literal(t - 8) == (STOCK_SENSOR_W, 34.0)
        and [m.w(a) for a in range(t - 32, t - 8, 4)] == leaf_body])

    # Getter A: `ldp q0, q1, [x20, #..] ; ldr x8, [x20, #..] ; ldrb w8, [x8, #..] ; cbnz w8` and a
    # call to Rect.init. Getter B is the first later function that starts by copying x20 and
    # loading two vectors from it.
    getter_tail = [(0xAD400280, 0xFFC003FF), (0xF9400288, 0xFFC003FF), (0x39400108, 0xFFC003FF),
                   (0x35000008, 0xFF00001F)]
    s.getter_a = one("sensor getter A", [
        a - 4 for a in m.find(PROLOGUE)
        if m.w(a - 4) == PACIBSP and m.match(a + 8, getter_tail)
        and any(m.w(x) >> 26 == 0x25 and m.branch_target(x) == s.rect_init for x in range(a + 24, a + 48, 4))])
    s.getter_b = None
    for f in range(s.getter_a + 4, s.getter_a + 0x400, 4):
        if m.w(f) == PACIBSP and any(m.w(x) == 0xAA1403F3 and m.match(x + 4, getter_tail[:1])
                                     for x in range(f + 4, f + 40, 4)):
            s.getter_b = f
            break
    s.sites_a, s.sites_b = calls.get(s.getter_a, []), calls.get(s.getter_b, [])
    if len(s.sites_a) < 40 or len(s.sites_b) < 5:
        raise SignatureError(f"sensor getters: {len(s.sites_a)} and {len(s.sites_b)} call sites")

    # Expanded layout (layout "below"): the function with exactly four getter A calls at these
    # relative positions. None when it cannot be identified.
    per_function = {}
    for c in s.sites_a:
        per_function.setdefault(m.function_start(c), []).append(c)
    expanded = [cs for cs in per_function.values()
                if len(cs) == 4 and [c - cs[0] for c in cs] == [0, 0x30, 0x9C, 0x330]]
    s.expanded_sites = tuple(expanded[0]) if len(expanded) == 1 else None

    # Expanded scenes report "no obstruction":
    # `ldrb w8, [x20, #..] ; cmp w8, #1 ; b.ne ; adrp x8 ; ldr x0, [x8, #..] ; bl ; ldur q2, [..]`
    s.no_obstruction = one("no-obstruction branch", [
        a + 4 for a in m.find([0x7100051F])
        if m.match(a - 4, [(0x39400288, 0xFFC003FF), (0x7100051F, 0xFFFFFFFF), (0x54000001, 0xFF00001F),
                           (0x90000008, 0x9F00001F), (0xF9400100, 0xFFC003FF), (0x94000000, 0xFC000000),
                           (0x3CC00002, 0xFFE00C1F)])])

    # Screen conversion helper: `stp q0, q1, [sp] ; adrp x8 ; ldr x0, [x8, #..] ; bl ; bl ;
    # ldr q3, [sp] ; mov d1, v3[1] ; fsub d0, d0, d1`
    s.to_screen = one("screen conversion helper", [
        m.function_start(a) for a in m.find([0x3DC003E3, 0x5E180461, 0x1E613800])
        if m.match(a - 20, [(0xAD0007E0, 0xFFFFFFFF), (0x90000008, 0x9F00001F), (0xF9400100, 0xFFC003FF),
                            (0x94000000, 0xFC000000), (0x94000000, 0xFC000000)])])
    # Software notch window, creation: the call is followed by
    # `stp q0, q1, [sp] ; mov d8, v0[1] ; mov d9, v1[1]`.
    converts = calls.get(s.to_screen, [])
    create = one("notch window creation site", [
        c for c in converts if [m.w(c + 4), m.w(c + 8), m.w(c + 12)] == [0xAD0007E0, 0x5E180408, 0x5E180429]])
    # Update: of the functions that call the creating function, the one with a single conversion.
    creator_callers = {m.function_start(c) for c in calls.get(m.function_start(create), [])}
    by_function = {}
    for c in converts:
        by_function.setdefault(m.function_start(c), []).append(c)
    update = one("notch window update site", [cs[0] for f, cs in by_function.items()
                                              if f in creator_callers and len(cs) == 1])
    s.notch_window_sites = (create, update)

    # Remote Live Activity width: `mov x1, #0 ; mov w2, #0 ; x0 = 374.0 ; ret`
    s.remote_width_site = one("remote width", m.find([int.from_bytes(bytes.fromhex(h), "little")
                                                        for h in REMOTE_WIDTH_ORIG] + [0xD65F03C0]))

    # Code caves: zero padding between the last __TEXT section and the end of the segment.
    vmaddr, vmsize = m.segments["__TEXT"]
    end = max(a + n for a, n, *_ in m.sections.values() if vmaddr <= a < vmaddr + vmsize)
    cave = (end + 0xFF) & ~0xFF
    size = sum(CAVE_SLOTS) * 4
    if cave + size > vmaddr + vmsize or any(m.raw(cave, size)):
        raise SignatureError("no room for the code caves")
    s.caves = tuple(cave + off for off in CAVE_OFFSETS)
    return s


def menubar_sites(image):
    """Locates the patch sites and compares them with the verified addresses of known builds."""
    try:
        s = locate_sites(image)
    except (SignatureError, KeyError, IndexError, struct.error) as exc:
        fail("The Dynamic Island code could not be located in this build of MenuBarAgent "
             f"({exc}).\nThis macOS build is not supported; the Dynamic Island exists from "
             "macOS 27.3 beta on. No changes were made.")
    build, known = VERIFIED_BUILDS.get(s.uuid, (None, None))
    if known:
        found = dict(gates=[a for a, _ in s.gates], leaf=s.leaf, rect_init=s.rect_init, getter_a=s.getter_a,
                     getter_b=s.getter_b, calls=(len(s.sites_a), len(s.sites_b)),
                     expanded_sites=s.expanded_sites, no_obstruction=s.no_obstruction, to_screen=s.to_screen,
                     notch_window_sites=s.notch_window_sites, remote_width_site=s.remote_width_site,
                     caves=s.caves[0])
        wrong = sorted(k for k in known if known[k] != found[k])
        if wrong:
            fail(f"The located patch sites differ from the verified ones of build {build} "
                 f"({', '.join(wrong)}). No changes were made.")
    s.verified = build
    return s


# ---------------------------------------------------------------------------------------------
# Multi-display patch (contributed by Eric Dou)
# ---------------------------------------------------------------------------------------------
# MenuBarAgent picks a presentation style per display in one small function, the "style
# selector", which sits directly in front of the sensor rectangle leaf. It returns 2 (ordinary
# menu bar) when DisableCenterHUD is set, handles one display kind on its own, and otherwise
# returns 1 (island) only if EmulateCenterHUD is set and Display.Storage.isMain is true. The
# last check keeps the island on the main display.
#
# The selector is shared with menu-item spacing (8 vs 14 pt), so it is not patched in place:
# doing so removed Apple's own menu bar items. Instead a copy without the isMain check is
# placed after the code caves, and every caller except the spacing one is redirected to it.
# For "secondary" a small wrapper calls the copy and returns 2 whenever isMain is true.
# A system banner reaches only the HUD of its preferred display; that filter is skipped so
# that each HUD receives it.
#
# Display modes: "main" (stock), "all" (every active display), "secondary" (every display but
# the main one). Located by signature like the other sites; verified on 26C5035r and 26C5047e.
# 26C5035r checks banners differently, so there they stay on their preferred display.

DISPLAY_MODES = ("main", "all", "secondary")
MULTIDISPLAY_VERIFIED = {
    "DE472913-A81D-35BE-9584-EC597987E015": dict(
        selector=0x1001F7A04, gate=0x1001F7A8C, spacing_call=0x1000305EC,
        calls=(0x100017650, 0x10001895C, 0x1000194D8, 0x1001F76F4, 0x100313334, 0x1004D416C),
        banner=None, banner_append=None),
    "5DA23C12-9FC3-3E00-88CC-F873E76F7CBC": dict(
        selector=0x100202C90, gate=0x100202D10, spacing_call=0x100032084,
        calls=(0x100017780, 0x100018AEC, 0x1000194F0, 0x100019C04, 0x100202934, 0x10031807C, 0x1004DE4B4),
        banner=0x100018C48, banner_append=0x100018C7C),
}


def locate_multidisplay(image, s):
    m = MachO(image)
    d = types.SimpleNamespace()
    # Selector tail, right in front of the leaf: `mov x0, #0 ; bl <metadata> ; ldrsw x8, [x0, #isMain] ;
    # add x8, x19, x8 ; ldrb w8, [x8] ; tbz w8, #0, <style 2> ; mov w0, #1 ; b <epilogue>`
    d.gate = s.leaf - 12
    if not m.match(d.gate - 20, [(0xD2800000, 0xFFFFFFFF), (0x94000000, 0xFC000000), (0xB9800008, 0xFFC003FF),
                                 (0x8B080268, 0xFFFFFFFF), (0x39400108, 0xFFFFFFFF), (0x36000008, 0xFFF8001F),
                                 (0x52800020, 0xFFFFFFFF), (0x14000000, 0xFC000000)]):
        raise SignatureError("style selector: unexpected isMain check")
    d.selector = m.function_start(d.gate)
    off = ((m.w(d.gate) >> 5) & 0x3FFF) << 2
    style2 = d.gate + off - ((off & 0x8000) << 1)
    if m.w(style2) != 0x52800040 or not d.selector < style2 < d.gate:        # mov w0, #2
        raise SignatureError("style selector: unexpected fallback")
    # x19 = display storage, set up after `mov w0, #2 ; b` and before `ldrb w20, [x19, #..]`.
    d.storage_setup = []
    a = style2 + 8
    while m.w(a) & 0xFFC003FF != 0x39400274:
        d.storage_setup.append(a)
        a += 4
        if len(d.storage_setup) > 4:
            raise SignatureError("style selector: display storage not found")
    d.words = [m.w(x) for x in range(d.selector, s.leaf, 4)]
    for i, w in enumerate(d.words):            # every other pc-relative branch must stay inside
        pc = d.selector + 4 * i
        if w >> 26 in (0x05, 0x25) or w & 0x9F000000 == 0x90000000:
            continue
        if w & 0x7E000000 == 0x36000000:        # tbz / tbnz
            off = ((w >> 5) & 0x3FFF) << 2; off -= (off & 0x8000) << 1
        elif w & 0xFF000010 == 0x54000000 or w & 0x7E000000 == 0x34000000:      # b.cond, cbz / cbnz
            off = ((w >> 5) & 0x7FFFF) << 2; off -= (off & 0x100000) << 1
        elif w & 0x1F000000 == 0x10000000 or w & 0x3B000000 == 0x18000000:      # adr, ldr literal
            raise SignatureError("style selector: unexpected pc-relative instruction")
        else:
            continue
        if not d.selector <= pc + off < s.leaf:
            raise SignatureError("style selector: branch leaves the function")
    for i, w in enumerate(d.words):
        if w >> 26 == 0x05 and not d.selector <= m.branch_target(d.selector + 4 * i) < s.leaf:
            raise SignatureError("style selector: branch leaves the function")

    callers = [c for c in range(m.t0, m.t1, 4) if m.w(c) >> 26 == 0x25 and m.branch_target(c) == d.selector]
    # Menu-item spacing: `and w8, w0, #0xff ; cmp w8, #2 ; fmov d0, #imm`
    d.spacing_call = one("menu item spacing call", [c for c in callers if m.w(c + 8) == 0x7100091F
                                                    and m.w(c + 12) & 0xFFE01FE0 == 0x1E601000])
    d.calls = tuple(c for c in callers if c != d.spacing_call)
    if len(d.calls) < 4:
        raise SignatureError(f"style selector: {len(d.calls)} island callers")

    # Banner display filter: `ldrb w10, [x9, #4] ; cmp w10, #1 ; b.ne L ; tst x8, #8 ; cset w8, eq ;
    # and ; orr ; tbnz w8, #0, <append> ; b <skip> ; L: ldr ; ldr ; cmp ; b.ne <skip> ; <append>`
    pattern = [(0x39400000, 0xFFC00000), (0x7100055F, 0xFFFFFFFF), (0x54000001, 0xFF00001F),
               (0xF27D011F, 0xFFFFFFFF), (0x1A9F17E8, 0xFFFFFFFF), (0x0A000000, 0xFF200000),
               (0x2A000000, 0xFF200000), (0x37000008, 0xFFF8001F), (0x14000000, 0xFC000000),
               (0xB9400000, 0xFFC00000), (0xB9400000, 0xFFC00000), (0x6B00001F, 0xFF20001F),
               (0x54000001, 0xFF00001F)]
    # Optional: without it the island still appears on every display, and banners only on their
    # preferred one.
    hits = [m.t0 + 4 * i for i in range(len(m.words) - len(pattern)) if m.match(m.t0 + 4 * i, pattern)]
    d.banner = hits[0] if len(hits) == 1 and (m.w(hits[0] + 28) >> 5) & 0x3FFF == 6 else None
    d.banner_append = d.banner + 4 * len(pattern) if d.banner else None     # tbnz targets the append call

    known = MULTIDISPLAY_VERIFIED.get(s.uuid)
    if known:
        found = dict(selector=d.selector, gate=d.gate, spacing_call=d.spacing_call, calls=d.calls,
                     banner=d.banner, banner_append=d.banner_append)
        wrong = sorted(k for k in known if known[k] != found[k])
        if wrong:
            raise SignatureError(f"located sites differ from the verified ones ({', '.join(wrong)})")
    d.verified = bool(known)
    return d


def multidisplay_patches(image, s, mode):
    """Patches for the display modes "all" and "secondary" (see "Multi-display patch")."""
    m = MachO(image)
    d = locate_multidisplay(image, s)

    def moved(src, dst):
        """The instruction at src, re-encoded for address dst."""
        w = m.w(src)
        if w >> 26 == 0x25:
            return bl(dst, m.branch_target(src))
        if w & 0x9F000000 == 0x90000000:
            return adrp(dst, adrp_page(src, w), w & 31)
        return w

    copy = s.caves[0] + sum(CAVE_SLOTS) * 4
    words = [moved(d.selector + 4 * i, copy + 4 * i) for i in range(len(d.words))]
    words[(d.gate - d.selector) // 4] = NOP
    target, end = copy, copy + 4 * len(words)
    if mode == "secondary":
        target = (end + 15) & ~15
        # Saves x19-x22, calls the copy, then reads isMain the way the selector does.
        w = [PACIBSP, 0xA9BC4FF4, 0xA90157F6, 0xA9027BFD, 0x910083FD]     # stp x20, x19, [sp, #-0x40]! ; ...
        w += [bl(target + 4 * len(w), copy), 0x2A0003F5]                   # bl copy ; mov w21, w0
        for a in d.storage_setup + [d.gate - 20 + 4 * i for i in range(5)]:
            w.append(moved(a, target + 4 * len(w)))
        w += [0x7200011F, 0x52800049, 0x1A8902A0]                          # tst w8, #1 ; mov w9, #2 ; csel w0, w21, w9, eq
        w += [0xA9427BFD, 0xA94157F6, 0xA8C44FF4, 0xD65F0FFF]              # restore ; retab
        words += [0] * ((target - end) // 4) + w
        end = target + 4 * len(w)
    vmaddr, vmsize = m.segments["__TEXT"]
    if end > vmaddr + vmsize or any(m.raw(copy, end - copy)):
        raise SignatureError("no room for the style selector copy")

    patches = [(copy + 4 * i, b"\0\0\0\0", enc(w)) for i, w in enumerate(words) if w]
    patches += [(c, m.raw(c, 4), enc(bl(c, target))) for c in d.calls]
    if d.banner:
        patches.append((d.banner, m.raw(d.banner, 4), enc(b(d.banner, d.banner_append))))
    return patches, d


def build_patches(layout, geometry, displays="main"):
    """Returns ([(address, original bytes, new bytes)], sites). geometry is the result of
    notch_geometry() or None; layouts other than "classic" require it."""
    with open(MENUBARAGENT, "rb") as f:
        image = arm64e_slice(f.read())
    s = menubar_sites(image)
    cave0, cave1, cave2, cave3, cave4, cave5, cave6 = s.caves

    def orig_word(addr):
        o = addr - 0x100000000
        return image[o: o + 4]

    patches = [(a, bytes.fromhex(h), enc(NOP)) for a, h in s.gates]
    s.multidisplay = None
    if displays != "main":
        try:
            extra, s.multidisplay = multidisplay_patches(image, s, displays)
        except (SignatureError, KeyError, IndexError, struct.error) as exc:
            fail(f"Showing the Dynamic Island on more than the main display is not available on this "
                 f"build of MenuBarAgent ({exc}). Use --displays main. No changes were made.")
        patches += extra

    def add_caves(caves):
        for base, words in caves.items():
            assert len(words) <= CAVE_SLOTS[s.caves.index(base)], "code cave overflow"
            for i, w in enumerate(words):
                patches.append((base + 4 * i, b"\0\0\0\0", enc(w)))

    def notch_window_cave(real_notch_w, notch_h):
        # Calls to_screen (v0 = origin, v1 = size) and zeroes the size when it equals the notch
        # within 1 pt. Other windows are returned unchanged.
        c4 = PROLOGUE + [bl(cave4 + 8, s.to_screen), fmov_imm(18, 1.0)]
        skips = []
        c4 += load_double(8, real_notch_w) + [fmov_d_x(17, 8), fsub(16, 1, 17), fabs(16, 16), fcmp(16, 18)]
        skips.append(len(c4)); c4.append(None)
        c4 += load_double(8, notch_h) + [fmov_d_x(17, 8), dup_hi(16, 1), fsub(16, 16, 17), fabs(16, 16), fcmp(16, 18)]
        skips.append(len(c4)); c4.append(None)
        c4 += [MOVI_V1_ZERO]
        for at in skips:
            c4[at] = 0x54000000 | ((len(c4) - at) << 5) | 0xA      # b.ge -> epilogue
        return c4 + EPILOGUE

    if layout == "classic":
        # Classic layout still needs to hide the software notch window
        if geometry:
            add_caves({cave4: notch_window_cave(*geometry[:2])})
            for a in s.notch_window_sites:
                patches.append((a, orig_word(a), enc(bl(a, cave4))))
        return patches, s

    assert geometry, "layouts other than classic need the notch geometry"
    real_notch_w, notch_h, screen_w, area_h = geometry
    notch_w = real_notch_w - 2 * NOTCH_MARGIN
    below = layout == "below"
    sensor_w = STOCK_SENSOR_W if below else notch_w
    if below and not s.expanded_sites:
        fail("The expanded layout could not be located in this build of MenuBarAgent, so layout "
             "\"below\" is not available. No changes were made.")

    # cave0 replaces the leaf: (x + (W - 59) / 2, 0, W, notch height), then Rect.init.
    c0 = (load_double(8, sensor_w) + [fmov_d_x(2, 8), fsub(1, 1, 2), FMOV_D3_HALF, fmul(1, 1, 3),
                                      fadd(0, 0, 1)]
          + load_double(9, notch_h) + [fmov_d_x(1, 8), ins_hi_x(1, 9)])
    c0.append(b(cave0 + 4 * len(c0), s.rect_init))

    # cave6 enters the leaf: cave0 for the built-in screen, whose area below the menu bar is
    # (x, notch height, screen width, area height); the stock leaf for all other screens.
    leaf_word = int.from_bytes(orig_word(s.leaf), "little")
    if leaf_word & 0x9F00001F != 0x90000008:
        fail("The sensor rectangle function has an unexpected first instruction. No changes were made.")
    c6, skips = [fmov_imm(18, 0.5)], []
    for value, lane in ((screen_w, [fsub(16, 1, 17)]), (area_h, [dup_hi(16, 1), fsub(16, 16, 17)]),
                        (notch_h, [dup_hi(16, 0), fsub(16, 16, 17)])):
        c6 += load_double(9, value) + [fmov_d_x(17, 9)] + lane + [fabs(16, 16), fcmp(16, 18)]
        skips.append(len(c6)); c6.append(None)
    c6.append(b(cave6 + 4 * len(c6), cave0))
    for at in skips:
        c6[at] = 0x54000000 | ((len(c6) - at) << 5) | 0xA         # b.ge -> stock leaf
    c6.append(adrp(cave6 + 4 * len(c6), adrp_page(s.leaf, leaf_word), 8))
    c6.append(b(cave6 + 4 * len(c6), s.leaf + 4))

    # Getter caves change only notch rectangles (y = 0, or 1 for inset variants). Stock
    # rectangles start 7 pt below the safe area (y >= 7, also on screens without a menu bar
    # inset) and are returned unchanged.
    def getter_cave(base, getter, body):
        c = PROLOGUE + [bl(base + 8, getter), dup_hi(16, 0), fmov_imm(18, 4.0), fcmp(16, 18), None]
        c += body
        c[len(PROLOGUE) + 4] = 0x54000000 | ((len(c) - len(PROLOGUE) - 4) << 5) | 0xA   # b.ge -> epilogue
        return c + EPILOGUE

    # cave1 (layout "below"): expanded presentations use the sensor moved down by its height.
    c1 = getter_cave(cave1, s.getter_a, [dup_hi(16, 0), dup_hi(4, 1), fadd(16, 16, 4), ins_hi_v(0, 16)])

    # cave2 / cave3: all other callers receive the notch rectangle.
    def notch_cave(base, getter):
        return getter_cave(base, getter, load_double(8, (notch_w - sensor_w) / 2) + [fmov_d_x(5, 8), fsub(0, 0, 5)]
                           + load_double(9, notch_w) + [ins_lo_x(1, 9)])

    # cave5: fixed width for remote Live Activities (x1 = 0, w2 = 0, x0 = width bit pattern).
    remote_w = round((real_notch_w + 2 * REMOTE_SIDE) / REMOTE_SCALE * 2) / 2
    c5 = [0xD2800001, 0x52800002] + load_double(0, remote_w) + [0xD65F03C0]

    caves = {cave0: c0, cave6: c6, cave1: c1, cave2: notch_cave(cave2, s.getter_a),
             cave3: notch_cave(cave3, s.getter_b), cave4: notch_window_cave(real_notch_w, notch_h)}
    if not below:
        caves[cave5] = c5
    add_caves(caves)

    patches.append((s.leaf, orig_word(s.leaf), enc(b(s.leaf, cave6))))
    for a in s.sites_a:
        patches.append((a, orig_word(a), enc(bl(a, cave1 if below and a in s.expanded_sites else cave2))))
    for a in s.sites_b:
        patches.append((a, orig_word(a), enc(bl(a, cave3))))
    if below:
        patches.append((s.no_obstruction, orig_word(s.no_obstruction), enc(NOP)))
    for a in s.notch_window_sites:
        patches.append((a, orig_word(a), enc(bl(a, cave4))))
    if not below:
        patches.append((s.remote_width_site, orig_word(s.remote_width_site), enc(b(s.remote_width_site, cave5))))
    return patches, s


MENUBAR_LLDB = r'''
import json, lldb
def apply(debugger, command, result, internal_dict):
    t = debugger.GetSelectedTarget(); p = t.GetProcess(); m = t.GetModuleAtIndex(0); e = lldb.SBError()
    table = json.load(open(command.strip()))
    todo = []
    for a, o, n in table:
        la = m.ResolveFileAddress(a).GetLoadAddress(t)
        cur = p.ReadMemory(la, 4, e)
        if cur != bytes.fromhex(o) and cur != bytes.fromhex(n):
            print("MISMATCH", hex(a), cur.hex() if cur else None); return
        todo.append((la, bytes.fromhex(n)))
    for la, n in todo:          # the process is stopped at the attach point
        p.WriteMemory(la, n, e)
    if all(p.ReadMemory(la, 4, e) == n for la, n in todo):
        print("PATCH_OK", len(todo))
def __lldb_init_module(debugger, d):
    debugger.HandleCommand("command script add -f island_patch.apply island_apply")
'''


def patch_menubar(patches):
    """Restarts MenuBarAgent and patches the new process before it reads its preferences."""
    pid = menubaragent_pid()
    if not pid:
        return False, "MenuBarAgent is not running."
    if run("ps", "-p", str(pid), "-o", "comm=").stdout.strip() != MENUBARAGENT:
        return False, "The running process does not match MenuBarAgent."

    workdir = tempfile.mkdtemp(prefix="dynamic_island_")
    table = os.path.join(workdir, "patches.json")
    module = os.path.join(workdir, "island_patch.py")
    with open(table, "w") as f:
        json.dump([[a, o.hex(), n.hex()] for a, o, n in patches], f)
    with open(module, "w") as f:
        f.write(MENUBAR_LLDB)
    cmd = ["xcrun", "lldb", "--batch", "-o", f"command script import {module}",
           "-o", "process attach --name MenuBarAgent --waitfor", "-o", f"island_apply {table}", "-o", "detach"]
    timed_out = False
    try:
        debugger = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, start_new_session=True)

        def restart():          # launchd relaunches MenuBarAgent; lldb catches the new process
            time.sleep(3)
            if debugger.poll() is None:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        threading.Thread(target=restart, daemon=True).start()
        try:
            output, _ = debugger.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(debugger.pid, signal.SIGINT)
            try:
                output, _ = debugger.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(debugger.pid, signal.SIGKILL)
                output, _ = debugger.communicate()
        success = debugger.returncode == 0 and "PATCH_OK" in output and "detached" in output
        if timed_out and not success:
            output += ("\nlldb did not finish within 30 seconds. It may have been waiting for the "
                       "\"Developer Tools Access\" dialog; check that \"DevToolsSecurity -status\" "
                       "reports enabled and that your user is in the _developer group.")
    except OSError as exc:
        output, success = str(exc), False
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return success, output


# ---------------------------------------------------------------------------------------------
# Top edge clicks (module loaded into MenuBarAgent, see "Top edge clicks" at the top)
# ---------------------------------------------------------------------------------------------

TOP_HIT_DIR = os.path.expanduser("~/Library/Group Containers/com.apple.MenuBar")   # legacy dylib cleanup only
TOP_HIT_PREFIX = "DynamicEnablerTopHit"
# LLDB has no usable AppKit module search path when attaching to this system binary. Its
# expression compiler also cannot persist Objective-C AST nodes between expressions. Supply
# the arm64 SDK declarations and the implementation as ONE expression prefix instead. No
# dylib is mapped, no sandbox extension is issued, and no protected container is written.
TOP_HIT_JIT_PREFIX = r'''
typedef bool BOOL;
typedef unsigned long NSUInteger;
typedef long NSInteger;
typedef unsigned int uint32_t;
typedef double CGFloat;
typedef struct CGPoint { CGFloat x, y; } CGPoint;
typedef struct CGSize { CGFloat width, height; } CGSize;
typedef struct CGRect { CGPoint origin; CGSize size; } CGRect;
typedef CGRect NSRect;
typedef struct objc_method *Method;
typedef void (*IMP)(void);
typedef const struct __CFString *CFStringRef;
typedef struct CGColor *CGColorRef;
typedef struct dispatch_queue_s *dispatch_queue_t;
typedef struct { unsigned long state; id *itemsPtr; unsigned long *mutationsPtr; unsigned long extra[5]; } NSFastEnumerationState;
#define YES true
#define NO false
#define nil ((id)0)
#define OBJC_ASSOCIATION_RETAIN 0x301
#define RTLD_LAZY 1
#define kCALayerWidthSizable (1U << 1)
#define kCALayerHeightSizable (1U << 4)
extern "C" {
id objc_getAssociatedObject(id, const void *);
void objc_setAssociatedObject(id, const void *, id, unsigned long);
Method class_getInstanceMethod(Class, SEL);
IMP method_setImplementation(Method, IMP);
unsigned int method_getNumberOfArguments(Method);
void method_getReturnType(Method, char *, unsigned long);
void method_getArgumentType(Method, unsigned int, char *, unsigned long);
Class objc_allocateClassPair(Class, const char *, unsigned long);
void objc_disposeClassPair(Class);
void objc_registerClassPair(Class);
Class object_getClass(id);
BOOL class_addMethod(Class, SEL, IMP, const char *);
void *dlopen(const char *, int);
void *dlsym(void *, const char *);
double fabs(double);
void dispatch_async_f(dispatch_queue_t, void *, void (*)(void *));
CGColorRef CGColorGetConstantColor(CFStringRef);
}
#define objc_msgSend (dlsym((void *)-2, "objc_msgSend"))
#define dispatch_get_main_queue() ((dispatch_queue_t)dlsym((void *)-2, "_dispatch_main_q"))
#define kCGColorBlack (*(CFStringRef *)dlsym((void *)-2, "kCGColorBlack"))
static inline CGFloat CGRectGetMaxY(CGRect r) { return r.origin.y + (r.size.height > 0 ? r.size.height : 0); }
static inline CGFloat NSMidX(NSRect r) { return r.origin.x + r.size.width / 2; }
static inline CGFloat NSMaxY(NSRect r) { return CGRectGetMaxY(r); }
static inline NSRect NSRectFromCGRect(CGRect r) { return r; }
static inline CGPoint CGPointMake(CGFloat x, CGFloat y) { return (CGPoint){x,y}; }
@class NSString, NSArray, NSMutableArray, NSDictionary, CALayer, NSWindow, NSView;
@class NSNotificationCenter, NSTimer;
@interface NSObject
+ (id)alloc;
- (id)init;
+ (Class)class;
- (BOOL)respondsToSelector:(SEL)de_sel;
- (BOOL)isKindOfClass:(Class)de_cls;
- (id)copy;
- (id)mutableCopy;
- (id)retain;
- (oneway void)release;
- (id)autorelease;
@end
@interface NSString : NSObject
- (BOOL)isEqualToString:(NSString *)de_other;
+ (id)stringWithFormat:(NSString *)de_format, ...;
@end
@interface NSArray : NSObject
+ (id)arrayWithObjects:(const id[])de_objects count:(NSUInteger)de_count;
@property(readonly) NSUInteger count;
@property(readonly) id firstObject;
- (id)objectAtIndexedSubscript:(NSUInteger)de_idx;
- (NSUInteger)countByEnumeratingWithState:(NSFastEnumerationState *)de_state objects:(id *)de_buffer count:(NSUInteger)de_len;
@end
@interface NSMutableArray : NSArray
- (void)addObject:(id)de_obj;
- (void)removeObjectIdenticalTo:(id)de_obj;
@end
@interface NSDictionary : NSObject
+ (id)dictionaryWithObjects:(const id[])de_objects forKeys:(const id[])de_keys count:(NSUInteger)de_count;
@end
@interface NSNull : NSObject
+ (id)null;
@end
@interface CALayer : NSObject
+ (id)layer;
@property(copy) NSString *name;
@property(copy) NSArray *sublayers;
@property(copy) NSArray *filters;
@property(copy) NSDictionary *actions;
@property CGColorRef backgroundColor;
@property CGRect frame;
@property CGRect bounds;
@property CGFloat cornerRadius;
@property BOOL masksToBounds;
@property unsigned int autoresizingMask;
@property(readonly) CALayer *superlayer;
- (void)insertSublayer:(CALayer *)de_layer atIndex:(unsigned int)de_index;
- (void)removeFromSuperlayer;
- (CGRect)convertRect:(CGRect)de_rect toLayer:(CALayer *)de_layer;
@end
@interface CATransaction : NSObject
+ (void)begin;
+ (void)commit;
+ (void)setDisableActions:(BOOL)de_value;
@end
@interface NSView : NSObject
@property(readonly) NSView *superview;
@property(readonly) CALayer *layer;
@end
@interface NSWindow : NSObject
@property(readonly) NSInteger level;
@property(readonly) NSInteger windowNumber;
@property(readonly) NSRect frame;
@property(readonly) NSView *contentView;
- (NSRect)convertRectToScreen:(NSRect)de_rect;
@end
@interface NSEvent : NSObject
@property(readonly) NSUInteger type;
@property(readonly) CGPoint locationInWindow;
@property(readonly) NSUInteger modifierFlags;
@property(readonly) double timestamp;
@property(readonly) NSInteger windowNumber;
@property(readonly) NSInteger eventNumber;
@property(readonly) NSInteger clickCount;
@property(readonly) float pressure;
+ (NSEvent *)mouseEventWithType:(NSUInteger)de_type location:(CGPoint)de_location modifierFlags:(NSUInteger)de_flags timestamp:(double)de_time windowNumber:(NSInteger)de_window context:(id)de_context eventNumber:(NSInteger)de_number clickCount:(NSInteger)de_clicks pressure:(float)de_pressure;
@end
@interface NSApplication : NSObject
+ (NSApplication *)sharedApplication;
@property(readonly) NSArray *windows;
@end
@interface NSScreen : NSObject
+ (NSArray *)screens;
@property(readonly) NSRect frame;
@end
@interface NSWorkspace : NSObject
+ (NSWorkspace *)sharedWorkspace;
@property(readonly) BOOL accessibilityDisplayShouldInvertColors;
@property(readonly) NSNotificationCenter *notificationCenter;
@end
@interface NSNotificationCenter : NSObject
- (void)addObserver:(id)de_observer selector:(SEL)de_selector name:(NSString *)de_name object:(id)de_obj;
@end
@interface NSTimer : NSObject
+ (NSTimer *)timerWithTimeInterval:(double)de_interval target:(id)de_target selector:(SEL)de_selector userInfo:(id)de_info repeats:(BOOL)de_repeats;
@property double tolerance;
@end
@interface NSRunLoop : NSObject
+ (NSRunLoop *)mainRunLoop;
- (void)addTimer:(NSTimer *)de_timer forMode:(NSString *)de_mode;
@end
@interface NSThread : NSObject
+ (BOOL)isMainThread;
@end
extern "C" { SEL NSSelectorFromString(NSString *); }
#define NSApp ([NSApplication sharedApplication])
#define NSRunLoopCommonModes (*(NSString **)dlsym((void *)-2, "NSRunLoopCommonModes"))
#define NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification (*(NSString **)dlsym((void *)-2, "NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification"))
'''

TOP_HIT_SOURCE = r'''
#ifndef DYNAMIC_ENABLER_JIT
#import <AppKit/AppKit.h>
#import <QuartzCore/QuartzCore.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <dlfcn.h>
#define DE_LOCAL static
#else
#define DE_LOCAL
#endif
#ifndef DYNAMIC_ENABLER_TOP_HIT_VERSION
#define DYNAMIC_ENABLER_TOP_HIT_VERSION 1
#endif
#if __has_feature(objc_arc)
#define DE_KEEP(obj) (obj)
#define DE_DROP(obj) ((void)0)
#define DE_AUTORELEASE(obj) (obj)
#else
#define DE_KEEP(obj) [(obj) retain]
#define DE_DROP(obj) [(obj) release]
#define DE_AUTORELEASE(obj) [(obj) autorelease]
#endif

#define kName @"DynamicEnablerTopHit"
#define kStripName @"DynamicEnablerTopStrip"
DE_LOCAL char kSavedFilters;              // container -> its filters before the invert filter was removed
DE_LOCAL BOOL gEnabled, gScanQueued, gWriting, gInitialized;
DE_LOCAL IMP gOrigSetHit, gOrigSetFilters, gOrigSendEvent;
DE_LOCAL int gCount, gStrips, gRefreshes, gTopChecks, gTopMisses, gTopErrors;
DE_LOCAL NSMutableArray *gCovered;        // [window, container, hit layer] of every covered island
DE_LOCAL NSMutableArray *gStripItems;     // [container, strip] of every top-row strip in place
DE_LOCAL NSMutableArray *gStripNext;      // the same, collected by the scan in progress
DE_LOCAL id gObserver;
DE_LOCAL NSWindow *gRedirect;         // island window that receives the click in progress (not retained)

typedef int (*FindWindowFn)(int, int, int, int, const CGPoint *, CGPoint *, uint32_t *, int *);
DE_LOCAL int (*gMainConnection)(void);
DE_LOCAL FindWindowFn gFindWindow;

DE_LOCAL BOOL isHitLayer(CALayer *l) {
    SEL sel = NSSelectorFromString(@"hitTestsAsOpaque");
    return [l respondsToSelector:sel] && ((BOOL (*)(id, SEL))objc_msgSend)(l, sel);
}

DE_LOCAL BOOL isInvertFilter(id f) {
    for (NSString *de_key in @[@"type", @"name"]) {
        SEL de_selector = NSSelectorFromString(de_key);
        if (![f respondsToSelector:de_selector]) continue;
        id v = ((id (*)(id, SEL))objc_msgSend)(f, de_selector);
        if ([v isKindOfClass:[NSString class]] && [v isEqualToString:@"colorInvertDisplayAware"]) return YES;
    }
    return NO;
}

DE_LOCAL void setFiltersRaw(CALayer *l, NSArray *filters) {
    gWriting = YES;
    ((void (*)(id, SEL, NSArray *))gOrigSetFilters)(l, @selector(setFilters:), filters);
    gWriting = NO;
}

DE_LOCAL void stripFilter(CALayer *container) {
    NSArray *filters = container.filters;
    if (!filters.count) return;
    NSMutableArray *rest = [filters mutableCopy];
    for (id f in filters) if (isInvertFilter(f)) [rest removeObjectIdenticalTo:f];
    if (rest.count != filters.count) {
        objc_setAssociatedObject(container, &kSavedFilters, filters, OBJC_ASSOCIATION_RETAIN);
        setFiltersRaw(container, rest.count ? rest : nil);
    }
    DE_DROP(rest);
}

DE_LOCAL void restoreFilter(CALayer *container) {
    NSArray *saved = DE_KEEP(objc_getAssociatedObject(container, &kSavedFilters));
    if (!saved) return;
    objc_setAssociatedObject(container, &kSavedFilters, nil, OBJC_ASSOCIATION_RETAIN);
    setFiltersRaw(container, saved);
    DE_DROP(saved);
}

DE_LOCAL CALayer *underlayOf(CALayer *hit) {
    for (CALayer *c in hit.sublayers) if ([c.name isEqualToString:kName]) return c;
    return nil;
}

DE_LOCAL CALayer *islandRoot(NSWindow *w) {
    return (w.contentView.superview ?: w.contentView).layer;
}

DE_LOCAL CALayer *stripIn(NSArray *items, CALayer *container) {
    for (NSArray *item in items) if (item[0] == container) return item[1];
    return nil;
}

// The rounded clipping shape used for hit testing is narrower than the painted top row near the
// corners, leaving a thin dead band where clicks reach the menu bar. A rectangular opaque strip
// over the flat part of the painted row makes that row part of the island's window alpha.
// Every island container has its own strip. A scan collects the strips that are still wanted
// and removes the rest, so a strip never outlives its island or another island's check.
DE_LOCAL void ensureTopStrip(NSWindow *w, CALayer *container) {
    CALayer *root = islandRoot(w);
    if (!root || stripIn(gStripNext, container)) return;
    CGRect lr = [container convertRect:container.bounds toLayer:root];
    if (fabs(CGRectGetMaxY(lr) - root.bounds.size.height) > 0.5) return;     // not at the top edge
    CGFloat flat = lr.size.width - 2.0 * container.cornerRadius;
    if (flat < 2.0) return;                                 // a circle has no flat top row
    CALayer *s = stripIn(gStripItems, container);
    if (!s) {
        s = [CALayer layer];
        s.name = kStripName;
        s.backgroundColor = CGColorGetConstantColor(kCGColorBlack);
        s.actions = @{@"bounds": [NSNull null], @"position": [NSNull null], @"frame": [NSNull null],
                      @"onOrderIn": [NSNull null], @"onOrderOut": [NSNull null]};
    }
    if (s.superlayer != root) [root insertSublayer:s atIndex:0];
    s.frame = (CGRect){{NSMidX(lr) - flat / 2.0, root.bounds.size.height - 1.0}, {flat, 1.0}};
    [gStripNext addObject:@[container, s]];
}

DE_LOCAL void walk(NSWindow *w, CALayer *l, BOOL add) {
    for (CALayer *c in DE_AUTORELEASE([l.sublayers copy])) {
        if ([c.name isEqualToString:kName] || [c.name isEqualToString:kStripName]) continue;
        // Island hit-test layers sit directly inside the rounded, clipping island container.
        if (isHitLayer(c) && l.cornerRadius > 0 && l.masksToBounds) {
            CALayer *u = underlayOf(c);
            if (add) {
                stripFilter(l);
                if (!u) {
                    u = [CALayer layer];
                    u.name = kName;
                    u.backgroundColor = CGColorGetConstantColor(kCGColorBlack);
                    u.frame = c.bounds;
                    u.autoresizingMask = kCALayerWidthSizable | kCALayerHeightSizable;
                    u.actions = @{@"bounds": [NSNull null], @"position": [NSNull null], @"frame": [NSNull null],
                                  @"onOrderIn": [NSNull null], @"onOrderOut": [NSNull null]};
                    [c insertSublayer:u atIndex:0];
                }
                ensureTopStrip(w, l);
                gCount++;
                [gCovered addObject:@[w, l, c]];
            } else {
                [u removeFromSuperlayer];
                restoreFilter(l);
            }
        }
        walk(w, c, add);
    }
}

DE_LOCAL void dynamic_enabler_scan_top_hit(void) {
    BOOL add = gEnabled && !NSWorkspace.sharedWorkspace.accessibilityDisplayShouldInvertColors;
    gCount = 0;
    DE_DROP(gCovered);
    gCovered = [[NSMutableArray alloc] init];
    gStripNext = [[NSMutableArray alloc] init];
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    for (NSWindow *w in NSApp.windows) {
        if (w.level != 26 || w.frame.size.height < 200) continue;      // the full-screen island window
        CALayer *root = (w.contentView.superview ?: w.contentView).layer;
        if (root) walk(w, root, add);
    }
    for (NSArray *item in gStripItems)      // the island is gone, left the top edge or is not covered
        if (!stripIn(gStripNext, item[0])) [(CALayer *)item[1] removeFromSuperlayer];
    DE_DROP(gStripItems);
    gStripItems = gStripNext;
    gStripNext = nil;
    gStrips = (int)gStripItems.count;
    [CATransaction commit];
}

// WindowServer sometimes keeps a stale click shape after the island's layer tree changes (the
// opaque underlay is then ignored again). It recomputes the shape when the container's filters
// are set, so each covered island that touches the top edge is checked by asking WindowServer
// which window owns the middle of its top row of pixels, and refreshed only when that fails.
DE_LOCAL int topRowReachable(NSWindow *w, CALayer *hit) {
    if (!gFindWindow || !gMainConnection) return -1;
    CALayer *root = (w.contentView.superview ?: w.contentView).layer;
    CGRect r = [hit convertRect:hit.bounds toLayer:root];
    if (fabs(CGRectGetMaxY(r) - root.bounds.size.height) > 0.5) return 2;   // not at the top edge
    if (r.size.width < r.size.height + 4) return 2;      // a circle touches the edge in one point only
    NSRect s = [w convertRectToScreen:NSRectFromCGRect(r)];
    CGFloat primary = ((NSScreen *)NSScreen.screens.firstObject).frame.size.height;
    CGPoint p = CGPointMake(NSMidX(s), primary - NSMaxY(s)), out;
    uint32_t wid = 0; int owner = 0;
    if (gFindWindow(gMainConnection(), 0, 1, 0, &p, &out, &wid, &owner) != 0) return -1;
    return wid == (uint32_t)w.windowNumber;
}

DE_LOCAL void checkTopRows(void) {
    gTopChecks = gTopMisses = gTopErrors = 0;
    for (NSArray *item in DE_AUTORELEASE([gCovered copy])) {
        NSWindow *w = item[0]; CALayer *container = item[1], *hit = item[2];
        if (!hit.superlayer) continue;
        int result = topRowReachable(w, hit);
        if (result == 2) continue;
        if (result < 0) { gTopErrors++; continue; }
        gTopChecks++;
        if (result == 1) continue;
        gTopMisses++;
        gRefreshes++;
        [CATransaction begin];
        [CATransaction setDisableActions:YES];
        NSArray *de_current = DE_KEEP(container.filters);
        setFiltersRaw(container, @[]);
        setFiltersRaw(container, de_current);
        DE_DROP(de_current);
        ((void (*)(id, SEL, BOOL))gOrigSetHit)(hit, NSSelectorFromString(@"setHitTestsAsOpaque:"), NO);
        ((void (*)(id, SEL, BOOL))gOrigSetHit)(hit, NSSelectorFromString(@"setHitTestsAsOpaque:"), YES);
        [CATransaction commit];
    }
}

__attribute__((visibility("default"))) int dynamic_enabler_top_hit_refreshes(void) { return gRefreshes; }

DE_LOCAL void scanCallback(void *unused) { gScanQueued = NO; dynamic_enabler_scan_top_hit(); }

DE_LOCAL void queueScan(void) {
    if (gScanQueued) return;
    gScanQueued = YES;
    dispatch_async_f(dispatch_get_main_queue(), (void *)0, scanCallback);
}

// New island layers turn on hit testing through this setter: rescan once the tree is assembled.
DE_LOCAL void hookedSetHit(id self, SEL _cmd, BOOL value) {
    ((void (*)(id, SEL, BOOL))gOrigSetHit)(self, _cmd, value);
    if (value && gEnabled) queueScan();
}

// A container whose filter was removed may get its filters assigned again.
DE_LOCAL void hookedSetFilters(CALayer *self, SEL _cmd, NSArray *filters) {
    ((void (*)(id, SEL, NSArray *))gOrigSetFilters)(self, _cmd, filters);
    if (!gWriting && objc_getAssociatedObject(self, &kSavedFilters)) {
        objc_setAssociatedObject(self, &kSavedFilters, nil, OBJC_ASSOCIATION_RETAIN);
        if (gEnabled) queueScan();
    }
}

// Mouse events on the top row of pixels need two corrections (macOS 27.3 beta 2):
//  - WindowServer delivers most real clicks there to the menu bar window, even while it reports
//    the island window as the owner of that point. Both windows belong to this process, so a
//    click that reaches another window on the flat top row of a covered island is handed to the
//    island window instead, together with the drag and mouse-up events that follow it.
//  - With the pointer pushed against the top of the screen an event lies exactly on the window's
//    top edge (y == height), a position the island's content does not accept. Events for the
//    island window are therefore moved 0.5 pt into it.
DE_LOCAL NSEvent *movedEvent(NSEvent *de_event, NSWindow *w, CGPoint de_point) {
    NSEvent *de_moved = [NSEvent mouseEventWithType:de_event.type location:de_point
        modifierFlags:de_event.modifierFlags timestamp:de_event.timestamp
        windowNumber:w.windowNumber context:nil eventNumber:de_event.eventNumber
        clickCount:de_event.clickCount pressure:de_event.pressure];
    return de_moved ?: de_event;
}

DE_LOCAL CGPoint screenPoint(NSWindow *w, CGPoint de_point) {
    return [w convertRectToScreen:(NSRect){de_point, {0, 0}}].origin;
}

// The island window whose covered container has its flat top row under this screen point.
DE_LOCAL NSWindow *islandAtTopRow(CGPoint de_screen) {
    for (NSArray *item in gCovered) {
        NSWindow *w = item[0]; CALayer *container = item[1];
        CALayer *root = islandRoot(w);
        if (!root || !container.superlayer) continue;
        NSRect r = [w convertRectToScreen:NSRectFromCGRect([container convertRect:container.bounds toLayer:root])];
        CGFloat de_radius = container.cornerRadius;
        if (fabs(de_screen.y - NSMaxY(r)) <= 0.5 && fabs(NSMaxY(r) - NSMaxY(w.frame)) <= 0.5 &&
            de_screen.x >= r.origin.x + de_radius && de_screen.x <= r.origin.x + r.size.width - de_radius) return w;
    }
    return nil;
}

DE_LOCAL void hookedSendEvent(NSWindow *self, SEL _cmd, NSEvent *de_event) {
    NSUInteger de_type = de_event.type;
    NSWindow *de_target = self;
    if (gEnabled && de_type >= 1 && de_type <= 7) {         // mouse down/up/moved/dragged
        BOOL de_island = self.level == 26 && self.frame.size.height >= 200;
        if (!de_island && de_type == 1) {                   // left mouse down in another window
            gRedirect = islandAtTopRow(screenPoint(self, de_event.locationInWindow));
            if (gRedirect) de_target = gRedirect;
        } else if (!de_island && gRedirect && (de_type == 2 || de_type == 6)) {
            de_target = gRedirect;                          // the drag and mouse-up of that click
            if (de_type == 2) gRedirect = nil;
        } else if (de_type == 1 || de_type == 2) {
            gRedirect = nil;
        }
        if (de_target != self || de_island) {
            CGPoint de_point = de_event.locationInWindow;
            if (de_target != self) {
                CGPoint de_screen = screenPoint(self, de_point);
                de_point = CGPointMake(de_screen.x - de_target.frame.origin.x, de_screen.y - de_target.frame.origin.y);
            }
            CGFloat de_top = de_target.frame.size.height - 0.5;
            if (de_target != self || de_point.y > de_top) {
                if (de_point.y > de_top) de_point.y = de_top;
                de_event = movedEvent(de_event, de_target, de_point);
            }
        }
    }
    ((void (*)(id, SEL, NSEvent *))gOrigSendEvent)(de_target, _cmd, de_event);
}

int dynamic_enabler_top_hit(int enable);
// Every 0.5 s: check the click shapes WindowServer has from the previous pass, then cover
// layers that were rebuilt without going through the hooked setters.
DE_LOCAL void timerTick(id self, SEL command, id timer) {
    if (gEnabled) { checkTopRows(); dynamic_enabler_scan_top_hit(); }
}
DE_LOCAL int controlTopHit(id self, SEL command, int enable) { return dynamic_enabler_top_hit(enable); }
DE_LOCAL int topHitVersion(id self, SEL command) { return DYNAMIC_ENABLER_TOP_HIT_VERSION; }
DE_LOCAL id topHitStatus(id self, SEL command) {
    return [NSString stringWithFormat:@"enabled=%d covered=%d strips=%d checks=%d misses=%d errors=%d refreshes=%d",
            gEnabled, gCount, gStrips, gTopChecks, gTopMisses, gTopErrors, gRefreshes];
}
DE_LOCAL BOOL validSetter(Method method, char argument) {
    if (!method || method_getNumberOfArguments(method) != 3) return NO;
    char result[8] = {0}, value[8] = {0};
    method_getReturnType(method, result, sizeof(result));
    method_getArgumentType(method, 2, value, sizeof(value));
    return result[0] == 'v' && value[0] == argument;
}

// Install on the main thread, once per process. The runtime class survives LLDB detaching and
// exposes a versioned control entry so a retry cannot stack another set of method hooks.
int dynamic_enabler_top_hit(int enable) {
    if (![NSThread isMainThread]) return -1;
    if (!gInitialized) {
        Method hitMethod = class_getInstanceMethod([CALayer class], NSSelectorFromString(@"setHitTestsAsOpaque:"));
        Method filterMethod = class_getInstanceMethod([CALayer class], @selector(setFilters:));
        Method sendMethod = class_getInstanceMethod([NSWindow class], @selector(sendEvent:));
        if (!validSetter(hitMethod, @encode(BOOL)[0]) || !validSetter(filterMethod, '@') ||
            !validSetter(sendMethod, '@')) return -2;
        void *sky = dlopen("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY);
        if (!sky) return -3;
        gMainConnection = (int (*)(void))dlsym(sky, "SLSMainConnectionID");
        gFindWindow = (FindWindowFn)dlsym(sky, "SLSFindWindowAndOwner");
        if (!gMainConnection || !gFindWindow) return -3;
        Class de_class = objc_allocateClassPair([NSObject class], "DynamicEnablerTopHitRuntime", 0);
        if (!de_class) return -4;
        Class meta = object_getClass((id)de_class);
        if (!class_addMethod(de_class, @selector(tick:), (IMP)timerTick, "v@:@") ||
            !class_addMethod(meta, @selector(dynamicEnablerSetEnabled:), (IMP)controlTopHit, "i@:i") ||
            !class_addMethod(meta, @selector(dynamicEnablerVersion), (IMP)topHitVersion, "i@:") ||
            !class_addMethod(meta, @selector(dynamicEnablerStatus), (IMP)topHitStatus, "@@:")) {
            objc_disposeClassPair(de_class);
            return -4;
        }
        objc_registerClassPair(de_class);
        gObserver = [[de_class alloc] init];
        gOrigSetHit = method_setImplementation(hitMethod, (IMP)hookedSetHit);
        gOrigSetFilters = method_setImplementation(filterMethod, (IMP)hookedSetFilters);
        gOrigSendEvent = method_setImplementation(sendMethod, (IMP)hookedSendEvent);
        [NSWorkspace.sharedWorkspace.notificationCenter addObserver:gObserver selector:@selector(tick:)
            name:NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification object:nil];
        NSTimer *de_timer = [NSTimer timerWithTimeInterval:0.5 target:gObserver selector:@selector(tick:) userInfo:nil repeats:YES];
        de_timer.tolerance = 0.2;
        [NSRunLoop.mainRunLoop addTimer:de_timer forMode:NSRunLoopCommonModes];
        gInitialized = YES;
    }
    gEnabled = enable != 0;
    dynamic_enabler_scan_top_hit();
    return gCount;
}
'''


def remove_top_hit_files():
    # Under partial SIP the App Group container is not even listable; there is nothing of ours to
    # remove in that case (the module was never installed), so ignore the permission error.
    try:
        names = os.listdir(TOP_HIT_DIR) if os.path.isdir(TOP_HIT_DIR) else []
    except OSError:
        return
    for name in names:
        if name.startswith(TOP_HIT_PREFIX) and name.endswith(".dylib"):
            os.remove(os.path.join(TOP_HIT_DIR, name))


TOP_HIT_LLDB = r'''
import json
import lldb

def __lldb_init_module(debugger, unused):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    result = {"ok": False}
    try:
        if not process.IsValid() or process.GetState() != lldb.eStateStopped:
            raise RuntimeError("MenuBarAgent did not stop for the debugger")
        main = next((t for t in process if t.GetQueueName() == "com.apple.main-thread"), None)
        if main is None:
            main = next((t for t in process if t.GetIndexID() == 1), None)
        if main is None:
            raise RuntimeError("MenuBarAgent's main thread could not be found")
        process.SetSelectedThread(main)
        options = lldb.SBExpressionOptions()
        options.SetLanguage(lldb.eLanguageTypeObjC_plus_plus)
        options.SetTimeoutInMicroSeconds(5000000)
        options.SetTryAllThreads(False)
        options.SetIgnoreBreakpoints(True)
        options.SetUnwindOnError(True)

        def evaluate(expression):
            value = target.EvaluateExpression(expression, options)
            if value.GetError().Fail():
                raise RuntimeError(str(value.GetError()))
            return value.GetValueAsSigned()

        runtime = '(void *)objc_getClass("DynamicEnablerTopHitRuntime")'
        send = '(void *)dlsym((void *)-2, "objc_msgSend")'
        if evaluate('(unsigned long)' + runtime):
            version = evaluate('((int(*)(void*,void*))' + send + ')(' + runtime +
                               ', (void *)sel_registerName("dynamicEnablerVersion"))')
            if version != __VERSION__:
                raise RuntimeError("another top-edge module version is loaded; restart MenuBarAgent")
            covered = evaluate('((int(*)(void*,void*,int))' + send + ')(' + runtime +
                               ', (void *)sel_registerName("dynamicEnablerSetEnabled:"), 1)')
        else:
            with open(__SOURCE_PATH__) as source:
                options.SetPrefix(source.read())
            # One compilation unit: persisting these ObjC declarations with --top-level causes
            # LLDB's AST importer to silently drop bodies containing ObjC messages/literals.
            covered = evaluate('dynamic_enabler_top_hit(1)')
        if covered < 0:
            raise RuntimeError({-1: "installation must run on the main thread",
                                -2: "required CALayer or NSWindow methods are missing or their ABI changed",
                                -3: "WindowServer hit-test functions are unavailable",
                                -4: "could not register the top-edge control class"}.get(covered, "native installation failed"))
        result = {"ok": True, "covered": covered}
    except Exception as error:
        result["error"] = str(error)
    finally:
        if process.IsValid() and process.GetState() == lldb.eStateStopped:
            detached = process.Detach()
            if detached.Fail():
                result = {"ok": False, "error": "could not detach from MenuBarAgent: " + str(detached)}
        print("DYNAMIC_ENABLER_TOP_HIT " + json.dumps(result), flush=True)
'''


def top_hit_jit_source():
    """Version the ABI declarations and implementation together; keep both in the login copy."""
    import hashlib
    version = int(hashlib.sha256((TOP_HIT_JIT_PREFIX + TOP_HIT_SOURCE).encode()).hexdigest()[:8], 16) & 0x7fffffff
    source = (f"#define DYNAMIC_ENABLER_JIT 1\n#define DYNAMIC_ENABLER_TOP_HIT_VERSION {version}\n" +
              TOP_HIT_JIT_PREFIX + TOP_HIT_SOURCE)
    return source, version


def load_top_hit():
    """JIT the native callbacks in memory using debug rights alone; return an error or None."""
    import json
    pid = menubaragent_pid()
    if not pid:
        return "MenuBarAgent is not running"
    workdir = tempfile.mkdtemp(prefix="dynamic_island_top_hit_")
    try:
        native, version = top_hit_jit_source()
        source = os.path.join(workdir, "TopHit.mm")
        loader = os.path.join(workdir, "load_top_hit.py")
        with open(source, "w") as f:
            f.write(native)
        with open(loader, "w") as f:
            f.write(TOP_HIT_LLDB.replace("__SOURCE_PATH__", repr(source)).replace("__VERSION__", str(version)))
        # Catch syntax/ABI declaration failures before attaching or changing any layers.
        built = subprocess.run(["xcrun", "clang", "-fsyntax-only", "-arch", "arm64e", "-fno-objc-arc", source],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        if built.returncode:
            return "native top-edge module failed to compile: " + built.stdout[-1000:].strip()
        loaded = subprocess.run(["xcrun", "lldb", "--batch", "-o", f"process attach --pid {pid}",
                                 "-o", "command script import " + json.dumps(loader)],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=45, start_new_session=True)
        if loaded.returncode:
            return f"lldb exited with status {loaded.returncode} while installing the top-edge module"
        records = [line.partition("DYNAMIC_ENABLER_TOP_HIT ")[2] for line in loaded.stdout.splitlines()
                   if line.startswith("DYNAMIC_ENABLER_TOP_HIT ")]
        if not records:
            return "lldb did not confirm top-edge module installation"
        result = json.loads(records[-1])
        if not isinstance(result, dict):
            return "lldb returned an invalid top-edge installation confirmation"
        if result.get("ok") is True:
            covered = result.get("covered")
            if type(covered) is not int or covered < 0:
                return "lldb did not confirm a valid native top-edge result"
            return None
        problem = result.get("error")
        return problem if isinstance(problem, str) and problem else "native top-edge installation failed"
    except subprocess.TimeoutExpired:
        return "top-edge module installation timed out"
    except (OSError, ValueError) as error:
        return "top-edge module installation failed: " + str(error)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------------------------
# Client processes (expanded widths)
# ---------------------------------------------------------------------------------------------

EXPANDED_W_RANGE = (230.0, 300.0)   # stock expanded widths: timer 260, AirDrop 234/274, Now Playing 286
MIN_HEIGHT = 60.0                   # height of single-row (Min Height) expanded presentations
MIN_SIDE = 100.5                    # single-row: stock side width (260 - 59) / 2, as for timers
TALL_SIDE = REMOTE_SIDE             # taller presentations: side width next to the notch, as for Now Playing
SKIP_CLIENTS = ("WidgetRenderer",)  # third-party WidgetKit content is sized by the app itself

# Per-client side width for single-row presentations with less content.
# Screen recording: the stop button and the elapsed time fit with 90 pt on each side.
CLIENT_MIN_SIDE = {
    "com.apple.screencaptureui.agent": 90.0,
}

# Clients that do not size their panel with .frame(): process name -> (module, binary,
# stock widths, register, side width). Each width is loaded once with `movz reg ; movk reg`.
# AirDrop: one function returns the panel width (274 for several items, 234 for one). Its
# thumbnail needs 94 pt on each side to clear the notch.
CLIENT_EXTRAS = {
    "airdropui": ("AirDropUI", AIRDROPUI, (274.0, 234.0), 8, 94.0),
}
EXTRA_JOBS = {"com.apple.sharing.airdropui": "/System/Library/CoreServices/AirDropUI.app"}  # launchd label -> app
EXTRA_SITES = {}    # located sites per client, reused after the first lookup


def extra_sites(key):
    """-> [(movz address, movk address, register, original instructions)], or None if the
    widths are not each loaded exactly once, in one function, in the installed binary."""
    if key not in EXTRA_SITES:
        _, path, widths, reg, _ = CLIENT_EXTRAS[key]
        sites = []
        try:
            with open(path, "rb") as f:
                m = MachO(arm64e_slice(f.read(), os.path.basename(path)))
            for width in widths:
                origs = mov_double(reg, width)
                at = one(f"width {width:g}", m.find([int.from_bytes(bytes.fromhex(h), "little") for h in origs]))
                sites.append((at, at + 4, reg, origs))
            if len({m.function_start(site[0]) for site in sites}) != 1:
                sites = None
        except (OSError, SignatureError, KeyError, IndexError, struct.error, SystemExit):
            sites = None
        EXTRA_SITES[key] = sites
    return EXTRA_SITES[key]


def frame_params(notch_w, neutral=False):
    """Parameters of the frame hook. neutral=True selects an empty range (no change)."""
    lo, hi = (1e9, 0.0) if neutral else EXPANDED_W_RANGE
    return {"lo": lo, "hi": hi, "min_h": MIN_HEIGHT,
            "w_min": notch_w + 2 * MIN_SIDE, "w_tall": notch_w + 2 * TALL_SIDE}


def client_params(ident, params):
    side = CLIENT_MIN_SIDE.get(ident)
    if side is None or params["lo"] > params["hi"]:
        return params
    return dict(params, w_min=params["w_min"] - 2 * MIN_SIDE + 2 * side)


def client_extras(name, params, neutral=False):
    """-> [module, [[address, original, new], ...]] or None. neutral=True restores the originals."""
    key = name.rsplit(".", 1)[-1]
    if key not in CLIENT_EXTRAS or not extra_sites(key):
        return None
    mod, side, sites = CLIENT_EXTRAS[key][0], CLIENT_EXTRAS[key][-1], extra_sites(key)
    width = params["w_tall"] - 2 * TALL_SIDE + 2 * side
    items = []
    for a_movz, a_movk, reg, origs in sites:
        new = origs if neutral else mov_double(reg, width)
        items += [[a_movz, origs[0], new[0]], [a_movk, origs[1], new[1]]]
    return [mod, items]


FRAMES_LLDB = r'''
import json, lldb, struct

FRAME = "function signature specialization <Arg[3] = Dead> of SwiftUI._FrameLayout.init(width:"
FLEX = "SwiftUI._FlexFrameLayout.init(minWidth:"
TRAMPOLINE = struct.pack("<II", 0x58000050, 0xD61F0200)        # ldr x16, #8 ; br x16 ; .quad target
HEADER = 32                                                    # displaced instructions + jump back
CAVE_SIZE = 256


def bits(v): return struct.unpack("<Q", struct.pack("<d", v))[0]


def assemble(items):
    # items: ("raw", word) / ("label", name) / ("quad", name, value) / (instruction, register, label)
    pos, labels = 0, {}
    for it in items:
        if it[0] == "label": labels[it[1]] = pos
        elif it[0] == "quad": labels[it[1]] = pos; pos += 8
        else: pos += 4
    out, pos = b"", 0
    for it in items:
        if it[0] == "label": continue
        if it[0] == "quad": out += struct.pack("<Q", it[2]); pos += 8; continue
        if it[0] == "raw": w = it[1]
        else:
            off = (labels[it[2]] - pos) >> 2
            if it[0] == "tbnz0": w = 0x37000000 | ((off & 0x3FFF) << 5) | it[1]
            elif it[0] == "ldr": w = 0x58000000 | ((off & 0x7FFFF) << 5) | it[1]
            elif it[0] in ("bne", "blo", "bhi"):
                w = 0x54000000 | ((off & 0x7FFFF) << 5) | {"bne": 1, "blo": 3, "bhi": 8}[it[0]]
            elif it[0] == "b": w = 0x14000000 | (off & 0x3FFFFFF)
        out += struct.pack("<I", w); pos += 4
    return out


def cmp(n, m): return ("raw", 0xEB00001F | (m << 16) | (n << 5))


def cave(kind, P, displaced, back):
    # kind "frame": width x0/w1, height x2/w3. kind "flex": maxWidth x4/w5, minHeight x6/w7,
    # maxHeight on the stack. Widths in [lo, hi] are replaced: height == min_h -> w_min,
    # otherwise w_tall. Positive doubles compare correctly as unsigned integers.
    wreg, wflag = (0, 1) if kind == "frame" else (4, 5)
    it = [("label", "start")] + [("raw", w) for w in displaced]
    it += [("ldr", 16, "back"), ("raw", 0xD61F0200), ("quad", "back", back)]
    it += [("label", "entry"), ("tbnz0", wflag, "orig"),
           ("ldr", 16, "lo"), cmp(wreg, 16), ("blo", 0, "orig"),
           ("ldr", 16, "hi"), cmp(wreg, 16), ("bhi", 0, "orig")]
    if kind == "frame":
        it += [("tbnz0", 3, "tall"), ("ldr", 17, "h"), cmp(2, 17), ("bne", 0, "tall")]
    else:
        it += [("tbnz0", 7, "maxh"), ("ldr", 17, "h"), cmp(6, 17), ("bne", 0, "maxh"), ("b", 0, "min"),
               ("label", "maxh"), ("raw", 0x394063F0), ("tbnz0", 16, "tall"),
               ("raw", 0xF9400BF0), ("ldr", 17, "h"), cmp(16, 17), ("bne", 0, "tall")]
    # With only a maximum width, panels with little content would not grow; pin the minimum too.
    pin = [("raw", 0xAA0403E0), ("raw", 0x52800001)] if kind == "flex" else []
    it += [("label", "min"), ("ldr", wreg, "minv")] + pin + [("b", 0, "orig"),
           ("label", "tall"), ("ldr", wreg, "tallv")] + pin + [
           ("label", "orig"), ("b", 0, "start"),
           ("quad", "h", bits(P["min_h"])), ("quad", "lo", bits(P["lo"])), ("quad", "hi", bits(P["hi"])),
           ("quad", "minv", bits(P["w_min"])), ("quad", "tallv", bits(P["w_tall"]))]
    code = assemble(it)
    assert len(code) <= CAVE_SIZE
    return code


def pc_relative(w):
    return ((w & 0x7C000000) == 0x14000000 or (w & 0x1F000000) == 0x10000000 or (w & 0x3B000000) == 0x18000000
            or (w & 0x7E000000) in (0x34000000, 0x36000000) or (w & 0xFF000010) == 0x54000000)


def find_addrs(t):
    # Both functions are in SwiftUICore (shared cache), so the addresses are the same in every process.
    found = {}
    for m in t.module_iter():
        if m.GetFileSpec().GetFilename() != "SwiftUICore": continue
        for s in m:
            n = s.GetName() or ""
            if n.startswith(FRAME): found["frame"] = s.GetStartAddress().GetLoadAddress(t)
            elif n.startswith(FLEX): found["flex"] = s.GetStartAddress().GetLoadAddress(t)
    return found if len(found) == 2 else None


def install(p, addrs, P):
    e = lldb.SBError()
    pcs = [th.GetFrameAtIndex(0).GetPC() for th in p]
    done = 0
    for kind in ("frame", "flex"):
        fn = addrs[kind]
        head = p.ReadMemory(fn, 16, e)
        if not head: continue
        already = head[:8] == TRAMPOLINE
        if any(fn <= pc < fn + 16 for pc in pcs):
            # A thread is in the first 16 bytes. Never rewrite bytes that are executing; if the
            # function is already hooked it is still in place, so count it rather than failing.
            if already: done += 1
            continue
        if already:                                 # already hooked: reuse the existing cave
            mem = struct.unpack("<Q", head[8:])[0] - HEADER
            displaced = struct.unpack("<4I", p.ReadMemory(mem, 16, e))
        else:
            displaced = struct.unpack("<4I", head)
            if any(pc_relative(w) for w in displaced): continue
            mem = p.AllocateMemory(CAVE_SIZE, lldb.ePermissionsReadable | lldb.ePermissionsExecutable, e)
            if not e.Success(): continue
        code = cave(kind, P, displaced, fn + 16)
        p.WriteMemory(mem, code, e)
        if not e.Success() or p.ReadMemory(mem, len(code), e) != code: continue
        p.WriteMemory(fn, TRAMPOLINE + struct.pack("<Q", mem + HEADER), e)
        done += 1
    return done


def write_extras(t, p, extras):
    # The immediates (bits 5-20) may have been changed by an earlier run; all other bits must match.
    e = lldb.SBError()
    mods = {m.GetFileSpec().GetFilename(): m for m in t.module_iter()}
    mod, items = extras
    if mod not in mods: return False
    todo = []
    for addr, orig, new in items:
        la = mods[mod].ResolveFileAddress(addr).GetLoadAddress(t)
        cur = p.ReadMemory(la, 4, e)
        if not cur or (int.from_bytes(cur, "little") ^ int.from_bytes(bytes.fromhex(orig), "little")) & 0xFFE0001F:
            return False
        todo.append((la, bytes.fromhex(new)))
    for la, new in todo:
        p.WriteMemory(la, new, e)
    return all(p.ReadMemory(la, 4, e) == new for la, new in todo)


def apply(debugger, command, result, d):
    arg = json.loads(command)
    t = debugger.GetSelectedTarget(); p = t.GetProcess()
    addrs = arg.get("addrs") or find_addrs(t)
    if not addrs:
        print("FRAMES_NOSYMBOL"); return
    print("FRAMES_ADDRS " + json.dumps(addrs))
    ok = install(p, addrs, arg["params"]) == 2
    if arg.get("extras"):
        ok = write_extras(t, p, arg["extras"]) and ok
    print("FRAMES_OK" if ok else "FRAMES_FAIL")


def __lldb_init_module(debugger, d):
    debugger.HandleCommand("command script add -f island_frames.apply island_frames")
'''

# Battery banners (system banners shown by ControlCenter, not Live Activities). The expanded size
# is fixed in a SystemBannerUI function used only by the battery banners: charging 220 x 50 and
# low battery 300 x 60. Both branches load the width with `adrp x8 ; ldr d0, [x8, #...]`,
# which is replaced by `movz x8, #hi, lsl #48 ; fmov d0, x8`. Only the top 16 bits can be set, so the
# width is the representable value (16 pt steps) closest to notch width + 2 x side width.
# The 220 and 300 constants themselves are shared with other banner layouts and are not changed.
BANNER_MODULE = "SystemBannerUI"
BANNER_FRAMEWORK = f"/System/Library/PrivateFrameworks/{BANNER_MODULE}.framework/{BANNER_MODULE}"
BANNER_WIDTHS = [   # (stock width, side width)
    (220.0, 80.5),      # charging 220 x 50: stock side width (220 - 59) / 2
    (300.0, MIN_SIDE),  # low battery 300 x 60: single row, as for timers
]
BANNER_SITES = []   # [(adrp address, original instructions, side width)], see banner_sites()


def locate_banner_sites():
    """Prints the two width loads as JSON [[address, [adrp, ldr]], ...] in the order of BANNER_WIDTHS.

    SystemBannerUI exists only in the dyld shared cache, which is mapped at the same addresses
    in every process, so the code is read from this process's own memory. Runs in a child
    process (--banner-sites) because it loads the framework and reads raw memory."""
    import ctypes
    lib = ctypes.CDLL(None)
    ctypes.CDLL(BANNER_FRAMEWORK)
    lib._dyld_get_image_name.restype = ctypes.c_char_p
    lib._dyld_get_image_header.restype = ctypes.c_void_p
    lib._dyld_get_image_vmaddr_slide.restype = ctypes.c_long
    lib._dyld_get_shared_cache_range.restype = ctypes.c_void_p
    length = ctypes.c_size_t()
    cache0 = lib._dyld_get_shared_cache_range(ctypes.byref(length))
    index = next(i for i in range(lib._dyld_image_count())
                 if lib._dyld_get_image_name(i).endswith(("/" + BANNER_MODULE).encode()))
    header, slide = lib._dyld_get_image_header(index), lib._dyld_get_image_vmaddr_slide(index)

    p, text = header + 32, None
    for _ in range(struct.unpack("<I", ctypes.string_at(header + 16, 4))[0]):
        cmd, size = struct.unpack("<II", ctypes.string_at(p, 8))
        if cmd == 0x19 and ctypes.string_at(p + 8, 16).rstrip(b"\0") == b"__TEXT":
            for i in range(struct.unpack("<I", ctypes.string_at(p + 64, 4))[0]):
                sect = p + 72 + 80 * i
                if ctypes.string_at(sect, 16).rstrip(b"\0") == b"__text":
                    text = struct.unpack("<QQ", ctypes.string_at(sect + 32, 16))
        p += size
    words = struct.unpack("<%dI" % (text[1] // 4), ctypes.string_at(text[0] + slide, text[1]))
    found = {width: [] for width, _ in BANNER_WIDTHS}
    for i in range(len(words) - 1):
        w, w2 = words[i], words[i + 1]
        if w & 0x9F00001F == 0x90000008 and w2 & 0xFFC003FF == 0xFD400100:     # adrp x8 ; ldr d0, [x8, #imm]
            pc = text[0] + 4 * i
            imm = (((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3)
            literal = (pc & ~0xFFF) + ((imm - ((imm & 0x100000) << 1)) << 12) + ((w2 >> 10) & 0xFFF) * 8 + slide
            if cache0 <= literal <= cache0 + length.value - 8:
                value = struct.unpack("<d", ctypes.string_at(literal, 8))[0]
                if value in found:
                    found[value].append([pc, [enc(w).hex(), enc(w2).hex()]])
    for width, hits in found.items():
        one(f"banner width {width:g}", [pc for pc, _ in hits])
    sites = [hits[0] for hits in found.values()]
    if abs(sites[0][0] - sites[1][0]) > 0x100:
        raise SignatureError("banner widths are not in one function")
    print("BANNER_SITES " + json.dumps(sites))


def banner_sites():
    """Locates the battery banner sites once. An empty list means they were not found."""
    if not BANNER_SITES:
        try:
            out = subprocess.run([sys.executable, os.path.abspath(__file__), "--banner-sites"], text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        found = re.search(r"^BANNER_SITES (.*)$", out, re.M)
        if found:
            BANNER_SITES.extend((addr, tuple(orig), side) for (addr, orig), (_, side)
                                in zip(json.loads(found.group(1)), BANNER_WIDTHS))
    return BANNER_SITES

BANNER_LLDB = r"""
import json, lldb
def apply(debugger, command, result, d):
    t = debugger.GetSelectedTarget(); p = t.GetProcess(); e = lldb.SBError()
    mods = [m for m in t.module_iter() if m.GetFileSpec().GetFilename() == "%s"]
    if not mods:
        print("BANNER_FAIL nomodule"); return
    todo = []
    for addr, orig_hex, new_hex in json.loads(command):
        la = mods[0].ResolveFileAddress(addr).GetLoadAddress(t)
        cur = p.ReadMemory(la, 8, e) or b""
        w0 = int.from_bytes(cur[:4], "little") if len(cur) == 8 else 0
        ours = (w0 & 0xFFE0001F) == 0xD2E00008 and cur[4:] == bytes.fromhex("0001679e")
        if cur != bytes.fromhex(orig_hex) and not ours:
            print("BANNER_FAIL unexpected %%x %%s" %% (addr, cur.hex())); return
        todo.append((la, bytes.fromhex(new_hex)))
    for la, new in todo:
        p.WriteMemory(la, new, e)
    print("BANNER_OK" if all(p.ReadMemory(la, 8, e) == new for la, new in todo) else "BANNER_FAIL write")
def __lldb_init_module(debugger, d):
    debugger.HandleCommand("command script add -f island_banner.apply island_banner")
""" % BANNER_MODULE


def banner_words(notch_w, side):
    target = notch_w + 2 * side
    hi = struct.unpack("<Q", struct.pack("<d", target))[0] >> 48
    hi = min((hi, hi + 1), key=lambda h: abs(struct.unpack("<d", struct.pack("<Q", h << 48))[0] - target))
    return (enc(0xD2E00000 | (hi << 5) | 8).hex(), enc(0x9E670100).hex())


def run_lldb_module(pid, module_name, source, command):
    """Attaches to pid, runs one command from an lldb Python module and detaches."""
    workdir = tempfile.mkdtemp(prefix="dynamic_island_")
    path = os.path.join(workdir, module_name + ".py")
    with open(path, "w") as f:
        f.write(source)
    try:
        return subprocess.run(
            ["xcrun", "lldb", "--batch", "-o", f"command script import {path}",
             "-o", f"process attach --pid {pid}", "-o", command, "-o", "detach"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120).stdout
    except subprocess.TimeoutExpired:
        return ""
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


FRAME_ADDRS = {}    # SwiftUI frame initializer addresses, reused after the first lookup


def patch_frames(pid, params, name="", neutral=False, tries=4):
    # Returns "ok", "fail", or "skip". "skip" means the process has no SwiftUICore, so it is not a
    # Live Activity SwiftUI client (e.g. a log pid that has since been recycled to another process).
    # A client that is still launching may be executing the frame initializers at attach time, which
    # makes a hook fail transiently; the PC moves on, so retry a few times before giving up.
    arg = json.dumps({"params": client_params(name, params), "addrs": FRAME_ADDRS or None,
                      "extras": client_extras(name, params, neutral)})
    for attempt in range(tries):
        out = run_lldb_module(pid, "island_frames", FRAMES_LLDB, "island_frames " + arg)
        found = re.search(r"^FRAMES_ADDRS (.*)$", out, re.M)
        if found:
            FRAME_ADDRS.update(json.loads(found.group(1)))
        if "FRAMES_OK" in out:
            return "ok"
        if "FRAMES_NOSYMBOL" in out:
            return "skip"       # no SwiftUICore in this process; not a client, retrying will not help
        try:
            os.kill(pid, 0)
        except OSError:
            return "skip"       # the client exited
        if attempt + 1 < tries:
            time.sleep(0.7)
    return "fail"


def control_center_pid():
    out = run("pgrep", "-x", "ControlCenter").stdout.split()
    return int(out[0]) if out else 0


def patch_banner(notch_w, neutral=False):
    pid = control_center_pid()
    if not pid or not banner_sites():
        return False
    items = [[addr, "".join(orig), "".join(orig if neutral else banner_words(notch_w, side))]
             for addr, orig, side in banner_sites()]
    return "BANNER_OK" in run_lldb_module(pid, "island_banner", BANNER_LLDB, "island_banner " + json.dumps(items))


CLIENT_LOG_PREDICATE = ('process == "MenuBarAgent" AND subsystem == "com.apple.FrontBoard" AND '
                        'eventMessage CONTAINS "liveactivity-" AND eventMessage CONTAINS "Client process connected"')


def parse_clients(out):
    """Live Activity client processes in MenuBarAgent log output -> {pid: bundle identifier}."""
    found = {}
    pattern = r"Client process connected: \[\w+<(?:application\.)?([^>]+?)(?:\.\d+\.\d+)?\(\d+\)>:(\d+)\]"
    for name, pid in re.findall(pattern, out):
        pid = int(pid)
        if any(s in name for s in SKIP_CLIENTS):
            continue
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        found[pid] = name
    return found


def live_activity_clients(last="12h"):
    return parse_clients(run("/usr/bin/log", "show", "--last", last, "--style", "compact",
                             "--predicate", CLIENT_LOG_PREDICATE).stdout)


def extra_clients(launch=False):
    """Running launchd agents with dedicated patches -> {pid: label}.

    These agents start on demand (AirDropUI with the first transfer) and lay out their first
    panel before a patch can arrive. launch=True starts them ahead of time for that reason."""
    found = {}
    for label, app in EXTRA_JOBS.items():
        job = run("launchctl", "print", f"{gui_domain()}/{label}")
        match = re.search(r"^\s*pid = (\d+)$", job.stdout, re.M)
        if launch and job.returncode == 0 and not match:
            # launchctl cannot start these agents; opening the app starts the same launchd job
            # in the background without showing anything.
            run("open", "-gj", app)
            time.sleep(1)
            job = run("launchctl", "print", f"{gui_domain()}/{label}")
            match = re.search(r"^\s*pid = (\d+)$", job.stdout, re.M)
        if job.returncode == 0 and match:
            found[int(match.group(1))] = label
    return found


def patch_clients(notch_w, neutral=False, report=print):
    params = frame_params(notch_w, neutral)
    done = "restored" if neutral else "patched"
    clients = live_activity_clients()
    clients.update(extra_clients(launch=not neutral))
    for pid, name in clients.items():
        status = patch_frames(pid, params, name, neutral)
        if status == "skip":        # recycled pid or non-SwiftUI process; not a real client
            continue
        report(f"  {name}: {done if status == 'ok' else 'failed'}")
    ok = patch_banner(notch_w, neutral)
    report(f"  Battery banners (ControlCenter): {done if ok else 'failed'}")


# ---------------------------------------------------------------------------------------------
# Background helper: patches client processes that start after the script has finished
# ---------------------------------------------------------------------------------------------

def log_line(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")


def builtin_display_state():
    """(width, height) in points of the active built-in display, or None. Cheap enough to poll:
    the notch size in points, and with it every patch constant, changes with the display
    resolution, and the built-in display disappears when the lid is closed."""
    import ctypes

    class CGRect(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double),
                    ("w", ctypes.c_double), ("h", ctypes.c_double)]
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cg.CGDisplayBounds.restype = CGRect
    cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    cg.CGDisplayIsBuiltin.argtypes = [ctypes.c_uint32]
    ids, count = (ctypes.c_uint32 * 16)(), ctypes.c_uint32(0)
    if cg.CGGetActiveDisplayList(16, ids, ctypes.byref(count)):
        return "unknown"
    for i in range(count.value):
        if cg.CGDisplayIsBuiltin(ids[i]):
            r = cg.CGDisplayBounds(ids[i])
            return (r.w, r.h)
    return None


def start_reapply(layout, displays):
    """Runs --reapply as its own launchd job: it restarts MenuBarAgent and replaces the watcher
    that started it, so it must not belong to the watcher's job."""
    run("launchctl", "bootout", f"{gui_domain()}/{REAPPLY_LABEL}")
    workdir = tempfile.mkdtemp(prefix="dynamic_island_")
    plist = os.path.join(workdir, REAPPLY_LABEL + ".plist")
    with open(plist, "wb") as f:
        plistlib.dump(launchd_job(REAPPLY_LABEL, [PYTHON, os.path.abspath(__file__), "--reapply",
                                                  "--layout", layout, "--displays", displays], WATCHER_LOG), f)
    ok = run("launchctl", "bootstrap", gui_domain(), plist).returncode == 0
    shutil.rmtree(workdir, ignore_errors=True)
    return ok


def run_watcher(mba_pid, layout, patch_widths, displays):
    """Patches Live Activity clients that start later (layout "notch" only) and reapplies all
    patches with the requested layout when the built-in display changes (resolution, lid)."""
    import select

    def note(text):
        log_line(WATCHER_LOG, text)

    def on_signal(signum, _frame):
        note(f"Received {signal.Signals(signum).name}; exiting.")
        sys.exit(0)
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, on_signal)

    geometry = notch_geometry() if patch_widths else None
    if patch_widths and not geometry:
        note("No built-in display with a notch was found; client widths are left unchanged.")
    params = frame_params(geometry[0]) if geometry else None
    display = [builtin_display_state(), None]      # state the patches were built for, pending change
    done = set()
    banner_pid = [0]

    def handle(clients):
        if not params:
            return
        for pid, name in clients.items():
            if pid not in done:
                done.add(pid)
                status = patch_frames(pid, params, name)
                if status == "skip":        # recycled pid or non-SwiftUI process; not a real client
                    continue
                note(f"{name} (pid {pid}): {'patched' if status == 'ok' else 'failed'}")

    def check_banner():         # reapply when ControlCenter restarts
        if not params:
            return
        pid = control_center_pid()
        if pid and pid != banner_pid[0]:
            banner_pid[0] = pid
            ok = patch_banner(geometry[0])
            note(f"Battery banners (ControlCenter, pid {pid}): {'patched' if ok else 'failed'}")

    def display_changed():
        """True once a changed built-in display state has been seen on two checks in a row."""
        state = builtin_display_state()
        if state == "unknown" or state == display[0]:
            display[1] = None
            return False
        if state != display[1]:
            display[1] = state
            return False
        return True

    note(f"Started for MenuBarAgent pid {mba_pid} (layout {layout}; displays {displays}; built-in display {display[0]}).")
    stream = subprocess.Popen(
        ["/usr/bin/log", "stream", "--style", "compact", "--predicate", CLIENT_LOG_PREDICATE],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    try:
        handle(live_activity_clients())
        handle(extra_clients(launch=True))
        check_banner()
        while True:
            try:
                os.kill(mba_pid, 0)
            except OSError:
                note("MenuBarAgent has exited; exiting.")
                return
            if display_changed():
                note(f"The built-in display changed to {display[1]}; reapplying the patches.")
                if not start_reapply(layout, displays):
                    note("The patches could not be reapplied; run the script again.")
                return
            check_banner()
            handle(extra_clients())
            if select.select([stream.stdout], [], [], 5)[0]:
                line = stream.stdout.readline()
                if not line:
                    note("The log stream ended; exiting.")
                    return
                handle(parse_clients(line))
    finally:
        stream.kill()


def launchd_job(label, arguments, log):
    return {"Label": label, "ProgramArguments": arguments, "RunAtLoad": True, "KeepAlive": False,
            "LimitLoadToSessionType": "Aqua", "WorkingDirectory": os.path.dirname(arguments[1]),
            "StandardOutPath": log, "StandardErrorPath": log}


def start_watcher(mba_pid, layout, patch_widths, displays):
    """Runs the helper as a launchd job so that it does not end with the terminal session."""
    stop_watcher()
    os.makedirs(LOG_DIR, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="dynamic_island_")
    plist = os.path.join(workdir, WATCHER_LABEL + ".plist")
    with open(plist, "wb") as f:
        plistlib.dump(launchd_job(WATCHER_LABEL, [PYTHON, os.path.abspath(__file__),
                                                  "--watcher", str(mba_pid), "--layout", layout,
                                                  "--displays", displays]
                                     + ([] if patch_widths else ["--no-client-widths"]), WATCHER_LOG), f)
    ok = run("launchctl", "bootstrap", gui_domain(), plist).returncode == 0
    shutil.rmtree(workdir, ignore_errors=True)
    return ok


def stop_watcher():
    run("launchctl", "bootout", f"{gui_domain()}/{WATCHER_LABEL}")
    me = os.getpid()
    for pid in run("pgrep", "-f", f"{SCRIPT_NAME} --watcher").stdout.split():
        if int(pid) != me:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


# ---------------------------------------------------------------------------------------------
# Automatic activation at login
# ---------------------------------------------------------------------------------------------

def install_autostart(layout, watch, restart_siri, displays):
    """Copies the script to Application Support and registers a login LaunchAgent."""
    os.makedirs(SUPPORT_DIR, exist_ok=True)
    installed = os.path.join(SUPPORT_DIR, SCRIPT_NAME)
    if os.path.abspath(__file__) != installed:
        shutil.copy2(os.path.abspath(__file__), installed)
    arguments = [PYTHON, installed, "--login", "--layout", layout, "--displays", displays] + ([] if watch else ["--no-watch"]) \
        + (["--restart-siri"] if restart_siri else [])
    os.makedirs(os.path.dirname(AUTOSTART_PLIST), exist_ok=True)
    with open(AUTOSTART_PLIST, "wb") as f:
        plistlib.dump(launchd_job(AUTOSTART_LABEL, arguments, AUTOSTART_LOG), f)


def autostart_restarts_siri():
    """Whether the installed login item restarts Siri AI."""
    try:
        with open(AUTOSTART_PLIST, "rb") as f:
            return "--restart-siri" in plistlib.load(f).get("ProgramArguments", [])
    except (OSError, plistlib.InvalidFileException):
        return False


def autostart_displays():
    """The display mode of the installed login item ("main" if there is none)."""
    try:
        with open(AUTOSTART_PLIST, "rb") as f:
            arguments = plistlib.load(f).get("ProgramArguments", [])
    except (OSError, plistlib.InvalidFileException):
        return "main"
    i = arguments.index("--displays") + 1 if "--displays" in arguments else 0
    return arguments[i] if 0 < i < len(arguments) and arguments[i] in DISPLAY_MODES else "main"


def remove_autostart():
    removed = os.path.exists(AUTOSTART_PLIST)
    run("launchctl", "bootout", f"{gui_domain()}/{AUTOSTART_LABEL}")
    for path in (AUTOSTART_PLIST, os.path.join(SUPPORT_DIR, SCRIPT_NAME)):
        if os.path.exists(path):
            os.remove(path)
    if os.path.isdir(SUPPORT_DIR) and not os.listdir(SUPPORT_DIR):
        os.rmdir(SUPPORT_DIR)
    return removed


# ---------------------------------------------------------------------------------------------
# Enable and disable
# ---------------------------------------------------------------------------------------------

def set_defaults():
    """Turns both preferences on and returns a function that restores the previous values."""
    previous = {}
    for key in DEFAULTS:
        prev = run("defaults", "read", DOMAIN, key)
        if prev.returncode == 0 and prev.stdout.strip() not in ("0", "1"):
            fail(f"The existing preference {key} has an unexpected value.")
        previous[key] = prev
        if run("defaults", "write", DOMAIN, key, "-bool", "true").returncode:
            fail(f"The preference {key} could not be written.")

    def restore():
        for key, prev in previous.items():
            if prev.returncode:
                run("defaults", "delete", DOMAIN, key)
            else:
                run("defaults", "write", DOMAIN, key, "-bool", "true" if prev.stdout.strip() == "1" else "false")
    return restore


SIRI_AI = "Siri AI"      # process name of /System/Applications/Siri AI.app, which also draws Spotlight


def restart_siri_ai():
    """Quits Siri AI so it reconnects to the patched MenuBarAgent; macOS relaunches it on demand."""
    if run("pgrep", "-x", SIRI_AI).returncode:
        return "Siri AI is not running; it will use the island when it starts."
    if run("killall", SIRI_AI).returncode:
        return "Siri AI could not be restarted. Run: killall \"Siri AI\""
    return "Siri AI was restarted so that it reconnects to the island (Siri AI and Spotlight)."


def enable(layout, watch, restart_siri=False, displays="main", report=print):
    requested = layout
    geometry = notch_geometry()
    fallback = layout != "classic" and not geometry
    if fallback:
        # Macs without a notch, a closed lid, or external displays only: the notch layouts have no
        # notch to fit, so the island keeps its stock position below the menu bar. The login item
        # keeps the requested layout and checks again at every login.
        report(f"No active display with a notch was found; using layout \"classic\" instead of \"{layout}\".")
        layout = "classic"
    patches, sites = build_patches(layout, geometry, displays)
    if not sites.verified:
        report(f"Note: this macOS build ({run('sw_vers', '-buildVersion').stdout.strip()}) has not been "
               "tested. The patch sites were located by their code signatures.")
    restore = set_defaults()

    # Clients are patched first: expanded views are rebuilt when MenuBarAgent recreates its scenes.
    report("Patching Live Activity clients:" if layout == "notch" else "Restoring stock client widths:")
    patch_clients(geometry[0] if geometry else 0.0, neutral=layout != "notch", report=report)

    report("Restarting and patching MenuBarAgent...")
    success, output = patch_menubar(patches)
    if not success:
        restore()
        report(output[-2000:])
        fail("The MenuBarAgent patch could not be confirmed. The previous preferences were restored.")

    top_hit_note = None
    if layout != "classic":
        time.sleep(3)       # let the new MenuBarAgent finish launching before loading the module
        problem = load_top_hit()
        top_hit_note = ("Top-edge click handling is enabled (in memory; debugging rights only)." if not problem else
                        f"Top edge clicks were not enabled ({problem}); everything else works.")

    siri_note = restart_siri_ai() if restart_siri else None

    stop_watcher()
    watcher_note = None
    if watch:
        pid = menubaragent_pid()
        watcher_note = (f"Background helper started (log: {WATCHER_LOG})." if pid and start_watcher(pid, requested, layout == "notch", displays)
                        else "The background helper could not be started; clients that start later keep "
                             "their stock widths, and display changes need a new run of this script.")

    description = {"notch": "expanded presentations wrap around the notch",
                   "below": "expanded presentations are placed below the notch",
                   "classic": "stock position below the menu bar"}[layout]
    report(f"The Dynamic Island is enabled ({description}; {len(patches)} patches).")
    report({"main": "Displays: the main display only.",
            "all": "Displays: every active display. Displays other than the built-in one use the stock position.",
            "secondary": "Displays: every display except the main one, which keeps the ordinary menu bar."}[displays])
    if sites.multidisplay and not sites.multidisplay.verified:
        report("Note: the multi-display patch has not been tested on this build.")
    if sites.multidisplay and not sites.multidisplay.banner:
        report("System banners appear only on their preferred display on this build.")
    if geometry:
        report(f"Notch: {geometry[0]:g} x {geometry[1]:g} pt.")
    if top_hit_note:
        report(top_hit_note)
    if siri_note:
        report(siri_note)
    if watcher_note:
        report(watcher_note)


def disable(report=print):
    stop_watcher()
    run("launchctl", "bootout", f"{gui_domain()}/{REAPPLY_LABEL}")
    if remove_autostart():
        report("Automatic activation at login has been removed.")

    if menubaragent_pid():
        report("Restoring stock client widths:")
        patch_clients(0.0, neutral=True, report=report)

    for key in DEFAULTS:
        run("defaults", "delete", DOMAIN, key)
    run("killall", "MenuBarAgent")      # launchd relaunches it without patches
    remove_top_hit_files()
    report("The Dynamic Island is disabled. MenuBarAgent has been restarted with its stock behavior.")


# ---------------------------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------------------------

def ask(question, options):
    """Presents numbered options and returns the key of the selected one."""
    print(question)
    for i, (_, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        try:
            answer = input(f"Select 1-{len(options)}: ").strip()
        except EOFError:
            fail("No selection was made.")
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            print()
            return options[int(answer) - 1][0]
        print("Please enter the number of one of the options.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Enables the macOS 27.3 beta Dynamic Island on MacBook Pro models with a notch.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--enable", action="store_true", help="enable the Dynamic Island")
    action.add_argument("--disable", action="store_true",
                        help="disable the Dynamic Island and remove automatic activation")
    startup = parser.add_mutually_exclusive_group()
    startup.add_argument("--autostart", dest="autostart", action="store_true", default=None,
                         help="apply the patches automatically at every login")
    startup.add_argument("--no-autostart", dest="autostart", action="store_false",
                         help="do not apply the patches at login")
    parser.add_argument("--displays", choices=DISPLAY_MODES, default=None,
                        help="main: the island on the main display only (default); all: on every active "
                             "display; secondary: on every display except the main one")
    parser.add_argument("--layout", choices=LAYOUTS, default="notch",
                        help="notch: wrap around the notch (default); below: expanded presentations "
                             "below the notch; classic: activation only, used automatically when no display "
                             "with a notch is active")
    siri = parser.add_mutually_exclusive_group()
    siri.add_argument("--restart-siri", dest="restart_siri", action="store_true", default=None,
                      help="restart Siri AI after patching so that Siri AI and Spotlight appear in the "
                           "island (no effect if Siri AI is off or not available)")
    siri.add_argument("--no-restart-siri", dest="restart_siri", action="store_false",
                      help="leave Siri AI running as it is")
    parser.add_argument("--no-watch", action="store_true",
                        help="do not start the background helper (patches clients that start later and "
                             "reapplies the patches when the built-in display changes)")
    parser.add_argument("--watcher", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--no-client-widths", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--reapply", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--login", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--banner-sites", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def login_run(args):
    """Entry point of the login LaunchAgent."""
    def report(text):
        log_line(AUTOSTART_LOG, text)
    report("Login activation started.")
    deadline = time.time() + 120
    while not menubaragent_pid() and time.time() < deadline:
        time.sleep(2)
    if not menubaragent_pid():
        report("MenuBarAgent did not start within two minutes.")
        sys.exit(1)
    time.sleep(5)       # let the login session settle before restarting MenuBarAgent
    try:
        enable(args.layout, not args.no_watch, bool(args.restart_siri), args.displays or "main", report=report)
    except SystemExit as exc:
        report(str(exc))
        raise


def reapply_run(args):
    """Started by the watcher after the built-in display changed."""
    def report(text):
        log_line(WATCHER_LOG, "[reapply] " + text)
    time.sleep(3)       # let the display configuration settle
    try:
        enable(args.layout, True, False, args.displays or "main", report=report)
    except SystemExit as exc:
        report(str(exc))
        raise


def main():
    args = parse_args()

    if args.watcher:
        run_watcher(args.watcher, args.layout, not args.no_client_widths, args.displays or "main")
        return
    if args.reapply:
        reapply_run(args)
        return
    if args.banner_sites:
        locate_banner_sites()
        return

    check_sip()
    check_environment()
    try:
        check_debug_access()
    except SystemExit as exc:
        if args.login:
            log_line(AUTOSTART_LOG, str(exc))
        raise

    if args.login:
        login_run(args)
        return

    interactive = not (args.enable or args.disable)
    if interactive and not sys.stdin.isatty():
        fail("Run the script in a terminal, or pass --enable or --disable.")

    action = "enable" if args.enable else "disable" if args.disable else ask(
        "What would you like to do?", [("enable", "Enable the Dynamic Island"),
                                       ("disable", "Disable the Dynamic Island")])
    if action == "disable":
        disable()
        return

    restart_siri = args.restart_siri    # None: keep the login item's setting (non-interactive use only)
    if restart_siri is None and interactive:
        restart_siri = ask("Restart Siri AI so that Siri AI and Spotlight appear in the Dynamic Island?\n"
                           "(If Siri AI is off or not available on this Mac, either choice is fine.)",
                           [(True, "Yes, restart Siri AI"),
                            (False, "No, leave Siri AI as it is")])
    if restart_siri is None:
        restart_siri = autostart_restarts_siri()

    displays = args.displays        # None: keep the login item's setting (non-interactive use only)
    if displays is None and interactive:
        displays = ask("Where should the Dynamic Island appear?",
                       [("main", "On the main display only"),
                        ("all", "On every active display (external displays, Sidecar)"),
                        ("secondary", "On every display except the main one")])
    if displays is None:
        displays = autostart_displays()

    autostart = args.autostart      # None: keep the current setting (non-interactive use only)
    if autostart is None and interactive:
        autostart = ask("Apply the Dynamic Island automatically at every login?",
                        [(True, "Yes, apply it at login"),
                         (False, "No, apply it only now (run this script again after a restart)")])

    enable(args.layout, not args.no_watch, restart_siri, displays)
    if autostart is None:
        autostart = os.path.exists(AUTOSTART_PLIST)
        if autostart:
            install_autostart(args.layout, not args.no_watch, restart_siri, displays)   # refresh the installed copy
    elif autostart:
        install_autostart(args.layout, not args.no_watch, restart_siri, displays)
        print(f"Automatic activation at login is enabled (log: {AUTOSTART_LOG}).")
        print("macOS may show a \"Background Items Added\" notification for this login item.")
    elif remove_autostart():
        print("Automatic activation at login has been removed.")
    if not autostart:
        print("The patches are lost when the Mac or MenuBarAgent restarts. Run this script again to reapply them.")
    print("Play music or start a timer to see the Dynamic Island.")


if __name__ == "__main__":
    main()
