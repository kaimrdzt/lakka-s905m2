# B860AV1.1-T 定制 Lakka 系统 — 完整移植工程

<p align="center"><a href="#english">English</a> | <a href="#chinese">中文</a></p>

<a id="chinese"></a>

> **硬件**：Amlogic S905M2（GXL 家族，Rev 21:d）/ 1GiB DDR3 @792MHz / 8GiB Micron MLC NAND
> **整机**：ZTE B860AV1.1-T 机顶盒
> 生成：2026-11-26（首版 11-25，主题/颜色修复/720p 更新至 11-26）。
> 本项目是当前盒子上运行固件的**完整可复现交付**：刷机脚本、编译脚本、配置、全部避坑记录。

**大体积二进制不入库**（UBI 镜像 974MB、bootloader 110MB、内核、脱敏 `/storage` 备份）请到
[Releases](../../releases/latest) 下载。本仓库只跟踪脚本、配置与文档，克隆体积 < 200KB。

> 本仓库发布的是**已验证可复现的移植产物**，不是通用发行版。刷机有变砖风险，请先读完全部
> 「避坑指南」（§5）再动手。

---

## 1. 系统概览

### 1.1 硬件规格

| 项目 | 规格 | 备注 |
|---|---|---|
| SoC | Amlogic S905M2（GXL 家族，Revision 21:d，`amlogic,meson-gxl`） | 4× Cortex-A53（r0p4）；BL2 固定 1200MHz，运行走 SCPI DVFS |
| GPU | ARM Mali-450 MP3（`arm,mali-450`） | gp + pp0/pp1/pp2，lima 驱动，无 regulator（固定电压） |
| 内存 | 1GiB DDR3 @ 792MHz，单 Rank，2T-CL11 | `mem=1008M`，顶部 16MiB 划给 ramoops；可用约 784MiB + CMA 128MiB |
| 存储 | 8GiB Micron MT29F64G08CBABA（U-Boot 报名 `MT29F64G08CBABBWP`）**MLC NAND** | 1 LUN × 4096 blocks，page 2KiB，BCH40/1K，8-bit @250MHz，随机化强制开启，无 SLC 模拟 |
| 网络 | Synopsys DWMAC1000（`snps,dwmac-3.70a`），10/100M RMII | 片内 PHY 地址 8（OUI `0181.4400`），irq 40 |
| WiFi | SDIO（mmc2，high-speed），RTL8189FS | DTB 兼容 `brcm,bcm4329-fmac`，需专用 `8189fs` 驱动（§3.3） |
| 蓝牙 | DTB 声明 BCM43438（shutdown GPIO 96），**节点已禁用** | 芯片型号未逐一核实，无天线/未贴片 |
| USB | dwc3-meson-g12a（2× USB2，0× USB3）+ xhci + dwc2 OTG | OTG 默认 peripheral，仅用于刷机；v80 补丁可改双 Host（§8.1） |
| 存储扩展 | TF 卡槽（`d0072000.mmc`，带 CD 热插拔 GPIO） | 可作第二存储/启动盘 |
| 显示 | HDMI 1.4（`amlogic,meson-gxl-dw-hdmi`） | 720p60 / 1080p60 实测可用 |
| 音频 | AIU（I2S）+ HDMI 音频 | dai-link-1 的 codec-0 必须指向 `AIU_HDMI CTRL_I2S`，指向 `hdmi_tx` 会与 dai-link-2 冲突（-EBUSY） |
| 视频解码 | GXL VDEC（`amlogic,gx-vdec`） | H.264 / H.265 硬解 |
| 调试串口 | `ttyAML0`，115200 8N1 | 板载焊点，USB-CH340 转接 |

> 规格来自本机 DTB 反编译与开机日志交叉核对；未贴片的外设（蓝牙）如实标注，不写推测值。

### 1.2 启动链

```
BL1(ROM) → BL2(原厂 2018) → BL30/BL31(原厂) → BL33 = U-Boot 2026.10 diag-r80（自编）
  → ubi part → 读 kernel/dtb 卷 → booti → Linux 6.18.0（Lakka rootfs）
```

### 1.3 NAND 物理布局（8GiB = 4096 PEB × 2MiB）

| 区域 | 范围 | 说明 |
|---|---|---|
| nand-boot（原厂引导区） | 0x0 – 0x800000 | BL2/BL33 等，UBT 刷机时更新 |
| **跳过区** | 0x800000 – 0xB800000 | 原厂 nand_key(PEB10-13) + 真坏块 90/91，**永不使用** |
| **nand-ubi（UBI 数据区）** | **0xB800000 (184MiB) – 8GiB** | 前 92 个 PEB 不用，剩余 4004 PEB 建 UBI；UBI 区内实测坏块 0 |

> 全片曾由原厂 MFTL 标记 59 个坏块，但该 BBT 不可信（有假坏块，也有真坏块），因此 UBI 布局
> 直接按“跳过前 92 PEB + 后段全好”规划，不依赖 BBT 扫描结果。

### 1.4 UBI 卷表（r79 镜像，attach 后 6 卷）

| vol | 名称 | 类型 | 大小 | 内容 |
|---|---|---|---|---|
| 0 | rootfs | static | **1GiB**（数据 922MiB，预留升级余量） | Lakka SYSTEM squashfs（只读挂载） |
| 1 | kernel | static | 29.3MiB | Linux 6.18.0 arm64 Image |
| 2 | dtb | static | 30KB | 补丁版 DTB（**蓝牙节点已禁用**） |
| 3 | userdata | dynamic | ~6.49GiB（3374 PEB） | UBIFS → /storage（所有可写内容） |
| 4/5 | uboot_env / uboot_env_r | dynamic | 8KB×2 | 空置（env 已改 NOWHERE，不使用） |

