#!/usr/bin/env python3
"""split_tftp_segments.py — 把 UBI 镜像切成 U-Boot TFTP 分段（4 段）
用法: python3 split_tftp_segments.py <ubi镜像> <输出目录>
分段布局与 flash_ubi.ps1 的写地址一一对应:
  seg01 -> 0xB800000  seg02 -> 0x1AC00000  seg03 -> 0x2A000000  seg04 -> 0x39400000
"""
import sys, os, hashlib

SIZES = [0xF400000, 0xF400000, 0xF400000, 0xF200000]   # 244/244/244/242 MiB

def main(src, outdir):
    d = open(src, 'rb').read()
    if sum(SIZES) != len(d):
        print(f"FATAL: 镜像 {len(d)} B ≠ 分段总和 {sum(SIZES)} B —— 镜像版本与分段布局不匹配")
        sys.exit(1)
    off = 0
    for i, s in enumerate(SIZES, 1):
        seg = d[off:off+s]
        p = os.path.join(outdir, f"ubi79.seg0{i}")
        open(p, 'wb').write(seg)
        print(f"seg0{i}: {s:,} B  sha256={hashlib.sha256(seg).hexdigest()[:16]}")
        off += s
    print(f"全部切分完成 -> {outdir}")

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
