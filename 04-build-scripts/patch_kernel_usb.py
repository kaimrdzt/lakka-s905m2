#!/usr/bin/env python3
# dwc3-meson-g12a.c 补丁：dwc2 子节点禁用时，GXL OTG PHY 不再强制设备模式启动
import sys

F = '/mnt/e/Lakka/build.Lakka-AMLGX.aarch64/build/linux-7d0a66e4bb9081d75c82ec4957c50034cb0ea449/drivers/usb/dwc3/dwc3-meson-g12a.c'
src = open(F).read()

def rep(old, new, tag):
    global src
    assert src.count(old) == 1, f"{tag}: 目标出现 {src.count(old)} 次（应为 1）"
    src = src.replace(old, new)
    print(f"OK  {tag}")

# 1) struct 加 dwc2_en
rep("""	const struct dwc3_meson_g12a_drvdata *drvdata;
};""",
"""	const struct dwc3_meson_g12a_drvdata *drvdata;
	/* B860: dwc2 子节点禁用时为 false，OTG PHY 允许以主机模式启动 */
	bool			dwc2_en;
};""", "1-struct-dwc2_en")

# 2) probe 读取 dwc2 子节点状态（在 usb_init 之前）
rep("""	/* Get dr_mode */
	priv->otg_mode = usb_get_dr_mode(dev);
""",
"""	/* Get dr_mode */
	priv->otg_mode = usb_get_dr_mode(dev);

	/* B860: OTG 口主机模式 —— dwc2 子节点被禁用时，
	 * OTG PHY 不再被强制成设备模式（原版 quirk 会压住主机口） */
	{
		struct device_node *dwc2_np = of_get_child_by_name(np, "usb@c9100000");
		priv->dwc2_en = dwc2_np && of_device_is_available(dwc2_np);
		of_node_put(dwc2_np);
	}
""", "2-probe-dwc2_en")

# 3) GXL quirk 按补丁开关
rep("""	/* On GXL PHY must be started in device mode for DWC2 init */
	return priv->drvdata->set_phy_mode(priv, i,
				(i == USB2_OTG_PHY) ? PHY_MODE_USB_DEVICE
						    : PHY_MODE_USB_HOST);
}""",
"""	/* On GXL PHY must be started in device mode for DWC2 init.
	 * B860: dwc2 子节点禁用时（OTG 口转纯主机）跳过该 quirk。 */
	return priv->drvdata->set_phy_mode(priv, i,
				(i == USB2_OTG_PHY && priv->dwc2_en) ? PHY_MODE_USB_DEVICE
						    : PHY_MODE_USB_HOST);
}""", "3-gxl-quirk-conditional")

open(F, 'w').write(src)
print("补丁写入完成")