### 1.5 U-Boot r80 特性（相对 r78）

- **PREBOOT 移除**：开机不再有 `splash/bmp/already selected` 三行噪声
- **env = NOWHERE**：环境变量编译进二进制；`Loading Environment from nowhere... OK` 瞬时完成，无 bad CRC，**永不写 NAND env**（该板 MLC 上写 env 有卡死史）
- bootargs/bootcmd/IP 全部编译期固定；`saveenv` 命令不编译
- mtdparts：`nand0:0x800000@0(nand-boot),-@0xb800000(nand-ubi)`
- 网络（wget/ping）与 NAND 救援命令保留

### 1.6 /storage 可写层（全部配置的所在，升级系统不丢）

| 路径 | 内容 |
|---|---|
| `kernel-overlays/8189fs/lib/modules/6.18.0/8189fs.ko` | WiFi 驱动（**无双接口版**，见避坑 #1） |
| `.cache/kernel-overlays/8189fs.conf` | 注册 overlay（开机 depmod 纳入 modprobe） |
| `.config/modules-load.d/8189fs.conf` | 开机自动加载 |
| `.config/modprobe.d/8189fs-ps-off.conf` | `rtw_power_mgnt=1 rtw_ips_mode=0`（禁省电） |
| `.config/udev.rules.d/90-wifi-powersave-off.rules` | mac80211 层 power_save off |
| `.config/system.d/` | connman 启用 drop-in、iwd STATE_DIRECTORY=/storage/iwd、time-restore/save/timer 单元 |
| `.config/connman_main.conf` | connman 主配置（eth0 黑名单） |
| `.config/timesyncd.conf.d/ntp.conf` | NTP=ntp.aliyun.com ntp.tencent.com + Google fallback |
| `.cache/timezone` | `TIMEZONE=Asia/Shanghai`（EnvironmentFile 格式） |
| `.cache/last-epoch` | 断电时间恢复时间戳（每分钟刷新） |
| `iwd/<你的SSID>.psk` | WiFi 凭据（iwd 持久化到 /storage/iwd） |
| `assets/xmb/monochrome/png/bg.png` + `assets/branding/logo.png` | 自定义开机画面 |

---

## 2. 刷机使用说明（给新盒子/救砖）

### 2.1 前置条件

- Windows PC：以太网口静态 IP `192.0.2.10/24`（示例网段，按实际修改）；COM14 串口线（USB-CH340，115200）
- 工具：Amlogic USB Burning Tool v2.x；管理员权限

### 2.2 第 1 步：刷 bootloader（UBT）

UBT 刷机工具为第三方软件，本仓库不重新分发。请从 Amlogic 官方或可信渠道获取
`Amlogic_USB_Burning_Tool_v2.2.4.zip`（含驱动），下载后核对
[02-flash/SHA256SUMS.txt](02-flash/SHA256SUMS.txt) 中的校验值。

1. 盒子断电 → 短接进 ROM 模式 → USB 连 PC
2. 解压并运行 UBT，加载 `01-images\boot-diag-r80.img`，**勾选 Erase flash** → 刷写
   （此时整 NAND 为空+镜像内容，UBI 区会没数据——第 2 步补上）
3. 断电重上电，串口应显示横幅 `diag-r80` 并停在 `Volume kernel not found!` 的诊断 shell（**正常**，UBI 还没灌）

### 2.3 第 2 步：灌 UBI 系统（02-flash\刷入UBI.bat）

1. 串口终端连接 COM14 后**关闭它**（bat 需要独占 COM14）
2. 右键 `刷入UBI.bat` → 管理员运行
3. 脚本自动：探测提示符 → 擦除 0xB800000..8GiB → TFTP 4 段传输（各约 36 秒）→ 写入落盘（各约 1-2 分钟，心跳显示）→ `ubi info` 验证
4. 看到 `全部完成` → 串口输入 `reset`
5. 首次启动：userdata 自动格式化 → 进 Lakka（此时 WiFi/时区等还是裸机状态，正常）

### 2.4 第 3 步：恢复 /storage 配置备份（03-restore）

```sh
# PC 上（storage-backup-public.tgz 从 Releases 下载，已脱敏）：
scp storage-backup-public.tgz root@192.0.2.100:/tmp/
# 盒子上：
cd / && tar xzf /tmp/storage-backup-public.tgz
sync && reboot
```

> 公开版备份已剔除 `storage/iwd/` 与 `storage/.cache/connman/`，**不含任何 WiFi 凭据**。
> 恢复后需自行重新连接 WiFi：`iwctl station wlan0 connect <你的SSID>`。

### 2.5 最终验证清单

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

## 3. 日常维护

### 3.1 更新系统（换 SYSTEM squashfs）

```sh
# 新 SYSTEM < 1GiB（rootfs 卷上限），放 /storage 后：
ubiupdatevol /dev/ubi0_0 /storage/SYSTEM-new.squashfs
sync && reboot
```
- /storage 全部配置自动保留；若新镜像内核版本变了，见 3.3

### 3.2 更新内核 / DTB

```sh
ubiupdatevol /dev/ubi0_1 /storage/Image-new    # 内核卷
ubiupdatevol /dev/ubi0_2 /storage/dtb-new      # dtb 卷
sync && reboot
```
- U-Boot bootcmd 按卷名读取，卷名不变无需动 bootloader

