#!/usr/bin/env python3
"""vncserve - a tiny RFB (VNC) server over WebSocket for the B860AV1.1-T.

Why not a normal VNC server: Lakka has no X11 and no Wayland, so x11vnc/wayvnc
cannot exist here. Instead a small C helper (drmcap) captures the DRM scanout
buffer - the real HDMI picture, including RetroArch's GL output and menus - and
publishes RGB frames to /dev/shm. This file serves those frames to noVNC and
injects mouse/keyboard through /dev/uinput.

Endpoints:
    GET /            -> redirect to vnc_lite.html
    GET /*           -> static files from the noVNC directory
    GET /websockify  -> WebSocket carrying the RFB streams

Supported: RFB 3.8, security type None, Raw encoding, 16bpp or 32bpp client
pixel formats. One thread per client.
"""
import base64
import hashlib
import mmap
import os
import socket
import struct
import sys
import threading
import time
import zlib
import traceback

SHM_PATH = "/dev/shm/drmcap.bin"
WEB_ROOT = "/storage/novnc"
LISTEN = ("0.0.0.0", 6080)

CAP_MAGIC = 0x43415032
HDR_FMT = "<10I6I"
HDR_SIZE = 64

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# --------------------------------------------------------------------------
# frame source
# --------------------------------------------------------------------------
class FrameSource:
    def __init__(self, path=SHM_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.mm = None
        self.w = self.h = 0
        self.len32 = self.len16 = self.lenrgb = 0
        self.off32 = HDR_SIZE
        self.off16 = 0
        self.seq = 0
        self.bx = self.by = self.bw = self.bh = 0
        self._open()

    def _open(self):
        for _ in range(120):
            try:
                fd = os.open(self.path, os.O_RDONLY)
                st = os.fstat(fd)
                if st.st_size >= HDR_SIZE + 24:
                    self.mm = mmap.mmap(fd, st.st_size, mmap.MAP_SHARED,
                                        mmap.PROT_READ)
                    os.close(fd)
                    magic, seq, w, h, l32, l16, bx, by, bw, bh, p0, p1 = \
                        struct.unpack_from("<12I", self.mm, 0)
                    if magic == CAP_MAGIC and w and h:
                        self.w, self.h = w, h
                        self.len32, self.len16, self.lenrgb = l32, l16, p1
                        self.off16 = HDR_SIZE + l32
                        self.seq = seq
                        print(f"[cap] {w}x{h} len32={l32} len16={l16} "
                              f"lenrgb={p1}", flush=True)
                        return
                else:
                    os.close(fd)
            except FileNotFoundError:
                pass
            time.sleep(0.5)
        raise RuntimeError("drmcap shared memory not available; is drmcap running?")

    def read_stable(self):
        """双缓冲版本：返回 (seq, bbox, cur, bytes32)。cur 指向已发布稳定缓冲。"""
        for _ in range(60):
            s0 = struct.unpack_from("<I", self.mm, 4)[0]
            if s0 == self.seq:
                time.sleep(0.003)
                continue
            bx, by, bw, bh = struct.unpack_from("<4I", self.mm, 24)
            cur = struct.unpack_from("<I", self.mm, 40)[0]
            step = self.len32 + self.len16 + self.lenrgb
            base = self.off32 + cur * step
            d32 = self.mm[base:base + self.len32]
            drgb = self.mm[base + self.len32 + self.len16:
                           base + self.len32 + self.len16 + self.lenrgb]
            s1 = struct.unpack_from("<I", self.mm, 4)[0]
            if s0 != s1:
                continue
            self.seq = s0
            self.bx, self.by, self.bw, self.bh = bx, by, bw, bh
            return s0, (bx, by, bw, bh), d32, drgb
        return self.seq, (0, 0, 0, 0), b"", b""

    def snapshot(self):
        """Return (seq, w, h, bbox, bytes32, bytes16) of the current frame."""
        with self.lock:
            for _ in range(50):
                s0 = struct.unpack_from("<I", self.mm, 4)[0]
                if s0 == self.seq:
                    time.sleep(0.005)
                    continue
                bx, by, bw, bh = struct.unpack_from("<4I", self.mm, 24)
                d32 = self.mm[self.off32:self.off32 + self.len32]
                d16 = self.mm[self.off16:self.off16 + self.len16]
                s1 = struct.unpack_from("<I", self.mm, 4)[0]
                if s0 != s1:
                    continue
                self.seq = s0
                self.bx, self.by, self.bw, self.bh = bx, by, bw, bh
                return s0, self.w, self.h, (bx, by, bw, bh), d32, d16
            return self.seq, self.w, self.h, (0, 0, 0, 0), b"", b""


# --------------------------------------------------------------------------
# input injection
# --------------------------------------------------------------------------
class UInput:
    UI_DEV_CREATE = 0x5501
    UI_DEV_DESTROY = 0x5502
    UI_SET_EVBIT = 0x40045564
    UI_SET_KEYBIT = 0x40045565
    UI_SET_RELBIT = 0x40045566
    UI_SET_ABSBIT = 0x40045567
    UI_DEV_SETUP = 0x405C5503

    EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
    SYN_REPORT = 0
    REL_X, REL_Y, REL_WHEEL = 0, 1, 8
    ABS_X, ABS_Y = 0, 1

    KEY_CODES = [
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
        21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38,
        39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56,
        57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74,
        75, 76, 77, 78, 79, 80, 81, 82, 83, 85, 86, 87, 88, 89, 90, 91, 92, 93,
        94, 95, 96, 97, 98, 99, 100, 102, 103, 104, 105, 106, 107, 108, 109,
        110, 111, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124,
        125, 126, 127,
    ]

    def __init__(self, screen_w, screen_h):
        self.sw, self.sh = screen_w, screen_h
        # capture size vs real scanout size (relative motion is in screen pixels)
        self.screen_w, self.screen_h = 1920, 1080
        try:
            e = os.environ.get("VNC_SCREEN", "1920x1080").lower().split("x")
            self.screen_w, self.screen_h = int(e[0]), int(e[1])
        except Exception:
            pass
        self.rx = self.screen_w / float(screen_w)
        self.ry = self.screen_h / float(screen_h)
        # RetroArch's udev driver divides relative Y by the viewport height,
        # which on this KMS setup comes out at half the screen height.  Keep it
        # tunable instead of hard-coding the compensation.
        try:
            self.ry_fix = float(os.environ.get("VNC_MOUSE_YFIX", "2"))
        except Exception:
            self.ry_fix = 1.0
        self.cur_x = 0
        self.cur_y = 0
        self.homed = False
        self.kbd = self._make("vnc-keyboard", keys=True)
        self.mouse = self._make("vnc-mouse", rel=True, mouse=True)
        self.last_x = 0
        self.last_y = 0

    def _make(self, name, keys=False, rel=False, abs_axes=False, mouse=False):
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        import fcntl
        fcntl.ioctl(fd, self.UI_SET_EVBIT, self.EV_SYN)
        if keys:
            fcntl.ioctl(fd, self.UI_SET_EVBIT, self.EV_KEY)
            for c in self.KEY_CODES:
                fcntl.ioctl(fd, self.UI_SET_KEYBIT, c)
        if mouse:
            # keyboard key codes stop at 127; BTN_* must be declared or the
            # kernel silently drops every click.  EV_KEY has to be enabled
            # first, otherwise these bits are ignored.
            fcntl.ioctl(fd, self.UI_SET_EVBIT, self.EV_KEY)
            for c in (0x110, 0x111, 0x112):      # BTN_LEFT / RIGHT / MIDDLE
                fcntl.ioctl(fd, self.UI_SET_KEYBIT, c)
        if rel:
            fcntl.ioctl(fd, self.UI_SET_EVBIT, self.EV_REL)
            for a in (self.REL_X, self.REL_Y, self.REL_WHEEL):
                fcntl.ioctl(fd, self.UI_SET_RELBIT, a)
        if abs_axes:
            fcntl.ioctl(fd, self.UI_SET_EVBIT, self.EV_ABS)
            for a in (self.ABS_X, self.ABS_Y):
                fcntl.ioctl(fd, self.UI_SET_ABSBIT, a)
        # struct uinput_setup { input_id{4x u16}; char name[80]; u32 ff_max; }
        # struct uinput_abs_setup { u16 code; pad; input_absinfo{6x s32} }
        setup = struct.pack("<4H80sI", 0x03, 0x1234, 0x5678, 1,
                            name.encode()[:79], 0)
        fcntl.ioctl(fd, self.UI_DEV_SETUP, setup)
        if abs_axes:
            for code, mx in ((self.ABS_X, self.sw - 1), (self.ABS_Y, self.sh - 1)):
                # input_absinfo = value, minimum, maximum, fuzz, flat, resolution
                absinfo = struct.pack("<6i", 0, 0, mx, 0, 0, 0)
                # _IOW('U', 0x04, struct uinput_abs_setup): 2+2+24 = 28 bytes
                fcntl.ioctl(fd, 0x401C5504, struct.pack("<H2x", code) + absinfo)
        fcntl.ioctl(fd, self.UI_DEV_CREATE)
        return fd

    @staticmethod
    def _ev(fd, etype, code, value):
        t = time.time()
        os.write(fd, struct.pack("<qqHHi", int(t), int((t % 1) * 1e6),
                                 etype, code, value))

    def key(self, code, down):
        self._ev(self.kbd, self.EV_KEY, code, 1 if down else 0)
        self._ev(self.kbd, self.EV_SYN, self.SYN_REPORT, 0)

    def home(self):
        """Pin the pointer to (0,0); RetroArch clamps relative motion at edges."""
        for _ in range(2):
            self._ev(self.mouse, self.EV_REL, self.REL_X, -4000)
            self._ev(self.mouse, self.EV_REL, self.REL_Y, -4000)
            self._ev(self.mouse, self.EV_SYN, self.SYN_REPORT, 0)
        self.cur_x = 0
        self.cur_y = 0
        self.homed = True

    def move(self, x, y):
        if not self.homed:
            self.home()
        x = max(0.0, min(float(self.sw - 1), float(x)))
        y = max(0.0, min(float(self.sh - 1), float(y)))
        tx = int(round(x * self.rx))
        ty = int(round(y * self.ry * self.ry_fix))

        if x <= 1:                  # slam into the edge to re-sync the pointer
            self._ev(self.mouse, self.EV_REL, self.REL_X, -4000)
            self.cur_x = 0
        elif x >= self.sw - 2:
            self._ev(self.mouse, self.EV_REL, self.REL_X, 4000)
            self.cur_x = self.screen_w - 1
        else:
            dx = tx - self.cur_x
            if dx:
                self._ev(self.mouse, self.EV_REL, self.REL_X, dx)
            self.cur_x = tx

        if y <= 1:
            self._ev(self.mouse, self.EV_REL, self.REL_Y, -4000)
            self.cur_y = 0
        elif y >= self.sh - 2:
            self._ev(self.mouse, self.EV_REL, self.REL_Y, 4000)
            self.cur_y = self.screen_h - 1
        else:
            dy = ty - self.cur_y
            if dy:
                self._ev(self.mouse, self.EV_REL, self.REL_Y, dy)
            self.cur_y = ty

        self._ev(self.mouse, self.EV_SYN, self.SYN_REPORT, 0)
        self.last_x, self.last_y = x, y

    def button(self, mask):
        for bit, code in ((1, 0x110), (2, 0x112), (4, 0x111)):
            self._ev(self.mouse, self.EV_KEY, code, 1 if mask & bit else 0)
        self._ev(self.mouse, self.EV_SYN, self.SYN_REPORT, 0)

    def wheel(self, delta):
        self._ev(self.mouse, self.EV_REL, self.REL_WHEEL, delta)
        self._ev(self.mouse, self.EV_SYN, self.SYN_REPORT, 0)


KEYSYM_MAP = {
    0xFF08: 14, 0xFF09: 15, 0xFF0D: 28, 0xFF1B: 1,
    0xFF51: 105, 0xFF52: 103, 0xFF53: 106, 0xFF54: 108,
    0xFF50: 102, 0xFF57: 107, 0xFF55: 104, 0xFF56: 109,
    0xFF63: 110, 0xFFFF: 111, 0xFF8D: 96,
    0xFFE1: 42, 0xFFE2: 54, 0xFFE3: 29, 0xFFE4: 97, 0xFFE9: 56, 0xFFEA: 100,
    0xFFEB: 125, 0xFFE5: 58,
    0x20: 57, 0x2D: 12, 0x3D: 13, 0x5B: 26, 0x5D: 27, 0x5C: 43,
    0x3B: 39, 0x27: 40, 0x60: 41, 0x2C: 51, 0x2E: 52, 0x2F: 53,
    0x09: 15, 0x0D: 28,
}
for i, kc in enumerate([59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 87, 88]):
    KEYSYM_MAP[0xFFBE + i] = kc
DIGITS = {0x30: 11, 0x31: 2, 0x32: 3, 0x33: 4, 0x34: 5,
          0x35: 6, 0x36: 7, 0x37: 8, 0x38: 9, 0x39: 10}
KEYSYM_MAP.update(DIGITS)
LETTERS = dict(zip(range(0x61, 0x7B),
                   [30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50,
                    49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45, 21, 44]))
KEYSYM_MAP.update(LETTERS)
for k, v in list(LETTERS.items()):
    KEYSYM_MAP[k - 0x20] = v


def keysym_to_keycode(sym):
    if sym in KEYSYM_MAP:
        return KEYSYM_MAP[sym]
    if 0x61 <= sym <= 0x7A:
        return LETTERS.get(sym, 0)
    return 0


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------
class WS:
    """WebSocket transport that exposes a plain byte stream to RFB.

    RFB has no message boundaries of its own, so the de-framed payload is kept in
    a stream buffer and callers simply ask for N bytes. Reading raw socket bytes
    here would hand RFB frame headers instead of protocol data - that was the
    first bug in this server.
    """

    def __init__(self, sock, leftover=b""):
        self.sock = sock
        self.raw = bytearray(leftover)
        self.pl = bytearray()
        self.send_lock = threading.Lock()

    def _raw_read(self, n):
        while len(self.raw) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("eof")
            self.raw += chunk
        out = bytes(self.raw[:n])
        del self.raw[:n]
        return out

    def _pump(self):
        h = self._raw_read(2)
        op = h[0] & 0x0F
        masked = h[1] & 0x80
        ln = h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._raw_read(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._raw_read(8))[0]
        mask = self._raw_read(4) if masked else None
        data = self._raw_read(ln) if ln else b""
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if op == 0x8:
            raise ConnectionError("ws close")
        if op == 0x9:
            self._send_raw(0xA, data)
            return
        if op == 0xA:
            return
        self.pl += data

    def read(self, n):
        while len(self.pl) < n:
            self._pump()
        out = bytes(self.pl[:n])
        del self.pl[:n]
        return out

    def _send_raw(self, opcode, data):
        hdr = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            hdr.append(n)
        elif n < 65536:
            hdr.append(126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(127)
            hdr += struct.pack(">Q", n)
        with self.send_lock:
            self.sock.sendall(bytes(hdr) + data)

    def send(self, data):
        self._send_raw(0x2, data)


def ws_handshake(conn, request):
    key = None
    for line in request.split(b"\r\n"):
        if line.lower().startswith(b"sec-websocket-key:"):
            key = line.split(b":", 1)[1].strip()
    if not key:
        return None
    accept = base64.b64encode(hashlib.sha1(key + WS_GUID).digest())
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\n"
                 b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                 b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
    return accept


# --------------------------------------------------------------------------
# RFB
# --------------------------------------------------------------------------
class RBFSession:
    def __init__(self, ws, src, ui):
        self.ws = ws
        self.src = src
        self.ui = ui
        self.bpp = 32
        self.w = src.w
        self.h = src.h
        self.scale_x = 1920.0 / src.w
        self.scale_y = 1080.0 / src.h
        self.last_seq = 0
        self.encodings = set()
        self.zl = None          # ZRLE 持久 zlib 压缩流（每连接一个）
        self.last_bytes = 0     # 上次更新字节数（自适应限速用）
        self.last_send_t = 0.0
        self.upd_req = threading.Event()      # 画面更新请求（读取线程置位）
        self.req_rect = (0, 0, 0, 0, False)   # (x,y,w,h,incremental)

    def _send(self, data):
        self.ws.send(data)

    def handshake(self):
        self._send(b"RFB 003.008\n")
        ver = self.ws.read(12)
        if not ver.startswith(b"RFB"):
            raise ConnectionError("bad version %r" % ver)
        self._send(bytes([1, 1]))          # 1 security type: None
        self.ws.read(1)
        self._send(struct.pack(">I", 0))   # SecurityResult OK
        self.ws.read(1)                    # ClientInit

        pf = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 0, 8, 16)   # B860 colorfix: 声明与 shm 实际 RGBX 一致
        name = b"B860AV1.1-T (drmcap)"
        self._send(struct.pack(">HH", self.w, self.h) + pf +
                   struct.pack(">I", len(name)) + name)

    def _pixel_payload(self, buf, bpp, x, y, w, h):
        out = bytearray()
        stride = self.w * bpp
        for row in range(y, y + h):
            start = row * stride + x * bpp
            out += buf[start:start + w * bpp]
        return bytes(out)

    def _send_update(self, x, y, w, h, use16):
        buf = self.src.mm
        if use16:
            base = self.src.off16
            bpp = 2
        else:
            base = self.src.off32
            bpp = 4
        stride = self.w * bpp
        payload = bytearray()
        for row in range(y, y + h):
            start = base + row * stride + x * bpp
            payload += buf[start:start + w * bpp]
        hdr = struct.pack(">BBH", 0, 0, 1) + struct.pack(">HHHHI", x, y, w, h, 0)
        self.last_bytes = len(payload)
        self._send(hdr + bytes(payload))

    def _send_update_zrle(self, x, y, w, h, drgb):
        if self.zl is None:
            self.zl = zlib.compressobj(1)
        stride3 = self.w * 3
        tiles = bytearray()
        ty = y
        while ty < y + h:
            th = min(64, y + h - ty)
            tx = x
            while tx < x + w:
                tw = min(64, x + w - tx)
                tiles += b"\x00"
                for r in range(ty, ty + th):
                    s = r * stride3 + tx * 3
                    tiles += drgb[s:s + tw * 3]
                tx += 64
            ty += 64
        comp = self.zl.compress(bytes(tiles)) + self.zl.flush(zlib.Z_SYNC_FLUSH)
        hdr = struct.pack(">BBH", 0, 0, 1) + struct.pack(">HHHHi", x, y, w, h, 16)  # noVNC 1.6.0: encodingZRLE=16 (not -7)
        self.last_bytes = len(comp)
        self._send(hdr + struct.pack(">I", len(comp)) + comp)

    def _send_empty(self):
        self.last_bytes = 0
        self._send(struct.pack(">BBH", 0, 0, 0))

    def run(self):
        # B860 低延迟改造：输入与画面分离为两个线程。
        # 旧版单线程里 _respond 最长阻塞 0.5s 等新帧，期间按键全部排队 —— 这是
        # "键盘操作卡顿延迟大" 的根因。现在按键在任何时刻都被立即注入 uinput。
        self.handshake()
        print("[vnc] handshake OK", flush=True)
        threading.Thread(target=self._client_reader, daemon=True).start()
        self._updater()

    def _client_reader(self):
        nmsg = 0
        while True:
            t = self.ws.read(1)[0]
            nmsg += 1
            if nmsg <= 12 or (t != 3 and t != 4 and t != 5):
                print(f"[vnc] client msg#{nmsg} type={t}", flush=True)
            if t == 0:      # SetPixelFormat
                self.ws.read(3)
                pf = self.ws.read(16)
                self.bpp = 16 if pf[0] == 16 else 32
            elif t == 2:    # SetEncodings
                self.ws.read(1)
                n = struct.unpack(">H", self.ws.read(2))[0]
                encs = self.ws.read(n * 4)
                self.encodings = set(struct.unpack_from(">i", encs, k)[0]
                                     for k in range(0, n * 4, 4))
            elif t == 3:    # FramebufferUpdateRequest → 只唤醒更新线程
                inc = self.ws.read(1)[0]
                x, y, w, h = struct.unpack(">HHHH", self.ws.read(8))
                self.req_rect = (x, y, w, h, inc)
                self.upd_req.set()
            elif t == 4:    # KeyEvent（立即注入，不等待画面）
                down = self.ws.read(1)[0]
                self.ws.read(2)
                sym = struct.unpack(">I", self.ws.read(4))[0]
                kc = keysym_to_keycode(sym)
                if kc:
                    self.ui.key(kc, down)
            elif t == 5:    # PointerEvent
                mask = self.ws.read(1)[0]
                px, py = struct.unpack(">HH", self.ws.read(4))
                if mask & 0x08:
                    self.ui.wheel(-1 if py < self.h // 2 else 1)
                else:
                    self.ui.move(px * self.scale_x, py * self.scale_y)
                    self.ui.button(mask & 0x07)
            elif t == 6:    # ClientCutText
                self.ws.read(3)
                ln = struct.unpack(">I", self.ws.read(4))[0]
                self.ws.read(ln)
            else:
                raise ConnectionError("unknown client message %d" % t)

    def _updater(self):
        while True:
            self.upd_req.wait()
            self.upd_req.clear()
            x, y, w, h, inc = self.req_rect
            self._respond(inc, x, y, w, h)

    def _respond(self, inc, x, y, w, h):
        # 自适应限速：仅异常突发时触发（ZRLE 后全帧 ~400KB 不会命中）
        now = time.time()
        if self.last_bytes > 2000000:
            gap = now - self.last_send_t
            if gap < 0.30:
                time.sleep(0.30 - gap)
        seq, (bx, by, bw, bh), snap32, drgb = self.src.read_stable()
        if seq == self.last_seq:
            self._send_empty()
            return
        self.last_seq = seq
        if not inc or bw == 0:
            x0, y0, x1, y1 = x, y, x + w, y + h
        else:
            x0, y0 = max(x, bx), max(y, by)
            x1 = min(x + w, bx + bw)
            y1 = min(y + h, by + bh)
            if x1 <= x0 or y1 <= y0:
                self._send_empty()
                return
        if self.bpp == 16:
            self._send_update(x0, y0, x1 - x0, y1 - y0, True)
        else:
            self._send_update_zrle(x0, y0, x1 - x0, y1 - y0, drgb)
        self.last_send_t = time.time()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
MIME = {
    ".html": "text/html", ".js": "application/javascript",
    ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml",
    ".json": "application/json", ".ico": "image/x-icon",
}


def serve_file(conn, path):
    if path == "/":
        conn.sendall(b"HTTP/1.1 302 Found\r\nLocation: /vnc_lite.html\r\n"
                     b"Content-Length: 0\r\n\r\n")
        return
    fp = os.path.normpath(os.path.join(WEB_ROOT, path.lstrip("/")))
    if not fp.startswith(WEB_ROOT) or not os.path.isfile(fp):
        body = b"not found"
        conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: %d\r\n\r\n"
                     % len(body) + body)
        return
    with open(fp, "rb") as f:
        data = f.read()
    ext = os.path.splitext(fp)[1].lower()
    ctype = MIME.get(ext, "application/octet-stream")
    conn.sendall((b"HTTP/1.1 200 OK\r\nContent-Type: " + ctype.encode() +
                  b"\r\nContent-Length: %d\r\nCache-Control: no-cache\r\n\r\n"
                  % len(data)) + data)


def handle(conn, addr, src, ui):
    try:
        conn.settimeout(30)
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = conn.recv(4096)
            if not chunk:
                return
            request += chunk
            if len(request) > 65536:
                return
        line = request.split(b"\r\n", 1)[0].decode("latin1")
        parts = line.split()
        path = parts[1] if len(parts) > 1 else "/"

        if "upgrade: websocket" in request.lower().decode("latin1"):
            head, _, rest = request.partition(b"\r\n\r\n")
            ws_handshake(conn, head + b"\r\n\r\n")
            # The client may pipeline its first WebSocket frame right behind the
            # HTTP request; those bytes are already in "rest" and must not be lost.
            ws = WS(conn, leftover=rest)
            RBFSession(ws, src, ui).run()
        else:
            serve_file(conn, path)
    except ConnectionError:
        pass          # normal client disconnect
    except Exception:
        print(f"[vnc] client {addr} error:", flush=True)
        traceback.print_exc()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    print("[vnc] starting", flush=True)
    src = FrameSource()
    ui = UInput(1920, 1080)
    print("[vnc] uinput devices ready", flush=True)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN)
    srv.listen(8)
    print(f"[vnc] listening on http://{LISTEN[0]}:{LISTEN[1]}/", flush=True)

    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle, args=(conn, addr, src, ui),
                         daemon=True).start()


if __name__ == "__main__":
    main()
