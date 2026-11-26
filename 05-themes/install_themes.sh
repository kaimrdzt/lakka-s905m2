#!/bin/sh
# 一键安装主题到 /storage/assets/xmb/（copy-up 持久）
# 用法：把主题文件夹放到本脚本同目录，sh install_themes.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p /storage/assets/xmb
n=0
for d in "$DIR"/*/; do
  name=$(basename "$d")
  [ "$name" = "monochrome" ] && continue   # 当前默认主题已在用
  rm -rf "/storage/assets/xmb/$name"
  cp -r "$d" "/storage/assets/xmb/$name"
  n=$((n+1))
  echo "已安装: $name"
done
sync
echo "共安装 $n 个主题。切换方法："
echo "  RetroArch 菜单 → 设置 → 用户界面 → 外观 → 菜单图标主题"
echo "（切换即时生效，无需重启）"