### 3.3 添加/更新驱动（8189fs 模式，无需重刷系统）

1. WSL 编译（内核树/工具链见 §4.4）
2. 盒子上部署：
   ```sh
   mkdir -p /storage/kernel-overlays/<名>/lib/modules/$(uname -r)
   cp xxx.ko /storage/kernel-overlays/<名>/lib/modules/$(uname -r)/
   echo /storage/kernel-overlays/<名> > /storage/.cache/kernel-overlays/<名>.conf
   echo <模块名> > /storage/.config/modules-load.d/<模块名>.conf   # 开机自动加载（可选）
   echo "options <模块名> 参数=值" > /storage/.config/modprobe.d/xxx.conf  # 参数（可选）
   ```
3. 重启即生效（`kernel-overlays-setup` 自动 depmod + `systemd-modules-load` 自动 modprobe）
4. ⚠️ 每次更换内核后，overlay 驱动必须按新内核重编（vermagic 必须匹配）

### 3.4 备份 /storage（强烈建议定期做）

```sh
cd / && tar czf /tmp/storage-backup.tgz storage/.config storage/.cache storage/assets storage/iwd storage/kernel-overlays storage/lib
# 然后 scp 回 PC
```

---

## 4. 编译复现

### 4.1 环境

- WSL Ubuntu 22.04（root）
- U-Boot 源码：`/root/uboot/u-boot`（含 meson NAND 修复链 r76/r77）
- 内核源码：`/mnt/e/Lakka/build.Lakka-AMLGX.aarch64/build/linux-7d0a66e4bb9081d75c82ec4957c50034cb0ea449`（6.18.0）
- 交叉工具链：`/mnt/e/Lakka/build.Lakka-AMLGX.aarch64/toolchain/bin/aarch64-libreelec-linux-gnu-`
- aarch64-linux-gnu-（U-Boot 用，apt 安装）
- 加密工具：`/root/uboot/aml-fip/fip/gxl/aml_encrypt_gxl`
- 打包器：`D:\b860\uboot-2026.10\repack.py`（护栏：BL33≤0x80000、BL2 逐字节不变、差异仅限 bootloader 区）

### 4.2 编译 U-Boot r80（04-build-scripts\make_r80.sh）

关键规则（违反即翻车）：
1. **必须从上一次实机验证过的构建目录派生**（`cp -a verify_r79 verify_r80`），**绝不能**从源码 defconfig 开始——defconfig 与实机配置有漂移（dtb 加载地址等）
2. 只改目标项 + 横幅，其余不动
3. 改 .config 后必须 `make olddefconfig`

r80 的改动 = r79 + env 后端切换：
```
CONFIG_ENV_IS_IN_UBI=y        → # ... is not set
# CONFIG_ENV_IS_NOWHERE ...   → CONFIG_ENV_IS_NOWHERE=y
横幅 diag-r79 → diag-r80
```
Kconfig 会自动置 `ENV_IS_DEFAULT=y`（env/Kconfig:94 派生规则）并**不编译 saveenv**（cmd/nvedit.c:462 守卫）——这是预期行为。

编译：
```bash
make -C /root/uboot/u-boot O=/root/uboot/verify_r80 olddefconfig
make -C /root/uboot/u-boot O=/root/uboot/verify_r80 -j8 CROSS_COMPILE=aarch64-linux-gnu-
```

### 4.3 打包 bootloader（04-build-scripts\pack_r80.sh）

```bash
aml_encrypt_gxl --bl3enc --input u-boot.bin      # 容器必须 ≤ 524288 B
repack.py <v71 GA 原厂镜像> <bootloader.bin> <bl33.enc> <out.img>
# 护栏自检必须全部通过：bl2 unchanged=True / outside=0 / selfconsistent=True
```

### 4.4 编译 UBI 镜像（04-build-scripts\build_ubi_r79.sh）

- 输入：`out/ubi2/{SYSTEM,Image,dtb}`（SYSTEM=922MiB squashfs、Image=29.3MiB、dtb=补丁版）
- 几何：PEB 2MiB / LEB 2064384 / min-io 8192 / VID offset 24576
- 卷大小账：rootfs 512PEB(1GiB) + kernel 15 + dtb 1 + userdata 动态 + env 2 + 布局 2 + BEB 78 + 余 20 ≤ 4004
- `ubinize -m 8192 -s 8192 -O 24576 -p 2097152`
- 产物头部自检：前 3 个 PEB 的 EC=0x55424923 / VID=0x55424921（脚本只抽查前 3 个，不是全盘扫描）

### 4.5 TFTP 分段（04-build-scripts\split_tftp_segments.py）

244/244/244/242 MiB 四段，与 flash_ubi.ps1 的写地址一一对应（0xB800000 起）。镜像版本变了必须重新切分。

### 4.6 编译 8189fs 驱动（含关双接口）

源码：`/root/rtl8189fs`（jwrdegoede/rtl8189ES_linux 的 rtl8189fs 分支）

**关键修改（去掉 wlan1 虚拟接口）**：Makefile 第 26 行注释掉并发模式
```makefile
#ccflags-y += -DCONFIG_CONCURRENT_MODE
```
（警告：不要用 `rtw_virtual_iface_num=0` 方式——该驱动在 0 时会内核 Oops，实测 `rtw_port_switch_chk` 崩溃）

