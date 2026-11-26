#!/bin/bash
# build r80: derive from verify_r79, switch env backend UBI -> NOWHERE
# Per BL33_NOWHERE doc 3.1/3.2: derive from the ACTUAL build dir, never source defconfig.
set -e
SRC=/root/uboot/verify_r79
DST=/root/uboot/verify_r80

if [ -d "$DST" ]; then
  echo "verify_r80 exists, reusing"
else
  cp -a "$SRC" "$DST"
  echo "copied verify_r79 -> verify_r80"
fi
cd "$DST"

# env backend: UBI -> NOWHERE (Kconfig will clean implicit deps via olddefconfig)
sed -i 's/^CONFIG_ENV_IS_IN_UBI=y/# CONFIG_ENV_IS_IN_UBI is not set/' .config
sed -i 's/^# CONFIG_ENV_IS_NOWHERE is not set/CONFIG_ENV_IS_NOWHERE=y/' .config
for k in CONFIG_ENV_UBI_PART CONFIG_ENV_UBI_VOLUME CONFIG_ENV_UBI_VOLUME_REDUND \
         CONFIG_ENV_UBI_VOLUME_CREATE CONFIG_ENV_UBI_VID_OFFSET CONFIG_ENV_REDUNDANT; do
  sed -i "s/^$k=/# $k=/" .config
done

# banner
sed -i 's/diag-r79/diag-r80/g' .config

echo "---- env config sanity ----"
grep -E 'CONFIG_ENV_IS_NOWHERE|CONFIG_ENV_IS_IN_UBI|CONFIG_ENV_UBI|diag-r80' .config | head -10

make -C /root/uboot/u-boot O=$DST olddefconfig >/dev/null
make -C /root/uboot/u-boot O=$DST -j8 CROSS_COMPILE=aarch64-linux-gnu- 2>&1 | grep -E "error|Error" | head -5 || true

echo "---- result ----"
ls -la $DST/u-boot.bin
grep -aq 'diag-r80' $DST/u-boot.bin && echo "BANNER OK"
echo "-- splash/env refs in binary --"
strings $DST/u-boot.bin | grep -c 'Loading Environment from UBI' || echo "0 (env-UBI read gone)"
strings $DST/u-boot.bin | grep -c 'Saving Environment to UBI' || echo "0 (env-UBI save gone)"
grep -E '^CONFIG_ENV_IS_NOWHERE|^CONFIG_ENV_IS_IN_UBI' $DST/.config
