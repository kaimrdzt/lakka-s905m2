<p align="center">Language / 语言: <a href="README.md">中文</a> | Current: English (click 中文 to switch)</p>


# B860AV1.1-T Custom Lakka System — Complete Porting Project

> **Hardware**: Amlogic S905M2 (GXL family, Rev 21:d) / 1GiB DDR3 @792MHz / 8GiB Micron MLC NAND
> **Device**: ZTE B860AV1.1-T set-top box
> Generated: 2026-11-26 (first version 11-25; theme / color fix / 720p updated through 11-26).
> This project is a **fully reproducible delivery** of the firmware currently running on this box: flashing scripts, build scripts, configuration, and every pitfall encountered.

**Large binaries are not tracked in Git** (UBI image 974MB, bootloader 110MB, kernel, sanitized `/storage` backup); download them from
[Releases](../../releases/latest). This repository tracks only scripts, configuration, and documentation, so the clone stays under 200KB.

> This repository publishes a **verified reproducible port**, not a general-purpose distribution. Flashing can brick the device; read the entire
> "Pitfall Guide" (§5) before proceeding.

---

## 1. System Overview

### 1.1 Hardware Specifications

| Item | Specification | Notes |
|---|---|---|
| SoC | Amlogic S905M2 (GXL family, Revision 21:d, `amlogic,meson-gxl`) | 4× Cortex-A53 (r0p4); BL2 fixed at 1200MHz, runtime uses SCPI DVFS |
| GPU | ARM Mali-450 MP3 (`arm,mali-450`) | gp + pp0/pp1/pp2, lima driver, no regulator (fixed voltage) |
| Memory | 1GiB DDR3 @ 792MHz, single rank, 2T-CL11 | `mem=1008M`, top 16MiB reserved for ramoops; about 784MiB usable + CMA 128MiB |
| Storage | 8GiB Micron MT29F64G08CBABA (U-Boot reports `MT29F64G08CBABBWP`) **MLC NAND** | 1 LUN × 4096 blocks, page 2KiB, BCH40/1K, 8-bit @250MHz, scrambling mandatory, no SLC emulation |
| Network | Synopsys DWMAC1000 (`snps,dwmac-3.70a`), 10/100M RMII | Internal PHY address 8 (OUI `0181.4400`), irq 40 |
| WiFi | SDIO (mmc2, high-speed), RTL8189FS | DTB compatible with `brcm,bcm4329-fmac`; requires the dedicated `8189fs` driver (§3.3) |
| Bluetooth | DTB declares BCM43438 (shutdown GPIO 96), **node disabled** | Chip model not individually verified; no antenna / not populated |
| USB | dwc3-meson-g12a (2× USB2, 0× USB3) + xhci + dwc2 OTG | OTG defaults to peripheral and is only used for flashing; the v80 patch can switch it to dual Host (§8.1) |
| Storage expansion | TF card slot (`d0072000.mmc`, with CD hot-plug GPIO) | Can serve as second storage / boot disk |
| Display | HDMI 1.4 (`amlogic,meson-gxl-dw-hdmi`) | 720p60 / 1080p60 verified working |
| Audio | AIU (I2S) + HDMI audio | codec-0 of dai-link-1 must point to `AIU_HDMI CTRL_I2S`; pointing to `hdmi_tx` conflicts with dai-link-2 (-EBUSY) |
| Video decoding | GXL VDEC (`amlogic,gx-vdec`) | H.264 / H.265 hardware decoding |
| Debug serial | `ttyAML0`, 115200 8N1 | Onboard solder pads, USB-CH340 adapter |

> Specifications were cross-checked against DTB decompilation and boot logs; unpopulated peripherals (Bluetooth) are marked as such, with no guessed values.

### 1.2 Boot Chain

```
BL1(ROM) → BL2(原厂 2018) → BL30/BL31(原厂) → BL33 = U-Boot 2026.10 diag-r80（自编）
  → ubi part → 读 kernel/dtb 卷 → booti → Linux 6.18.0（Lakka rootfs）
```

### 1.3 NAND Physical Layout (8GiB = 4096 PEB × 2MiB)

| Region | Range | Description |
|---|---|---|
| nand-boot (stock boot region) | 0x0 – 0x800000 | BL2/BL33, etc.; updated when flashing with UBT |
| **Skipped region** | 0x800000 – 0xB800000 | Stock nand_key (PEB10-13) + true bad blocks 90/91, **never used** |
| **nand-ubi (UBI data region)** | **0xB800000 (184MiB) – 8GiB** | First 92 PEBs unused; UBI created on the remaining 4004 PEBs; 0 bad blocks measured inside the UBI region |

> The stock MFTL once marked 59 bad blocks across the chip, but that BBT is untrustworthy (some false, some genuine), so the UBI layout is planned
> directly as "skip the first 92 PEBs + treat the rest as good" and does not rely on BBT scan results.

### 1.4 UBI Volume Table (r79 image, 6 volumes after attach)

| vol | Name | Type | Size | Contents |
|---|---|---|---|---|
| 0 | rootfs | static | **1GiB** (data 922MiB, upgrade margin reserved) | Lakka SYSTEM squashfs (mounted read-only) |
| 1 | kernel | static | 29.3MiB | Linux 6.18.0 arm64 Image |
| 2 | dtb | static | 30KB | Patched DTB (**Bluetooth node disabled**) |
| 3 | userdata | dynamic | ~6.49GiB (3374 PEB) | UBIFS → /storage (all writable content) |
| 4/5 | uboot_env / uboot_env_r | dynamic | 8KB×2 | Empty (env changed to NOWHERE and is not used) |