```bash
cd /root/rtl8189fs
make clean
make ARCH=arm64 CROSS_COMPILE=<工具链>/aarch64-libreelec-linux-gnu- KSRC=<内核树> -j4
# 产物 8189fs.ko，modinfo vermagic 必须 = 目标内核 `uname -r`
```

### 4.7 部署审计（强烈建议每次刷机前跑）

- U-Boot：strings 检查横幅/残留字符串；config diff 只允许预期改动；DTB cmp 一致
- UBI：解析 VID 卷表 + 三个 static 卷内容 sha256 与源文件比对（本会话曾借此抓到 dtb 未回写的回归）

---

## 5. 避坑指南（全部为实机踩过的坑，按危险度排序）

### 🔴 变砖/卡死级

1. **env 永远不要 saveenv**：MLC + 本驱动上 `saveenv`/`env erase` 曾导致系统卡死（NAND 等待超时）。这就是 r80 改 NOWHERE 的动机——env 编译进二进制，写路径被连根移除。
2. **UBT 勾 Erase flash = 全片擦除**：先刷 bootloader 再灌 UBI（顺序见 §2.2/2.3）；顺序反了或漏灌 UBI → `Volume kernel not found`。
3. **不要用信任 BBT 的工具重刷 bootloader**：本板 OOB 坏块标记不可信（开机日志 Bad eraseblock 0-7 为假象、90/91 为真），BBT 不可靠。只用 UBT / 已验证的 U-Boot 命令流。
4. **MLC 无 SLC 模拟**（内核补丁放行）：掉电可能损坏 userdata。定期备份 /storage；避免频繁硬断电。

### 🟠 脚本/流程级

5. **U-Boot 的 nand erase/write 结束时不打印 "OK"**，只返回提示符——完成判定必须以提示符返回为准。等 "OK" = 假卡住（v4 教训）。
6. **长命令（nand write）执行期间，排队输入的命令会被 ctrlc() 轮询吃掉**（nand_util.c 循环里有 ctrlc 检查）。灌入脚本必须严格串行：上一条命令的提示符返回并确认后，才发下一条（v5→v6 教训）。
7. **tftpboot 收尾的 "U-Boot> " 提示符会残留在接收缓冲里**，若不消化，下一段的"等提示符"会瞬间假匹配 → "写入完成 0 秒"（v5 教训）。每步完成后清空/消化缓冲。
8. **驱动修改后必须回写源镜像目录再打包**：只把补丁文件拷到盒子、不回写打包源目录 → 下次打包悄悄回退（本包 dtb 曾因此差点回归蓝牙问题，审计脚本抓到）。
9. **COM14 独占**：运行灌入 bat 前关闭所有串口终端，否则 `Access to the port 'COM14' is denied`。
10. **给盒子发命令必须带回车**：裸字符（如 SysRq 的 `b`）打在提示符上不提交，永远等不到新提示符（v2/v3 教训）。
11. **TFTP 分段与镜像必须版本匹配**：镜像变了必须重新切分（split_tftp_segments.py 有总量断言）。
12. **USB Burning Tool 刷机镜像基于 v71 原厂 GA**：repack 后除 bootloader 区外全是原厂内容——刷完必须灌 UBI，不存在"bootloader 刷完就能启动"。

### 🟡 配置/编码级

13. **PowerShell 5.1 读 .ps1 的 UTF-8 中文需要 BOM**；用工具改文件后 BOM 会被剥掉，要重新补。bat 用 `chcp 65001` + UTF-8 BOM。
14. **systemd unit 里 `%s` 是 specifier**（会被展开成 shell 路径）——`date +%s` 必须写成 `date +%%s`（曾导致时间戳文件内容变成 "/bin/bash"）。
15. **EnvironmentFile 要求 KEY=value 格式**：tz-data.service 读 `/storage/.cache/timezone`，写裸 `Asia/Shanghai` 无效，必须 `TIMEZONE=Asia/Shanghai`。
16. **/var 是 tmpfs**：一切需要跨重启的状态（iwd PSK、timesync 时间戳）必须重定向到 /storage——iwd 用 `Environment=STATE_DIRECTORY=/storage/iwd`（drop-in），connman 用官方覆盖点 `/storage/.config/connman_main.conf`。
17. **connman 与 iwd 分工**：本镜像 connman 没有 wifi 后端（`connmanctl services` 报 Not supported），必须 iwd 管认证 + connman 管 DHCP；`/dev/.kernel_ipconfig` 存在（内核 ip= 静态 IP）会让 connman.service 被 Condition 跳过 → 用 drop-in 清除 Condition 并 enable。
18. **双网卡不是 Lakka 的问题**：RTL8189FS 驱动编译了 `CONFIG_CONCURRENT_MODE` 就会创建 wlan1；且 wlan1 会被 connman 连接并抢默认路由导致**公网不通**（wlan0 正常）——必须从 Makefile 关并发模式重编。
19. **两层省电都要关**：驱动层 `rtw_power_mgnt=1`（lps_level 只是档位不是开关）+ mac80211 层 `iw dev wlan0 set power_save off`（iwd 连接时会自动重开 → 用 udev 规则压制）。
20. **systemd EnvironmentFile / specifier / drop-in Condition** 三件套语义易错：改 ConditionPathExists 用空值清除；`%%` 转义；drop-in 放 `/storage/.config/system.d/<unit>.d/override.conf`（systemd UnitPath 含此目录）。
21. **恢复备份必须在 RetroArch 停止时改它的配置**：RA 退出时把内存配置写回 retroarch.cfg——恢复 tar 时如果 RA 在跑，重启后语言/设置会被内存默认值覆盖回英文（实测 user_language 12→0）。修复脚本 `03-restore\fix_cn_language.sh`：停 RA → sed 改 `user_language="12"` → 补 Ozone 字体（regular/bold 也要，共 3 处）→ 启动 → 启动后复核没被回滚。
22. **中文字体共 3 个落点**：`/storage/assets/xmb/monochrome/font.ttf` + `/storage/assets/ozone/regular.ttf` + `/storage/assets/ozone/bold.ttf`（均为 DroidSansFallbackFull 4,033,420 B，md5 ebc9b34d…）。漏掉 Ozone 两处则部分界面仍出方框。
23. **8189fs 驱动日志默认 INFO 级会刷爆串口**：响应 ARP、EDCA 调度等都打 `RTW:` 行（内核 cmdline `loglevel=7` 全放行），流量越多刷得越凶，交互 shell 被"淹没"（回车其实生效了，只是看不见）。修复：`rtw_drv_log_level=2`（运行时 `echo 2 > /sys/module/8189fs/parameters/rtw_drv_log_level` 立即生效；持久化写入 modprobe.d，已包含在 `8189fs-ps-off.conf` 中）。

