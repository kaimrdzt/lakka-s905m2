#!/bin/sh
# Start the capture helper and the VNC/WebSocket server together.
# drmcap publishes frames to /dev/shm; vncsrv serves them to noVNC and injects
# input through /dev/uinput.
#
# 2026-11-23: this unit is pulled in by multi-user.target, which is reached
# BEFORE graphical.target (RetroArch sets the CRTC mode there).  Putting
# After=graphical.target on the unit created an ordering cycle and systemd
# silently deleted the job at boot.  Instead we start early and wait here for
# the scanout framebuffer to appear.

CAP=/storage/.config/bin/drmcap
SRV=/storage/.config/vnc/vncsrv.py
RES=${VNC_RES:-960x540}
FPS=${VNC_FPS:-5}
SHM=/dev/shm/drmcap.bin

W=${RES%x*}
H=${RES#*x}
EXPECT=$((64 + 2 * (W * H * 4 + W * H * 2 + W * H * 3)))

pkill -f vncsrv.py 2>/dev/null
pkill drmcap 2>/dev/null
sleep 1
rm -f "$SHM"

echo "[vnc] waiting for framebuffer (expect shm=${EXPECT} bytes)"
i=0
while [ "$i" -lt 60 ]; do
    i=$((i + 1))
    "$CAP" -w "$W" -h "$H" -f "$FPS" >/tmp/drmcap.log 2>&1 &
    CPID=$!
    sleep 2
    if [ -f "$SHM" ]; then
        SZ=$(wc -c < "$SHM" 2>/dev/null)
        if [ "$SZ" = "$EXPECT" ]; then
            echo "[vnc] drmcap ready after ${i} tries (shm=${SZ})"
            break
        fi
    fi
    kill "$CPID" 2>/dev/null
    sleep 1
done

if [ ! -f "$SHM" ]; then
    echo "[vnc] ERROR: no framebuffer after 60 tries, see /tmp/drmcap.log" >&2
fi

exec python3 "$SRV"