### 1.5 U-Boot r80 Features (relative to r78)

- **PREBOOT removed**: no more `splash/bmp/already selected` three-line noise at boot
- **env = NOWHERE**: environment variables compiled into the binary; `Loading Environment from nowhere... OK` completes instantly, no bad CRC, **never writes NAND env** (writing env on this board's MLC has historically hung)
- bootargs / bootcmd / IP are all fixed at compile time; the `saveenv` command is not compiled
- mtdparts: `nand0:0x800000@0(nand-boot),-@0xb800000(nand-ubi)`
- Network (wget/ping) and NAND rescue commands retained

### 1.6 /storage Writable Layer (where all configuration lives and survives system upgrades)

| Path | Contents |
|---|---|
| `kernel-overlays/8189fs/lib/modules/6.18.0/8189fs.ko` | WiFi driver (**no dual-interface version**, see pitfall #1) |
| `.cache/kernel-overlays/8189fs.conf` | Registers the overlay (picked up by depmod/modprobe at boot) |
| `.config/modules-load.d/8189fs.conf` | Auto-load at boot |
| `.config/modprobe.d/8189fs-ps-off.conf` | `rtw_power_mgnt=1 rtw_ips_mode=0` (disable power saving) |
| `.config/udev.rules.d/90-wifi-powersave-off.rules` | power_save off at the mac80211 layer |
| `.config/system.d/` | connman enable drop-in, iwd STATE_DIRECTORY=/storage/iwd, time-restore/save/timer units |
| `.config/connman_main.conf` | connman main configuration (eth0 blacklist) |
| `.config/timesyncd.conf.d/ntp.conf` | NTP=ntp.aliyun.com ntp.tencent.com + Google fallback |
| `.cache/timezone` | `TIMEZONE=Asia/Shanghai` (EnvironmentFile format) |
| `.cache/last-epoch` | Timestamp restored after power loss (refreshed every minute) |
| `iwd/<你的SSID>.psk` | WiFi credentials (iwd persists to /storage/iwd) |
| `assets/xmb/monochrome/png/bg.png` + `assets/branding/logo.png` | Custom boot splash |

---

## 2. Flashing Instructions (for a new box / brick recovery)

### 2.1 Prerequisites

- Windows PC: Ethernet static IP `192.0.2.10/24` (example subnet; adjust to your network); COM14 serial cable (USB-CH340, 115200)
- Tools: Amlogic USB Burning Tool v2.x; administrator privileges

### 2.2 Step 1: Flash the bootloader (UBT)

UBT is third-party software and is not redistributed by this repository. Obtain
`Amlogic_USB_Burning_Tool_v2.2.4.zip` (including drivers) from Amlogic's official site or a trusted source, then verify it against
[02-flash/SHA256SUMS.txt](02-flash/SHA256SUMS.txt).

1. Power off the box → short the contacts to enter ROM mode → connect USB to the PC
2. Extract and run UBT, load `01-images\boot-diag-r80.img`, **check Erase flash** → flash
   (the whole NAND is then empty plus the image contents; the UBI region has no data — added in step 2)
3. Power-cycle; the serial console should show the `diag-r80` banner and stop at the `Volume kernel not found!` diagnostic shell (**normal**, UBI has not been written yet)

### 2.3 Step 2: Write the UBI system (02-flash\刷入UBI.bat)

1. Connect the serial terminal to COM14 and then **close it** (the bat file needs exclusive access to COM14)
2. Right-click `刷入UBI.bat` → Run as administrator
3. The script automatically: detects the prompt → erases 0xB800000..8GiB → transfers 4 TFTP segments (about 36 seconds each) → writes to flash (about 1-2 minutes each, with a heartbeat display) → verifies with `ubi info`
4. When `全部完成` appears → type `reset` in the serial console
5. First boot: userdata is formatted automatically → Lakka starts (WiFi/timezone, etc. are still in a bare state — normal)

### 2.4 Step 3: Restore the /storage configuration backup (03-restore)

```sh
# PC 上（storage-backup-public.tgz 从 Releases 下载，已脱敏）：
scp storage-backup-public.tgz root@192.0.2.100:/tmp/
# 盒子上：
cd / && tar xzf /tmp/storage-backup-public.tgz
sync && reboot
```

> The public backup has `storage/iwd/` and `storage/.cache/connman/` removed and **contains no WiFi credentials**.
> After restoring, reconnect WiFi yourself: `iwctl station wlan0 connect <你的SSID>`.

### 2.5 Final Verification Checklist

```sh
lsmod | grep 8189fs                        # 驱动已加载
iwctl station wlan0 show                   # State=connected <你的SSID> + IPv4
ip -4 addr show wlan0                      # 192.0.2.150/24
iw dev wlan0 get power_save                # off
ping -c2 223.5.5.5                         # 公网通
date                                       # CST 北京时间
readlink /var/run/localtime                # → Asia/Shanghai
systemctl is-active time-save.timer        # active
md5sum /tmp/assets/xmb/monochrome/png/bg.png   # = 你的开机图
serial 横幅                                # diag-r80，且无 splash/bmp/badCRC 行
```

---

## 3. Routine Maintenance

### 3.1 Update the System (replace the SYSTEM squashfs)

```sh
# 新 SYSTEM < 1GiB（rootfs 卷上限），放 /storage 后：
ubiupdatevol /dev/ubi0_0 /storage/SYSTEM-new.squashfs
sync && reboot
```
- All /storage configuration is preserved automatically; if the new image has a different kernel version, see 3.3

### 3.2 Update the Kernel / DTB

```sh
ubiupdatevol /dev/ubi0_1 /storage/Image-new    # 内核卷
ubiupdatevol /dev/ubi0_2 /storage/dtb-new      # dtb 卷
sync && reboot
```
- U-Boot bootcmd reads by volume name; as long as the names stay the same, the bootloader needs no changes

### 3.3 Add / Update a Driver (8189fs pattern, no system reflash required)

1. Build in WSL (kernel tree / toolchain in §4.4)
2. Deploy on the box:
   ```sh
   mkdir -p /storage/kernel-overlays/<名>/lib/modules/$(uname -r)
   cp xxx.ko /storage/kernel-overlays/<名>/lib/modules/$(uname -r)/
   echo /storage/kernel-overlays/<名> > /storage/.cache/kernel-overlays/<名>.conf
   echo <模块名> > /storage/.config/modules-load.d/<模块名>.conf   # 开机自动加载（可选）
   echo "options <模块名> 参数=值" > /storage/.config/modprobe.d/xxx.conf  # 参数（可选）
   ```
3. Reboot to apply (`kernel-overlays-setup` runs depmod automatically + `systemd-modules-load` runs modprobe automatically)
4. ⚠️ After every kernel change, overlay drivers must be rebuilt against the new kernel (vermagic must match)

### 3.4 Back Up /storage (strongly recommended on a regular basis)

```sh
cd / && tar czf /tmp/storage-backup.tgz storage/.config storage/.cache storage/assets storage/iwd storage/kernel-overlays storage/lib
# 然后 scp 回 PC
```

---

## 4. Build Reproduction

### 4.1 Environment

- WSL Ubuntu 22.04 (root)
- U-Boot source: `/root/uboot/u-boot` (includes the meson NAND fix chain r76/r77)
- Kernel source: `/mnt/e/Lakka/build.Lakka-AMLGX.aarch64/build/linux-7d0a66e4bb9081d75c82ec4957c50034cb0ea449` (6.18.0)
- Cross toolchain: `/mnt/e/Lakka/build.Lakka-AMLGX.aarch64/toolchain/bin/aarch64-libreelec-linux-gnu-`
- aarch64-linux-gnu- (for U-Boot, installed via apt)
- Encryption tool: `/root/uboot/aml-fip/fip/gxl/aml_encrypt_gxl`
- Packager: `D:\b860\uboot-2026.10\repack.py` (guards: BL33≤0x80000, BL2 byte-for-byte unchanged, differences limited to the bootloader region)

### 4.2 Build U-Boot r80 (04-build-scripts\make_r80.sh)

Key rules (breaking them will cause trouble):
1. **Always derive from the last build directory verified on real hardware** (`cp -a verify_r79 verify_r80`); **never** start from the source defconfig — defconfig has drifted from the actual hardware configuration (DTB load address, etc.)
2. Change only the target items + banner; leave everything else untouched
3. After editing .config, always run `make olddefconfig`

r80 changes = r79 + env backend switch:
```
CONFIG_ENV_IS_IN_UBI=y        → # ... is not set
# CONFIG_ENV_IS_NOWHERE ...   → CONFIG_ENV_IS_NOWHERE=y
横幅 diag-r79 → diag-r80
```
Kconfig automatically sets `ENV_IS_DEFAULT=y` (derivation rule at env/Kconfig:94) and **does not compile saveenv** (guard at cmd/nvedit.c:462) — this is expected behavior.

Build:
```bash
make -C /root/uboot/u-boot O=/root/uboot/verify_r80 olddefconfig
make -C /root/uboot/u-boot O=/root/uboot/verify_r80 -j8 CROSS_COMPILE=aarch64-linux-gnu-
```

### 4.3 Pack the Bootloader (04-build-scripts\pack_r80.sh)

```bash
aml_encrypt_gxl --bl3enc --input u-boot.bin      # 容器必须 ≤ 524288 B
repack.py <v71 GA 原厂镜像> <bootloader.bin> <bl33.enc> <out.img>
# 护栏自检必须全部通过：bl2 unchanged=True / outside=0 / selfconsistent=True
```

### 4.4 Build the UBI Image (04-build-scripts\build_ubi_r79.sh)

- Input: `out/ubi2/{SYSTEM,Image,dtb}` (SYSTEM=922MiB squashfs, Image=29.3MiB, dtb=patched version)
- Geometry: PEB 2MiB / LEB 2064384 / min-io 8192 / VID offset 24576
- Volume-size budget: rootfs 512PEB(1GiB) + kernel 15 + dtb 1 + userdata dynamic + env 2 + layout 2 + BEB 78 + spare 20 ≤ 4004
- `ubinize -m 8192 -s 8192 -O 24576 -p 2097152`
- Output header self-check: EC=0x55424923 / VID=0x55424921 for the first 3 PEBs (the script only samples the first 3, not a full-disk scan)

### 4.5 TFTP Segmentation (04-build-scripts\split_tftp_segments.py)

Four segments of 244/244/244/242 MiB, matching the write addresses in flash_ubi.ps1 one-to-one (starting at 0xB800000). Re-split whenever the image version changes.

### 4.6 Build the 8189fs Driver (including disabling the dual interface)

Source: `/root/rtl8189fs` (the rtl8189fs branch of jwrdegoede/rtl8189ES_linux)

**Key modification (remove the wlan1 virtual interface)**: comment out concurrent mode on line 26 of the Makefile
```makefile
#ccflags-y += -DCONFIG_CONCURRENT_MODE
```
(Warning: do not use the `rtw_virtual_iface_num=0` approach — with 0 this driver triggers a kernel Oops; `rtw_port_switch_chk` was observed to crash.)

```bash
cd /root/rtl8189fs
make clean
make ARCH=arm64 CROSS_COMPILE=<工具链>/aarch64-libreelec-linux-gnu- KSRC=<内核树> -j4
# 产物 8189fs.ko，modinfo vermagic 必须 = 目标内核 `uname -r`
```

### 4.7 Deployment Audit (strongly recommended before every flash)

- U-Boot: strings check for banner / leftover strings; config diff shows only expected changes; DTB cmp identical
- UBI: parse the VID volume table + compare sha256 of the three static volumes against source files (this session used it to catch a regression where the dtb had not been written back)

---

## 5. Pitfall Guide (all encountered on real hardware, ordered by severity)

### 🔴 Brick / hang level

1. **Never call saveenv on env**: `saveenv`/`env erase` on MLC with this driver once hung the system (NAND wait timeout). That is why r80 switched to NOWHERE — env is compiled into the binary and the write path is removed entirely.
2. **UBT with Erase flash checked = full-chip erase**: flash bootloader first, then write UBI (order in §2.2/2.3); reversed order or missing UBI → `Volume kernel not found`.
3. **Do not use tools that trust the BBT to reflash the bootloader**: OOB bad-block markers on this board are untrustworthy (boot-log Bad eraseblock 0-7 are false, 90/91 are genuine), the BBT is unreliable. Use only UBT / verified U-Boot command flows.
4. **No SLC emulation on MLC** (allowed by the kernel patch): power loss can corrupt userdata. Back up /storage regularly; avoid frequent hard power cuts.

### 🟠 Script / workflow level

5. **U-Boot `nand erase/write` does not print "OK" when it finishes**, it only returns the prompt — completion must be judged by the prompt returning. Waiting for "OK" = false hang (v4 lesson).
6. **While a long command (`nand write`) is running, queued input is consumed by `ctrlc()` polling** (the `nand_util.c` loop checks ctrlc). The flashing script must be strictly serial: send the next command only after the previous command's prompt has returned and been confirmed (v5→v6 lesson).
7. **The trailing "U-Boot> " prompt from `tftpboot` remains in the receive buffer**; if it is not consumed, the next segment's "wait for prompt" instantly false-matches → "write complete in 0 seconds" (v5 lesson). Clear/consume the buffer after every step.
8. **After modifying a driver, write it back to the source image directory before packing**: copying only the patched file to the box and not back to the packing source directory → silently reverts on the next pack (this package's dtb nearly regressed the Bluetooth issue; the audit script caught it).
9. **COM14 exclusive access**: close all serial terminals before running the flashing bat, otherwise `Access to the port 'COM14' is denied`.
10. **Commands sent to the box must include Enter**: bare characters (e.g. SysRq `b`) typed at the prompt are not submitted, so a new prompt never appears (v2/v3 lesson).
11. **TFTP segments and image must match versions**: re-split whenever the image changes (`split_tftp_segments.py` has a total-size assertion).
12. **The USB Burning Tool flash image is based on the v71 stock GA image**: after repack everything outside the bootloader region is stock content — UBI must be written after flashing; there is no such thing as "flash the bootloader and it boots".

### 🟡 Config / encoding level

13. **PowerShell 5.1 needs a BOM to read UTF-8 Chinese in .ps1**; editing with tools strips the BOM, so re-add it. bat uses `chcp 65001` + UTF-8 BOM.
14. **`%s` in a systemd unit is a specifier** (expanded into a shell path) — `date +%s` must be written as `date +%%s` (this once made the timestamp file contain "/bin/bash").
15. **EnvironmentFile requires KEY=value format**: tz-data.service reads `/storage/.cache/timezone`; bare `Asia/Shanghai` is invalid, it must be `TIMEZONE=Asia/Shanghai`.
16. **/var is tmpfs**: all state that must survive a reboot (iwd PSK, timesync timestamps) must be redirected to /storage — iwd uses `Environment=STATE_DIRECTORY=/storage/iwd` (drop-in), connman uses the official override point `/storage/.config/connman_main.conf`.
17. **connman and iwd division of labor**: connman in this image has no wifi backend (`connmanctl services` reports Not supported); iwd must handle authentication + connman must handle DHCP; the presence of `/dev/.kernel_ipconfig` (kernel ip= static IP) makes connman.service be skipped by a Condition → clear the Condition with a drop-in and enable it.
18. **Dual NICs are not a Lakka problem**: compiling the RTL8189FS driver with `CONFIG_CONCURRENT_MODE` creates wlan1; connman can connect wlan1 and steal the default route, causing **no public-network access** (wlan0 is fine) — rebuild with concurrent mode disabled in the Makefile.
19. **Both power-saving layers must be turned off**: driver layer `rtw_power_mgnt=1` (lps_level is only a level, not a switch) + mac80211 layer `iw dev wlan0 set power_save off` (iwd automatically re-enables it on connect → suppress with a udev rule).
20. **The systemd EnvironmentFile / specifier / drop-in Condition trio is easy to get wrong**: clear ConditionPathExists with an empty value; escape `%%`; place drop-ins in `/storage/.config/system.d/<unit>.d/override.conf` (the systemd UnitPath includes this directory).
21. **Change RetroArch's configuration only while RetroArch is stopped when restoring a backup**: RA writes its in-memory configuration back to retroarch.cfg on exit — if a tar is restored while RA is running, after reboot the language/settings are overwritten back to English by in-memory defaults (observed user_language 12→0). Fix script `03-restore\fix_cn_language.sh`: stop RA → sed to `user_language="12"` → add Ozone fonts (regular/bold too, 3 places total) → start → verify after startup that it was not rolled back.
22. **Chinese fonts have 3 locations**: `/storage/assets/xmb/monochrome/font.ttf` + `/storage/assets/ozone/regular.ttf` + `/storage/assets/ozone/bold.ttf` (all DroidSansFallbackFull 4,033,420 B, md5 ebc9b34d…). Missing the two Ozone files leaves boxes appearing in some UI screens.
23. **8189fs driver logs at the default INFO level flood the serial console**: ARP responses, EDCA scheduling, etc. all print `RTW:` lines (kernel cmdline `loglevel=7` allows everything through); more traffic means more flooding, and the interactive shell gets "drowned" (Enter actually took effect, it is just invisible). Fix: `rtw_drv_log_level=2` (runtime `echo 2 > /sys/module/8189fs/parameters/rtw_drv_log_level` takes effect immediately; persist it in modprobe.d, already included in `8189fs-ps-off.conf`).

---

## 6. Repository Structure and Downloadable Assets

### 6.1 Files in the Repository (Git-tracked)

```
lakka-s905m2\
├── README.md                          Chinese README
├── README_EN.md                       English README (this file)
├── LICENSE                            GPL-2.0
├── 01-images\
│   └── SHA256SUMS.txt                 镜像校验值
├── 02-flash\
│   ├── SHA256SUMS.txt                 第三方工具校验值
│   ├── 刷入UBI.bat                    一键灌入（TFTP+串口自动写 NAND）
│   ├── flash_ubi.ps1                  串口驱动脚本（严格串行+进度）
│   └── tftp_server.py                 最小 TFTP 服务（端口 69）
├── 03-restore\
│   ├── fix_cn_language.sh             中文语言修复脚本（见避坑 #21）
│   └── vnc-tools\
│       ├── drmcap.c                   DRM scanout 抓帧（静态 C）
│       ├── vncsrv.py                  RFB over WebSocket 服务
│       ├── vnc_run.sh                 启动脚本
│       └── vncview.service            systemd 单元
├── 04-build-scripts\
│   ├── make_r80.sh                    U-Boot r80 编译
│   ├── pack_r80.sh                    BL33 容器 + repack + 审计
│   ├── build_ubi_r79.sh               UBI 镜像生成 + 头部自检
│   ├── split_tftp_segments.py         TFTP 分段切分
│   └── patch_kernel_usb.py            GXL OTG PHY 双主机模式内核补丁（§8.1）
└── 05-themes\
    └── install_themes.sh              XMB 图标主题安装（主题资源自行获取，见 §10）
```

### 6.2 Releases Assets

| File | Size | Purpose | sha256 (first 8 chars) |
|---|---|---|---|
| `b860av11t_ubi_r79.ubi` | 974MB | UBI 系统镜像（串口/TFTP 灌） | `6aa05193` |
| `boot-diag-r80.img` | 110MB | U-Boot r80 bootloader（UBT 刷） | `6a597992` |
| `kernel-usbhost.new` | 29.3MB | 双主机模式内核（ubiupdatevol 更新） | `af06f225` |
| `dtb-usbhost.new` | 30KB | 配套 DTB | `418a9b34` |
| `storage-backup-public.tgz` | 70MB | `/storage` 配置备份（已脱敏） | `c75f758f` |
| `SHA256SUMS.txt` | — | 全量校验值 | — |

Full checksums are in `SHA256SUMS.txt` on the Releases page.

## 7. Known Non-Issues

| Item | Classification |
|---|---|
| systemd unit 0755 warning string | Native upstream Lakka behavior, no functional impact |
| `lima no regulator (mali)` | GPU runs at fixed voltage |
| HDMI `Unknown ELD` / user-mode hints | Display-end negotiation; ignore if image quality is normal |
| Bad blocks 0-7 (false) / 90-91 (true) | Architectural reality; starting at PEB92 already avoids them |
| No SLC emulation on MLC | Allowed by the kernel patch, backed up as a safety net |
| U-Boot `NAND: 0 MiB` | Display-layer issue |
| NTP primarily Aliyun, Google fallback | Determined by the network environment (domestic NTP IPs are restricted on this network) |

## 8. USB Dual-Port Notes (verified conclusions, after the v80 kernel patch)

| Port | Property | Description |
|---|---|---|
| **Host port** | USB Host (xhci) | USB drives / gamepad dongles are **plug-and-play**; Lakka auto-mounts to `/storage/roms/<名>-usb-*` (vfat/NTFS both fine); copy ROMs in and they appear in the RA list |
| **Flashing port** (OTG port) | USB Device (dwc2 UDC, stock design) | **Only for USB Burning Tool flashing** (ROM mode is handled by BootROM's own USB stack, unrelated to Linux). Plugging in a USB drive at runtime is **never recognized — this is stock hardware design**, not a fault |

DT evidence: glue node usb@d0078080 has dr_mode="peripheral" + dwc2 child node dr_mode="peripheral", exactly matching the upstream Khadas VIM1 configuration. Attempts to change it to host on GXL do not work (PHY initialization quirk: it must boot in device mode, host mode relies on OTG role-switch, and that mechanism is not registered when glue is not in otg mode — both ports were observed dead and the change was rolled back).

USB drive test: usb 1-1 "Disk 2.0" (346d:5678) → usb-storage → sda (8.05GB) → sda1 (vfat) → auto-mounted at /storage/roms/sda1-usb-VendorCo_Product. ROMs are copied in via SMB (\\192.0.2.100\storage\roms\) or over the network.

### 8.1 Dual-Host-Mode Kernel Patch (v80, installed)

The mainline GXL glue driver has a quirk: it unconditionally forces device mode during OTG PHY initialization ("for DWC2 init"), so under Linux the flashing port can only act as a device. The patch (dwc3-meson-g12a.c, 3 changes):

1. Add `bool dwc2_en` to the struct
2. In probe, read the dwc2 child node status into `priv->dwc2_en`
3. Make the forced device mode in `dwc3_meson_gxl_usb2_init_phy` conditional: `(i == USB2_OTG_PHY && priv->dwc2_en)`

Combined with DT: glue `dr_mode="host"` + dwc2 `status="disabled"` → the OTG PHY starts in host mode and is taken over by xhci.

**Verified**: two USB drives enumerated simultaneously (1-1 + 1-2), `sda`/`sdb` block devices, **both auto-mounted** to `/storage/roms/`. The patch preserves original behavior on other boxes where dwc2 is enabled (the OTG port remains a device).

Rebuild the kernel with the `04-build-scripts` flow; the patch script is `patch_kernel_usb.py` (three precise replacements in the kernel tree's dwc3-meson-g12a.c). Update the kernel/DTB volumes in place with `ubiupdatevol`; there is no need to reflash UBI.

## 9. Remote Viewing of the Box Screen in a Browser (noVNC, built in)

Open this in a PC browser on the same subnet as the box:

    http://192.0.2.100:6080/vnc_lite.html

Click **Connect** to see the live HDMI picture; mouse and keyboard can operate the box directly (RA menu navigation).

- Pipeline: drmcap (static C, direct DRM scanout read at 5fps/960×540) → /dev/shm → vncsrv.py (RFB over WebSocket, standard library only) → noVNC front end (browser)
- Service: `vncview.service` (already enabled, starts at boot, located in /storage/.config/system.d)
- Files: `/storage/.config/bin/drmcap`, `/storage/.config/vnc/{vncsrv.py,vnc_run.sh}`, `/storage/novnc/` (noVNC 1.6.0 front end)
- Logs: `/tmp/drmcap.log`, `/tmp/vnc.log`; an unchanged picture is normal (dirty-region detection, static frames produce very little output)
- After restoring the backup (03-restore) there is no need to reinstall — all files in this section are included in the backup

## 10. XMB Icon Theme Pack (05-themes\, 8 official sets)

Source: [libretro/retroarch-assets](https://github.com/libretro/retroarch-assets) (official asset repository). RA source limitation: XMB icon theme directory names are a built-in enumeration, so only the following can be selected — **community third-party themes (NeoActive/Retrorama, etc.) cannot be selected directly**; they can only be placed in `assets/xmb/custom/` and used through the "Custom" slot (one at a time).

| Theme | Size | Style |
|---|---|---|
| systematic | 25M | Modern flat (highest-rated by the community)|
| flatux | 12M | Colorful Flat UI |
| automatic | 9.5M | Follows system light/dark theme |
| dot-art | 8.1M | Dot-matrix pixel style |
| retrosystem | 4.9M | Retro console style |
| daite | 4.4M | Clean lines |
| flatui | 1.8M | Classic Flat UI |
| pixel | 1.8M | Minimalist pixel style |

**Install**: on the box, `05-themes\install_themes.sh` (copy to /storage/assets/xmb/, persistent through copy-up).
**Switch**: RetroArch menu → Settings → User Interface → Appearance → Menu Icon Theme → pick one (takes effect immediately).

## 11. VNC Color Fix Record (three-layer swap issue)

The complete causal chain of wrong colors in the browser (brown / red-blue swapped):
1. Old drmcap output BGRX order 32bpp + vncsrv declared R@16 + noVNC **forced its own SetPixelFormat (R@0) on connect, overriding the server declaration** → red and blue swapped;
2. The `-c` parameter in the original `vnc_run.sh` compensated for "old binary + old declaration" (one more swap to make it right);
3. After switching to the new binary (RGBX output), `-c` remained → one extra swap → brown.
Final fix: drmcap outputs true RGBX (byte0=R) + vncsrv declares R@0 + **remove `-c`** → both ends agree. Fixed files are in `03-restore\vnc-tools\`.

## 12. Reusable Experience and Derivative Uses

This project is not a one-off adaptation: most modules can be taken apart and reused on other Amlogic boxes, other ARM64 Linux distributions,
and NAS-style systems. This section spells out what can be borrowed, how to borrow it, and what must not be copied verbatim.

### 12.1 Reuse Value by Module

| Module | Approach in this project | Reusable for |
|---|---|---|
| **Boot chain modification** | Keep stock BL2/BL30/BL31, replace only BL33 (U-Boot), repack FIP + BL33 ≤ 512KB guard | Any Amlogic box that wants to keep stock boot but change U-Boot (GXL / G12A / G12B) |
| **NAND → UBI** | Skip the stock key region and true bad blocks, create UBI starting at `0xB800000`; VID offset 24576 avoids MLC paired pages | Old NAND set-top boxes / industrial PCs converted to Linux; low-level storage layout for NAS systems (OMV / Armbian / Debian) |
| **NAND driver fixes** | `NAND_NEED_SCRAMBLING` (send SEED), DT ECC 40/1024 + BCH encoding 6, preserve scrambling bit bit19 on read/write, physical raw-read fallback for empty pages, flush cache before DMA, clear ECC status buffer before read, `NAND_CE0`, read/write/erase CFG=`0x800000e5` | Any Amlogic NFC platform porting a mainline kernel |
| **env = NOWHERE** | Environment variables compiled into the binary, `saveenv` removed | Devices with aging / unstable NAND writes, avoiding an unbootable env after corruption |
| **OTG PHY dual host** | Conditional GXL "force device mode" quirk (`patch_kernel_usb.py`) | GXL boxes that want to turn the flashing port into a second USB Host (attach USB drives / HDD enclosures for NAS) |
| **TFTP segmented writing** | Split large images into ≤244MiB segments, `tftpboot` + `nand write` segment by segment | Devices with RAM smaller than the image (1GB RAM flashing a 974MB system) |
| **Kernel logo handoff** | fbcon (1.8 seconds) + initramfs ply-image (about 5 seconds), two-stage | Boot splash optimization for any embedded Linux |
| **noVNC remote** | Direct DRM scanout read + RFB over WebSocket, standard library only | Remote operation of headless/offline devices, out-of-band management after turning a box into a server |
| **U-Boot upgrade method** | Derive from a verified build directory + guard self-checks (§13) | Upgrading U-Boot on all Amlogic devices based on stock BL2 |

### 12.2 Typical Derivative Scenarios

**1) Flash other ARM64 Linux distributions (Armbian / Debian / Ubuntu)**

The boot layer is fully reusable: `boot-diag-r80.img` provides U-Boot, `ubi part` + `ubi read` read volumes, and `booti` boots any
arm64 `Image` + DTB. Replace the kernel/dtb volumes with the target distribution's kernel and device tree, and the rootfs volume with the
corresponding squashfs/ext4. The DTB must keep this board's NAND and USB modifications (§8.1), otherwise peripherals will not work.

**2) Turn the box into a NAS (OMV / Armbian / fnOS ARM edition)**

- The storage layer directly reuses this project's UBI layout and MLC paired-page avoidance strategy (§1.3)
- To attach 2.5-inch hard drives / USB drives: apply the dual-host patch in §8.1 so both USB ports can mount storage
- Note this board has 100M RMII; NAS throughput tops out at about 11MB/s; choose a gigabit model if you need higher throughput
- **fnOS is currently an x86_64 platform** and cannot run directly on this SoC; if an ARM version is released, the boot chain,
  UBI layout, and TFTP deployment method in this document will apply just as well

**3) Other Amlogic GXL / G12 boxes**

The NAND driver fixes (scrambling / ECC / empty pages) and env NOWHERE strategy are not tied to a specific model; only swap the DTB and
NAND geometry parameters to port them. The bad-block skip region must be recalibrated against the target machine's actual BBT;
**do not copy this board's `0x800000–0xB800000` verbatim**.

**4) Set-top box as an out-of-band management / remote desktop terminal**

The noVNC approach (§9) does not depend on Lakka: cross-compile `drmcap.c`, deploy `vncsrv.py` + the noVNC front end, and it can be used
directly on systems such as Armbian/OMV.

### 12.3 Parts That Cannot Be Copied Directly

| Item | Reason |
|---|---|
| NAND geometry parameters (PEB / LEB / VID offset) | Bound to the specific chip; must be retested when changing chips |
| Bad-block skip-region offset | This board's BBT is untrustworthy; actual bad-block locations vary by unit |
| DTB | Must match the target board's memory, PHY, USB, and audio codec wiring |
| `8189fs.ko` | vermagic must match the target kernel exactly; rebuild when changing kernels |
| Static IP / serial device name | Adjust for the actual network and COM port |

## 13. U-Boot Version Upgrade Guide

The current U-Boot is upstream 2026.10 + local patches, wrapped in the stock FIP and loaded by the stock BL2.
When upgrading to a newer version (e.g. 2027.x), follow the process below to keep risk as low as possible.

### 13.1 Iron Rule: Derive from a Verified Build Directory

```bash
# 正确：从上一版实机验证过的目录复制
cp -a /root/uboot/verify_r80 /root/uboot/verify_r90

# 错误：用源码 defconfig 重新生成配置 —— 会丢失实机修正
make b860av11t_min_defconfig     # 或 b860av11t_net_defconfig
```

This is not a theoretical risk — it happened on this board: the source defconfig had `CONFIG_ENV_IS_NOWHERE=y`, but the r78
actual hardware `.config` had `CONFIG_ENV_IS_IN_UBI=y`; the dtb load address was `0x10000000` in one and `0x20000000` in the other.
Rebuilding directly from defconfig changes bootcmd and therefore boot behavior. When upgrading, replace only the U-Boot source tree, keep
`.config` and board patches, then run `make olddefconfig` so newly added options take their defaults.

### 13.2 Upgrade Steps

| Step | Command / checkpoint |
|---|---|
| 1. Back up the current bootloader | Save `boot-diag-r80.img` and the serial boot log; confirm it can be flashed back |
| 2. Record the current configuration | In the build directory (`O=`), `cp .config .config.r80.bak`; after upgrading, `diff` should show only expected differences |
| 3. Update the source tree | In `/root/uboot/u-boot`, `git fetch && git checkout v2027.xx`; do not `git clean -xdf` (it removes local patches); build directory `verify_rXX` is unaffected |
| 4. Reapply local patches | NAND driver fix chain, env NOWHERE, bootargs, banner; apply each with `git apply` and review manually |
| 5. olddefconfig | `make olddefconfig`, then check whether new Kconfig options enable unwanted features by default (size!) |
| 6. Build | Generate `u-boot.bin`, record its size (this file itself is not the hard-constraint object) |
| 7. BL33 encrypted packaging | `aml_encrypt_gxl --bl3enc --input u-boot.bin`; **encrypted container `bl33.bin.enc` ≤ 524288 B (0x80000)** is a hard limit; ≤ 496KiB recommended to leave alignment margin |
| 8. FIP repack | `repack.py <原厂 GA 镜像> <bootloader.bin> <bl33.enc> <out.img>` |
| 9. Guard self-check | `bl2 unchanged=True` / `outside=0` / `selfconsistent=True`; stop if any check fails |
| 10. In-memory verification | `tftpboot 0x01000000 u-boot.bin` + `go 0x01000000` chain boot; confirm the banner and new version over serial, and confirm NAND read/write works |
| 11. Write to flash | Only write NAND after step 10 passes; reconfirm the old image is backed up before writing |

### 13.3 Known Risk Points When Crossing Major Versions

| Risk | Symptom | Handling |
|---|---|---|
| **Encrypted container over 512KB** | Stock BL2 ErrorCHK verification fails, no boot | Disable non-essential features such as video / Logo / wget; if necessary, use a two-stage U-Boot |
| **FIP header size not synced** | Container length changed but still loaded using the old size | Must also update the bl33 size field in the FIP header (offset `0xC000`) |
| **Kconfig defaults changed** | Size balloons or features go missing | After every `olddefconfig`, diff `.config` and confirm item by item |
| **NAND driver API changes** | Compiles but probe fails / read-write behaves abnormally | Recheck the NAND fix list in §12.1: SEED, scrambling bit, ECC status clearing, DMA flush |
| **DTS drift** | NAND / Ethernet / USB not working | Decompile with `dtc` and compare; confirm board-level nodes are still present |
| **env backend reset** | bad CRC / env write hang returns | Confirm `CONFIG_ENV_IS_NOWHERE=y` and that `saveenv` was not compiled in |

### 13.4 Rollback

- Keep a working `boot-diag-rXX.img`; it can be flashed back at any time with USB Burning Tool
- **Do not check Erase flash when flashing back**, or the UBI region will be erased and the system will need to be written again
- After flashing back, confirm over serial that the banner version and UBI volumes can be read normally; userdata/rootfs volumes are unaffected

### 13.5 Real Regression Records of This Project (version-iteration lessons)

| Version | Problem | Root cause |
|---|---|---|
| r37a | Async SError after probe (ELR=vsnprintf) | `INIT_LIST_HEAD(&nfc->chips)` missing; DM private memory not zeroed, list head is garbage |
| r38 | NAND completely nonfunctional | Missing `NAND_NEED_SCRAMBLING` → no SEED, N2M/M2N data pump does not start; DT ECC must be 40/1024 |
| r41 | Writes do not land | BCH enum mismatch: bch field for the 40/1024 row should be 6 (hardware encoding); writing 5 misaligned the command configuration |
| r43 | Write command does not complete | Missing drain between M2N and PAGEPROG: `IDLE×2 + wait_cmd_finish` |
| r44 | Writes still invalid | PAGEPROG encoding `0x14100` (low 8 bits are READ0) → changed to `0x14010` |
| r50test | BL33 slot enlargement had no effect | Only the container length was changed; the FIP header size field was not synced |
| r51 | No fastboot | Configuration was later than image generation time; rebuild and size trimming required |
| r54 | Endless reboot at boot | `nandprobe` parentheses misplaced, released a DMA buffer still in use |
| r65 | NAND writes invalid | Scrambling bit (bit19) was removed from the write path; data could not be written into pages |
| r66 | Wrong fix direction | Tried to fix r65 by "removing only the scrambling bit", disproved on real hardware; the correct approach is to preserve the scrambling bit (write with the scrambling bit, `ecc=0`) |
| r68/r69 | Random corruption in large contiguous reads | ECC status buffer not cleared; polling read the completion flag left over from the previous page |
| r80 | Env write hang risk | `saveenv` on MLC triggered a NAND wait timeout → changed to env NOWHERE |

## 14. Sources and Licenses

| Component | Version / Source | License |
|---|---|---|
| Scripts and documentation in this repository | Original to this project | GPL-2.0 (see [LICENSE](LICENSE)) |
| U-Boot (`boot-diag-r80.img`) | upstream 2026.10 + local NAND/env patches | GPL-2.0+ |
| Linux kernel (`kernel-usbhost.new`) | 6.18.0 (Lakka AMLGX build tree) + `patch_kernel_usb.py` | GPL-2.0 |
| Lakka / LibreELEC rootfs (SYSTEM) | Lakka AMLGX.aarch64 6.1-devel baseline | GPL-2.0 |
| noVNC front end | 1.6.0, [novnc/noVNC](https://github.com/novnc/noVNC) | MPL-2.0 |
| XMB icon themes | [libretro/retroarch-assets](https://github.com/libretro/retroarch-assets) | See upstream |
| Amlogic USB Burning Tool | Third party, not distributed by this repository | Vendor license |

**GPL compliance note**: `boot-diag-r80.img` and `kernel-usbhost.new` are redistributions of GPL binaries; corresponding source =
upstream released versions + the build scripts and patches in this repository:

- U-Boot: `04-build-scripts/make_r80.sh` and `pack_r80.sh` record the source tree, configuration derivation, and packaging steps;
  the NAND driver fix chain r76/r77 changes are described in §5.
- Kernel: `04-build-scripts/patch_kernel_usb.py` is the complete patch (three precise replacements); everything else is compiled as-is
  from the Lakka AMLGX 6.18.0 build tree.

To request a full source snapshot or the corresponding patches, open an Issue.

**Security note**: `storage-backup-public.tgz` in Releases has `storage/iwd/` and `storage/.cache/connman/` removed; it contains no WiFi SSID,
password, or PSK hash. Nothing in this repository contains any credentials.
All addresses in this document are RFC 5737 documentation example subnet (`192.0.2.0/24`) and do not represent an actual deployment network; replace them with your own.

[中文](#chinese)