---

## 6. 仓库结构与下载资产

### 6.1 仓库内文件（Git 跟踪）

```
lakka-s905m2\
├── README.md                          本文档
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

### 6.2 Releases 资产

| 文件 | 大小 | 用途 | sha256（前 8 位） |
|---|---|---|---|
| `b860av11t_ubi_r79.ubi` | 974MB | UBI 系统镜像（串口/TFTP 灌） | `6aa05193` |
| `boot-diag-r80.img` | 110MB | U-Boot r80 bootloader（UBT 刷） | `6a597992` |
| `kernel-usbhost.new` | 29.3MB | 双主机模式内核（ubiupdatevol 更新） | `af06f225` |
| `dtb-usbhost.new` | 30KB | 配套 DTB | `418a9b34` |
| `storage-backup-public.tgz` | 70MB | `/storage` 配置备份（已脱敏） | `c75f758f` |
| `SHA256SUMS.txt` | — | 全量校验值 | — |

完整校验值见 Releases 页面的 `SHA256SUMS.txt`。


## 7. 已知非问题项

| 项 | 定性 |
|---|---|
| systemd unit 0755 警告串 | Lakka 上游原生形态，无功能影响 |
| `lima no regulator (mali)` | GPU 固定电压运行 |
| HDMI `Unknown ELD` / 用户模式提示 | 显示端协商，画质正常即忽略 |
| 坏块 0-7（假）/90-91（真） | 架构现实，PEB92 起步已绕开 |
| MLC 无 SLC 模拟 | 内核补丁放行，靠备份兜底 |
| U-Boot `NAND: 0 MiB` | 显示层问题 |
| NTP 主用阿里云、Google 兜底 | 网络环境决定（国内 NTP IP 被该网络限制） |

## 8. USB 双口说明（实测定论，v80 内核补丁后）

| 口 | 属性 | 说明 |
|---|---|---|
| **主机口** | USB Host（xhci） | U 盘/手柄接收器**即插即用**，Lakka 自动挂载到 `/storage/roms/<名>-usb-*`（vfat/NTFS 均可），ROM 拷进去即可在 RA 列表出现 |
| **刷机口**（OTG 口） | USB Device（dwc2 UDC，原厂设计） | **只用于 USB Burning Tool 刷机**（ROM 模式由 BootROM 自己的 USB 栈处理，与 Linux 无关）。运行时插 U 盘**永远不识别——这是原厂硬件设计**，不是故障 |

DT 依据：glue 节点 usb@d0078080 的 dr_mode="peripheral" + dwc2 子节点 dr_mode="peripheral"，与上游 Khadas VIM1 配置完全一致。尝试改成 host 在 GXL 上无效（PHY 初始化怪癖：必须以设备模式启动，主机模式依赖 OTG role-switch，而 glue 非 otg 模式时该机制不注册——实测两个口全死，已回滚）。

U 盘实测：usb 1-1 "Disk 2.0" (346d:5678) → usb-storage → sda (8.05GB) → sda1 (vfat) → 自动挂载 /storage/roms/sda1-usb-VendorCo_Product。ROM 通过 SMB（\\192.0.2.100\storage\roms\）或网络拷入。

### 8.1 双主机模式内核补丁（v80，已实装）

主线 GXL glue 驱动有怪癖：OTG PHY 初始化时无条件强制设备模式（"for DWC2 init"），导致刷机口在 Linux 下只能作设备。补丁（dwc3-meson-g12a.c，3 处改动）：

1. struct 加 `bool dwc2_en`
2. probe 读 dwc2 子节点 status 存 `priv->dwc2_en`
3. `dwc3_meson_gxl_usb2_init_phy` 的强制设备模式改为条件化：`(i == USB2_OTG_PHY && priv->dwc2_en)`

配合 DT：glue `dr_mode="host"` + dwc2 `status="disabled"` → OTG PHY 以主机模式启动，由 xhci 接管。

**实测**：两个 U 盘同时枚举（1-1 + 1-2），`sda`/`sdb` 双块设备，**双双自动挂载**到 `/storage/roms/`。补丁对 dwc2 启用的其它盒子保持原行为（OTG 口仍为设备）。

重编内核：`04-build-scripts` 流程，补丁脚本见 `patch_kernel_usb.py`（内核树 dwc3-meson-g12a.c 三处精确替换）。内核卷/DTB 卷用 `ubiupdatevol` 原位更新，无需重灌 UBI。

## 9. 浏览器远程查看盒子画面（noVNC，已内置）

在 PC 浏览器（与盒子同网段）打开：

    http://192.0.2.100:6080/vnc_lite.html

点 **Connect** 即可看到 HDMI 实时画面，鼠标/键盘可直接操作盒子（RA 菜单导航）。

- 链路：drmcap（静态 C，DRM scanout 直读 5fps/960×540）→ /dev/shm → vncsrv.py（RFB over WebSocket，仅标准库）→ noVNC 前端（浏览器）
- 服务：`vncview.service`（已 enable，开机自启，位于 /storage/.config/system.d）
- 文件：`/storage/.config/bin/drmcap`、`/storage/.config/vnc/{vncsrv.py,vnc_run.sh}`、`/storage/novnc/`（noVNC 1.6.0 前端）
- 日志：`/tmp/drmcap.log`、`/tmp/vnc.log`；画面无更新属正常（脏区检测，静态画面输出极小）
- 恢复备份（03-restore）后无需重装——本节所有文件都在备份内

## 10. XMB 图标主题包（05-themes\，官方 8 套）

来源：[libretro/retroarch-assets](https://github.com/libretro/retroarch-assets)（官方资产库）。RA 源码限制：XMB 图标主题目录名是内置枚举，只有下面这些可选——**社区第三方主题（NeoActive/Retrorama 等）无法直接选用**，只能放进 `assets/xmb/custom/` 走 "Custom" 槽位（一次只能一个）。

| 主题 | 体积 | 风格 |
|---|---|---|
| systematic | 25M | 现代扁平（社区评价最高的一套）|
| flatux | 12M | 彩色 Flat UI |
| automatic | 9.5M | 跟随系统深浅色 |
| dot-art | 8.1M | 点阵像素风 |
| retrosystem | 4.9M | 复古主机风 |
| daite | 4.4M | 简洁线条 |
| flatui | 1.8M | 经典 Flat UI |
| pixel | 1.8M | 极简像素风 |

**安装**：盒子端 `05-themes\install_themes.sh`（拷到 /storage/assets/xmb/，copy-up 持久）。
**切换**：RetroArch 菜单 → 设置 → 用户界面 → 外观 → 菜单图标主题 → 选一个（即时生效）。

## 11. VNC 颜色修复记录（三层交换问题）

浏览器里颜色错误（棕色/红蓝互换）的完整因果链：
1. drmcap 旧版输出 BGRX 序 32bpp + vncsrv 声明 R@16 + noVNC 连接时**强制发送自己的 SetPixelFormat（R@0）覆盖服务端声明** → 红蓝互换；
2. 原 `vnc_run.sh` 的 `-c` 参数是针对"旧二进制+旧声明"的补偿（再交换一次凑对）；
3. 换新二进制（RGBX 输出）后 `-c` 残留 → 多一次交换 → 棕色。
最终修复：drmcap 输出真 RGBX（byte0=R）+ vncsrv 声明 R@0 + **去掉 `-c`** → 两端一致。修复文件已入 `03-restore\vnc-tools\`。

## 12. 可复用经验与衍生用途

本项目不是一次性适配：多数模块可以拆出来复用到其他 Amlogic 盒子、其他 ARM64 Linux 发行版，
以及 NAS 类系统。这一节明确哪些能借、怎么借、哪些不能照抄。

### 12.1 按模块划分的复用价值

| 模块 | 本项目做法 | 可复用到 |
|---|---|---|
| **引导链改造** | 保留原厂 BL2/BL30/BL31，只替换 BL33（U-Boot），FIP 重打包 + BL33 ≤ 512KB 护栏 | 任何想保留原厂引导、又想换 U-Boot 的 Amlogic 盒子（GXL / G12A / G12B） |
| **NAND → UBI** | 跳过原厂 key 区与真坏块，从 `0xB800000` 起建 UBI；VID offset 24576 避开 MLC 配对页 | 老 NAND 机顶盒/工控机改 Linux；NAS 系统（OMV / Armbian / Debian）的底层存储布局 |
| **NAND 驱动修复** | `NAND_NEED_SCRAMBLING`（发 SEED）、DT ECC 40/1024 + BCH 编码 6、读写保留加扰位 bit19、空页物理裸读兜底、DMA 前刷 cache、读前清零 ECC 状态缓冲、`NAND_CE0`、读写擦 CFG=`0x800000e5` | 任何 Amlogic NFC 平台移植主线内核 |
| **env = NOWHERE** | 环境变量编译进二进制，移除 `saveenv` | NAND 老化/写入不稳的设备，避免 env 写坏后无法启动 |
| **OTG PHY 双主机** | 条件化 GXL「强制设备模式」quirk（`patch_kernel_usb.py`） | 想把刷机口改造成第二个 USB Host 的 GXL 盒子（接 U 盘/硬盘盒做 NAS） |
| **TFTP 分段灌入** | 大镜像切成 ≤244MiB 分段，逐段 `tftpboot` + `nand write` | RAM 小于镜像体积的设备（1GB 内存灌 974MB 系统） |
| **内核 logo 衔接** | fbcon（1.8 秒）+ initramfs ply-image（约 5 秒）两段式 | 任何嵌入式 Linux 的开机画面优化 |
| **noVNC 远程** | DRM scanout 直读 + RFB over WebSocket，仅标准库 | 无显示器/无网络的设备远程运维、盒子改服务器后的带外管理 |
| **U-Boot 升级方法** | 从已验证构建目录派生 + 护栏自检（§13） | 所有基于原厂 BL2 的 Amlogic 设备升级 U-Boot |

### 12.2 典型衍生场景

**1）刷其他 ARM64 Linux 发行版（Armbian / Debian / Ubuntu）**

引导层完全复用：`boot-diag-r80.img` 提供 U-Boot，`ubi part` + `ubi read` 读卷，`booti` 启动任意
arm64 `Image` + DTB。把 kernel/dtb 卷换成目标发行版的内核与设备树，rootfs 卷换成对应
squashfs/ext4 即可。DTB 必须保留本板的 NAND 与 USB 修改（§8.1），否则外设不工作。

**2）把盒子改成 NAS（OMV / Armbian / fnOS ARM 版）**

- 存储层直接复用本项目的 UBI 布局与 MLC 配对页规避策略（§1.3）
- 想接 2.5 寸硬盘/U 盘：应用 §8.1 双主机补丁，两个 USB 口都能挂存储
- 注意本板是百兆 RMII，NAS 吞吐上限约 11MB/s；要高吞吐请选千兆机型
- **fnOS 目前是 x86_64 平台**，不能直接跑在这颗 SoC 上；其 ARM 版本若推出，本文的引导链、
  UBI 布局、TFTP 部署方法同样适用

**3）其他 Amlogic GXL / G12 盒子**

NAND 驱动修复（加扰 / ECC / 空页）与 env NOWHERE 策略不依赖具体机型，换 DTB 与 NAND 几何参数
即可移植。坏块跳过区要按目标机器的实际 BBT 重新标定，**不要照抄本机的 `0x800000–0xB800000`**。

**4）机顶盒改带外管理 / 远程桌面终端**

noVNC 方案（§9）不依赖 Lakka：交叉编译 `drmcap.c`，部署 `vncsrv.py` + noVNC 前端即可，
可直接用于 Armbian/OMV 等系统。

### 12.3 不能直接照搬的部分

| 项 | 原因 |
|---|---|
| NAND 几何参数（PEB / LEB / VID offset） | 与具体颗粒绑定，换颗粒必须重测 |
| 坏块跳过区偏移 | 本机 BBT 不可信，实际坏块位置因机器而异 |
| DTB | 必须匹配目标板的内存、PHY、USB、声卡连线 |
| `8189fs.ko` | vermagic 必须与目标内核完全一致，换内核必须重编 |
| 静态 IP / 串口设备名 | 按实际网络与 COM 口修改 |

## 13. U-Boot 版本升级指南

当前 U-Boot 为 upstream 2026.10 + 本地补丁，封装进原厂 FIP 后由原厂 BL2 加载。
升级到更新版本（如 2027.x）时，按下面的流程把风险压到最低。

### 13.1 铁律：从已验证构建目录派生

```bash
# 正确：从上一版实机验证过的目录复制
cp -a /root/uboot/verify_r80 /root/uboot/verify_r90

