# Fast 流程示例

这个文件说明 fast 版每条样本会如何流动。

## 输入

只来自 Phase A 的 A1 context：

- background
- lifestyle_theme
- action_theme
- mood_theme
- slot / slot_time_hint
- weather / season / solar_term

fast 版不使用文生图中间层。

## Fast Phase C 输出

示例结构：

```text
以参考图中的蘑菇TUTU作为唯一角色参考，保持角色外观特征一致，清晨的木质书桌被窗外阳光照亮，轻薄纸片停在桌面边缘。蘑菇TUTU先走到纸片旁，双手抵住纸张边缘向前推动，接着一阵风把纸片吹得翘起，它立刻弯腰压住纸片，眼神变得专注又倔强。随后它重新调整姿势，把纸片一步步推向另一张纸旁边。镜头保持稳定自然观察，随着它的移动平滑跟随，最后蘑菇TUTU停在整理好的纸片旁，露出满意的表情。
```

## Fast Phase D Payload

Fast Phase D 会提交：

- 一段文生视频 prompt。
- 一张角色参考图。

不会提交：

- 文生图中间层。
- 首帧图。
- Replicate 图片 URL。

这条路线的核心验证点是：只靠 A1 context、文字 prompt 和角色参考图，Seedance 是否能稳定生成 TUTU 的视频。
