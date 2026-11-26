#!/bin/bash
# build_ubi_r79.sh — rootfs expanded to 1024MiB (512 PEB), rest unchanged
# Volumes: rootfs(static,1GiB) kernel(static) dtb(static) userdata(dynamic,6.47GiB) env x2
set -e
cd /mnt/d/b860/out/ubi2

LEB=2064384
PEB=2097152
AVAIL_PEB=4004
LAYOUT=2
BEB=$(( AVAIL_PEB * 20 / 1024 ))      # 78
MARGIN=20

SYS_LEBS=$(( (967008256 + LEB - 1) / LEB ))          # 469 actual
ROOTFS_LEBS=512                                      # 1024MiB fixed
IMG_LEBS=$(( (30760968 + LEB - 1) / LEB ))           # 15
DTB_LEBS=1
ENV_LEBS=2                                           # 2 x 1 PEB dynamic

UD_LEBS=$(( AVAIL_PEB - LAYOUT - BEB - MARGIN - ROOTFS_LEBS - IMG_LEBS - DTB_LEBS - ENV_LEBS ))
UD_SIZE=$(( UD_LEBS * LEB ))
echo "rootfs=$ROOTFS_LEBS PEB (1GiB)  kernel=$IMG_LEBS  dtb=$DTB_LEBS  userdata=$UD_LEBS PEB ($UD_SIZE bytes = $((UD_SIZE/1024/1024)) MiB)"
[ "$UD_LEBS" -gt 100 ] || { echo "FATAL: userdata too small"; exit 1; }

cat > ubinize.r79.cfg <<EOF
[rootfs]
mode=ubi
image=SYSTEM
vol_id=0
vol_type=static
vol_name=rootfs
vol_size=$((ROOTFS_LEBS * LEB))

[kernel]
mode=ubi
image=Image
vol_id=1
vol_type=static
vol_name=kernel

[dtb]
mode=ubi
image=dtb
vol_id=2
vol_type=static
vol_name=dtb

[userdata]
mode=ubi
vol_id=3
vol_type=dynamic
vol_name=userdata
vol_size=$UD_SIZE

[uboot_env]
mode=ubi
vol_id=4
vol_type=dynamic
vol_name=uboot_env
vol_size=8192

[uboot_env_r]
mode=ubi
vol_id=5
vol_type=dynamic
vol_name=uboot_env_r
vol_size=8192
EOF

rm -f b860av11t_ubi_r79.ubi
ubinize -o b860av11t_ubi_r79.ubi -m 8192 -s 8192 -O 24576 -p $PEB ubinize.r79.cfg
ls -l b860av11t_ubi_r79.ubi
sha256sum b860av11t_ubi_r79.ubi | tee b860av11t_ubi_r79.ubi.sha256

echo "== header self-check =="
python3 - <<'PY'
import struct
d=open('b860av11t_ubi_r79.ubi','rb').read()
PEB=2097152
n=len(d)//PEB
print(f"  {len(d):,} B = {n} PEB")
ok=True
for i in range(3):
    b=i*PEB
    ec=struct.unpack_from('>I',d,b)[0]
    vid=struct.unpack_from('>I',d,b+24576)[0]
    s='OK' if ec==0x55424923 and vid==0x55424921 else 'BAD'
    if s=='BAD': ok=False
    print(f"  PEB{i}: EC=0x{ec:08X} VID=0x{vid:08X} {s}")
print("HEADERS", "OK" if ok else "FAIL")
PY