# 错误：用源码 defconfig 重新生成配置 —— 会丢失实机修正
make b860av11t_min_defconfig     # 或 b860av11t_net_defconfig
```

这不是理论风险，本机实际踩过：源码 defconfig 写的是 `CONFIG_ENV_IS_NOWHERE=y`，但 r78 实机
`.config` 是 `CONFIG_ENV_IS_IN_UBI=y`；dtb 加载地址也一个是 `0x10000000`、一个是 `0x20000000`。
直接用 defconfig 重建，bootcmd 会变，启动行为跟着变。升级时只替换 U-Boot 源码树，保留
`.config` 与板级补丁，再 `make olddefconfig` 让新增选项取默认值。

### 13.2 升级步骤

| 步骤 | 命令 / 检查点 |
|---|---|
| 1. 备份当前 bootloader | 保存 `boot-diag-r80.img` 与串口启动日志，确认可回刷 |
| 2. 记录当前配置 | 在构建目录（`O=`）内 `cp .config .config.r80.bak`；升级后 `diff` 只允许预期差异 |
| 3. 更新源码树 | 在 `/root/uboot/u-boot` 内 `git fetch && git checkout v2027.xx`，不要 `git clean -xdf` 清掉本地补丁；构建目录 `verify_rXX` 不受影响 |
| 4. 重放本地补丁 | NAND 驱动修复链、env NOWHERE、bootargs、横幅，逐条 `git apply` 并人工核对 |
| 5. olddefconfig | `make olddefconfig`，检查新增 Kconfig 是否默认开启不需要的功能（体积！） |
| 6. 编译 | 生成 `u-boot.bin`，记录大小（此文件本身不是硬约束对象） |
| 7. BL33 加密封装 | `aml_encrypt_gxl --bl3enc --input u-boot.bin`；**加密容器 `bl33.bin.enc` ≤ 524288 B（0x80000）** 是硬上限，建议 ≤ 496KiB 留对齐余量 |
| 8. FIP 重打包 | `repack.py <原厂 GA 镜像> <bootloader.bin> <bl33.enc> <out.img>` |
| 9. 护栏自检 | `bl2 unchanged=True` / `outside=0` / `selfconsistent=True`，任一不过就停止 |
| 10. 内存验证 | `tftpboot 0x01000000 u-boot.bin` + `go 0x01000000` 链式启动，串口确认横幅与新版本号，确认 NAND 读写正常 |
| 11. 落盘 | 仅在第 10 步通过后写 NAND；写前再次确认旧镜像已备份 |

### 13.3 大版本跨越的已知风险点

| 风险 | 表现 | 处理 |
|---|---|---|
| **加密容器超 512KB** | 原厂 BL2 ErrorCHK 校验失败，不开机 | 关视频/Logo/wget 等非必需功能；必要时做两级 U-Boot |
| **FIP header size 未同步** | 改了容器长度但仍按旧大小加载 | 必须同时改 FIP header（`0xC000` 偏移）的 bl33 size 字段 |
| **Kconfig 默认值变化** | 体积暴涨或功能丢失 | 每次 `olddefconfig` 后 diff `.config`，逐项确认 |
| **NAND 驱动 API 变更** | 编译通过但 probe 失败/读写异常 | 回归 §12.1 的 NAND 修复清单：SEED、加扰位、ECC 状态清零、DMA 刷新 |
| **DTS 漂移** | NAND / 网口 / USB 不工作 | 用 `dtc` 反编译比对，确认板级节点仍在 |
| **env 后端被重置** | 又出现 bad CRC / 写 env 卡死 | 确认 `CONFIG_ENV_IS_NOWHERE=y`、`saveenv` 未被编译进来 |

### 13.4 回滚

- 保留一份可用的 `boot-diag-rXX.img`，任何时候可用 USB Burning Tool 回刷
- **回刷时不要勾 Erase flash**，否则会清空 UBI 区，需要重新灌系统
- 回刷后串口确认横幅版本与 UBI 卷可正常读取；userdata/rootfs 卷不受影响

### 13.5 本项目的真实回归记录（版本迭代教训）

| 版本 | 问题 | 根因 |
|---|---|---|
| r37a | probe 后异步 SError（ELR=vsnprintf） | `INIT_LIST_HEAD(&nfc->chips)` 缺失，DM 私有内存未清零，链表头是垃圾 |
| r38 | NAND 完全不工作 | 缺 `NAND_NEED_SCRAMBLING` → 不发 SEED，N2M/M2N 数据泵不启动；DT ECC 须为 40/1024 |
| r41 | 写入不落盘 | BCH 枚举错位：40/1024 行的 bch 字段应为 6（硬件编码），写成 5 导致命令配置错位 |
| r43 | 写命令不完成 | M2N 与 PAGEPROG 之间缺排空：`IDLE×2 + wait_cmd_finish` |
| r44 | 写入仍无效 | PAGEPROG 编码 `0x14100`（低 8 位是 READ0）→ 改为 `0x14010` |
| r50test | BL33 槽位扩容无效 | 只改容器长度，未同步 FIP header 的 size 字段 |
| r51 | 无 fastboot | 配置晚于镜像生成时间，需重编并裁剪体积 |
| r54 | 开机无限重启 | `nandprobe` 括号位置错误，释放了仍在使用的 DMA 缓冲区 |
| r65 | NAND 写入无效 | 写路径移除了加扰位（bit19），数据写不进页 |
| r66 | 修复方向错误 | 试图用“仅去掉加扰位”修复 r65，被实机证伪；正确做法是保留加扰位（带加扰位写入 `ecc=0`） |
| r68/r69 | 大块连续读取随机损坏 | ECC 状态缓冲区未清零，轮询读到上一页残留的完成标志 |
| r80 | env 写入卡死风险 | MLC 上 `saveenv` 触发 NAND 等待超时 → 改 env NOWHERE |

## 14. 源码与许可

| 组件 | 版本 / 来源 | 许可 |
|---|---|---|
| 本仓库脚本与文档 | 本项目原创 | GPL-2.0（见 [LICENSE](LICENSE)） |
| U-Boot（`boot-diag-r80.img`） | upstream 2026.10 + 本机 NAND/env 补丁 | GPL-2.0+ |
| Linux 内核（`kernel-usbhost.new`） | 6.18.0（Lakka AMLGX 构建树）+ `patch_kernel_usb.py` | GPL-2.0 |
| Lakka / LibreELEC rootfs（SYSTEM） | Lakka AMLGX.aarch64 6.1-devel 基线 | GPL-2.0 |
| noVNC 前端 | 1.6.0，[novnc/noVNC](https://github.com/novnc/noVNC) | MPL-2.0 |
| XMB 图标主题 | [libretro/retroarch-assets](https://github.com/libretro/retroarch-assets) | 见上游 |
| Amlogic USB Burning Tool | 第三方，本仓库不分发 | 厂商许可 |

**GPL 合规说明**：`boot-diag-r80.img` 与 `kernel-usbhost.new` 是 GPL 二进制的再分发，对应源码 =
上游发布版本 + 本仓库内的构建脚本与补丁：

- U-Boot：`04-build-scripts/make_r80.sh`、`pack_r80.sh` 记录了源码树、配置派生方式与打包步骤；
  NAND 驱动修复链 r76/r77 的改动见 §5。
- 内核：`04-build-scripts/patch_kernel_usb.py` 为完整补丁（3 处精确替换），其余为 Lakka AMLGX
  6.18.0 构建树原样编译。

需要完整源码快照或对应补丁，请开 Issue 索取。

**安全说明**：Releases 中的 `storage-backup-public.tgz` 已剔除 `storage/iwd/` 与
`storage/.cache/connman/`，不含 WiFi SSID、密码或 PSK 哈希。本仓库所有内容不含任何凭据。
文中地址均为 RFC 5737 文档示例网段（`192.0.2.0/24`），不代表实际部署网络，请按自己的网络替换。

[English](#english)

---

<a id="english"></a>

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
├── README.md                          本文档
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

