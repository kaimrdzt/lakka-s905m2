#!/bin/bash
set -e
DST=/root/uboot/verify_r80
rm -rf /tmp/relr80 && mkdir -p /tmp/relr80
cp $DST/u-boot.bin /tmp/relr80/bl33.bin
cd /tmp/relr80
/root/uboot/aml-fip/fip/gxl/aml_encrypt_gxl --bl3enc --input bl33.bin >/dev/null
CONT=$(stat -c%s bl33.bin.enc)
echo "container: $CONT B (margin $((524288-CONT)))"
[ "$CONT" -le 524288 ] || { echo "FATAL over limit"; exit 1; }
cp bl33.bin.enc /mnt/d/b860/uboot-2026.10/out/bl33-diag-r80.enc

python3 /mnt/d/b860/uboot-2026.10/repack.py \
    /mnt/d/b860/out/v71/B860AV11T_v71_arm64_20261104_GA.img \
    /mnt/d/b860/bootchain/v71/bootloader.bin \
    /mnt/d/b860/uboot-2026.10/out/bl33-diag-r80.enc \
    /mnt/d/b860/uboot-2026.10/out/boot-diag-r80.img
echo "---- audit ----"
B=$DST/u-boot.bin
for s in 'nandprobe' 'booti' 'ubi read' 'nand-ubi' 'wget' 'ping' 'ipaddr' '172.16.30.100'; do
  echo "  '$s' refs: $(strings $B | grep -c "$s")"
done
echo "  bootdelay refs: $(strings $B | grep -c 'bootdelay')"
echo "  diag-r79 leftovers: $(strings $B | grep -c 'diag-r79')"
diff <(grep -E '^CONFIG_|^# CONFIG_' $DST/.config | sort) \
     <(grep -E '^CONFIG_|^# CONFIG_' /root/uboot/verify_r79/.config | sort) | head -25
cmp $DST/u-boot.dtb /root/uboot/verify_r79/u-boot.dtb && echo "DTB identical to r79"
sha256sum /mnt/d/b860/uboot-2026.10/out/boot-diag-r80.img
