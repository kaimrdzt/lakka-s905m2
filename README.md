# B860AV1.1-T 定制 Lakka 系统 — 完整移植工程

> 生成：2026-11-26（首版 11-25，主题/颜色修复/720p 更新至 11-26）。设备：ZTE B860AV1.1-T（Amlogic S905M2-B / GXL，1GB DDR3，8GiB MLC NAND）。
> 本项目是当前盒子上运行固件的**完整可复现交付**：刷机脚本、编译脚本、配置、全部避坑记录。

**大体积二进制不入库**（UBI 镜像 974MB、bootloader 110MB、内核、脱敏 `/storage` 备份）请到
[Releases](../../releases/latest) 下载。本仓库只跟踪脚本、配置与文档，克隆体积 < 200KB。

> 本仓库发布的是**已验证可复现的移植产物**，不是通用发行版。刷机有变砖风险，请先读完全部
> 「避坑指南」（§5）再动手。

---

## 1. 系统概览

### 1.1 启动链

```
BL1(ROM) → BL2(原厂 2018) → BL30/BL31(原厂) → BL33 = U-Boot 2026.10 diag-r80（自编）
  → ubi part → 读 kernel/dtb 卷 → booti → Linux 6.18.0（Lakka rootfs）
```

### 1.2 NAND 物理布局（8GiB = 4096 PEB × 2MiB）

| 区域 | 范围 | 说明 |
|---|---|---|
| nand-boot（原厂引导区） | 0x0 – 0x800000 | BL2/BL33 等，UBT 刷机时更新 |
| **跳过区** | 0x800000 – 0xB800000 | 原厂 nand_key(PEB10-13) + 真坏块 90/91，**永不使用** |
| **nand-ubi（UBI 数据区）** | **0xB800000 (184MiB) – 8GiB** | 4004 good PEB，坏块 0 |

### 1.3 UBI 卷表（r79 镜像，attach 后 6 卷）

| vol | 名称 | 类型 | 大小 | 内容 |
|---|---|---|---|---|
| 0 | rootfs | static | **1GiB**（数据 922MiB，预留升级余量） | Lakka SYSTEM squashfs（只读挂载） |
| 1 | kernel | static | 29.3MiB | Linux 6.18.0 arm64 Image |
| 2 | dtb | static | 30KB | 补丁版 DTB（**蓝牙节点已禁用**） |
| 3 | userdata | dynamic | ~6.6GiB | UBIFS → /storage（所有可写内容） |
| 4/5 | uboot_env / uboot_env_r | dynamic | 8KB×2 | 空置（env 已改 NOWHERE，不使用） |

### 1.4 U-Boot r80 特性（相对 r78）

- **PREBOOT 移除**：开机不再有 `splash/bmp/already selected` 三行噪声
- **env = NOWHERE**：环境变量编译进二进制；`Loading Environment from nowhere... OK` 瞬时完成，无 bad CRC，**永不写 NAND env**（该板 MLC 上写 env 有卡死史）
- bootargs/bootcmd/IP 全部编译期固定；`saveenv` 命令不编译
- mtdparts：`nand0:0x800000@0(nand-boot),-@0xb800000(nand-ubi)`
- 网络（wget/ping）与 NAND 救援命令保留

### 1.5 /storage 可写层（全部配置的所在，升级系统不丢）

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
| `iwd/SFT_xXx.psk` | WiFi 凭据（iwd 持久化到 /storage/iwd） |
| `assets/xmb/monochrome/png/bg.png` + `assets/branding/logo.png` | 自定义开机画面 |

---

## 2. 刷机使用说明（给新盒子/救砖）

### 2.1 前置条件

- Windows PC：以太网口静态 IP `172.16.30.10/16`；COM14 串口线（USB-CH340，115200）
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
scp storage-backup-public.tgz root@172.16.30.100:/tmp/
# 盒子上：
cd / && tar xzf /tmp/storage-backup-public.tgz
sync && reboot
```

> 公开版备份已剔除 `storage/iwd/` 与 `storage/.cache/connman/`，**不含任何 WiFi 凭据**。
> 恢复后需自行重新连接 WiFi：`iwctl station wlan0 connect <你的SSID>`。

### 2.5 最终验证清单

```sh
lsmod | grep 8189fs                        # 驱动已加载
iwctl station wlan0 show                   # State=connected SFT_xXx + IPv4
ip -4 addr show wlan0                      # 192.168.9.14/23
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
- 产物头部自检：每 PEB EC=0x55424923 / VID=0x55424921

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
lakka-b860av11t\
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

U 盘实测：usb 1-1 "Disk 2.0" (346d:5678) → usb-storage → sda (8.05GB) → sda1 (vfat) → 自动挂载 /storage/roms/sda1-usb-VendorCo_Product。ROM 通过 SMB（\\172.16.30.100\storage\roms\）或网络拷入。

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

    http://172.16.30.100:6080/vnc_lite.html

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

## 12. 源码与许可

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
文中出现的内网地址（`172.16.30.x`、`192.168.9.x`）均为示例拓扑，请按自己的网络替换。