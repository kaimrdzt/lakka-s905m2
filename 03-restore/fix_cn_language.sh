#!/bin/sh
# 修复：恢复被 RA 退出回写覆盖的中文语言设置（先停服务再改）
set -e
CFG=/storage/.config/retroarch/retroarch.cfg
FONT=/storage/assets/xmb/monochrome/font.ttf

echo "== [1] 停止 RetroArch =="
systemctl stop retroarch.service 2>/dev/null || true
sleep 3

echo "== [2] 补齐 Ozone 字体（regular/bold）=="
mkdir -p /storage/assets/ozone
cp -f "$FONT" /storage/assets/ozone/regular.ttf
cp -f "$FONT" /storage/assets/ozone/bold.ttf
ls -l /storage/assets/ozone/regular.ttf /storage/assets/ozone/bold.ttf

echo "== [3] 设置 user_language=12 + menu_driver=xmb =="
if grep -q '^user_language' "$CFG"; then
  sed -i 's/^user_language = ".*"/user_language = "12"/' "$CFG"
else
  echo 'user_language = "12"' >> "$CFG"
fi
if grep -q '^menu_driver' "$CFG"; then
  sed -i 's/^menu_driver = ".*"/menu_driver = "xmb"/' "$CFG"
else
  echo 'menu_driver = "xmb"' >> "$CFG"
fi
grep -nE '^(user_language|menu_driver)' "$CFG"

echo "== [4] 启动 RetroArch =="
systemctl start retroarch.service
sleep 8
echo -n "retroarch: "; systemctl is-active retroarch.service

echo "== [5] 启动后复核（确认没被回滚）=="
grep -nE '^(user_language|menu_driver)' "$CFG"
echo "完成。看屏幕确认中文菜单。"
