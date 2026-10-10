# 序号工具逆向规格（PixPin 3.5.5.1，二进制证据）

> 来源：`MarkSerialConfig`、`SerialIndexDialog`、配置键字符串。
> 状态：行为已确认；视觉类型数量未完全确认（标 ❓）。

## 已确认的行为

### 1. 删除时自动重排
- 配置键 `autoUpdateSerialItemWhenDelteSerialItem`（"Modify other item when deleting serial item"）
- 删除一个序号图元后，后续序号自动前移补位（如删掉 ②，③④→②③）
- 可配置开关

### 2. 起始值
- 配置键 `startFromZero`（"Serial number starts from 0 by default"）
- 可选从 0 或从 1 开始编号

### 3. 全局计数
- 配置键 `globalCounting`（"Global serial numbering"）
- 开启后计数器跨截图会话连续，不按每次截图重置

### 4. 单个序号微调
- `SerialIndexDialog` + `serialIndexIncreaseButton` / `serialIndexDecreaseButton`
- 可选中某个序号，用 +/- 步进调整它的值（不影响其他序号）

### 5. 大小可调
- `serialSizeDialog` + `serialSizeInput`：序号圆圈大小可调

### 6. 颜色
- `SerialOutlineColor` / `outlineColorMode`（"Serial foreground color"）：前景色可配

### 7. 多种视觉类型 ❓
- `SerialType` + `SerialTypeCombox`：下拉框切换类型
- 具体有几种、长什么样，静态未确认（需跑真机看）

## 给实现的要求（SerialTool）

1. 维护计数器：`next_index`，按 `startFromZero` 决定初值
2. 每个序号图元存自己的 `index`
3. 删除图元时：若 `auto_renumber` 开，对所有 index 大于被删者的图元执行 -1
4. 提供 `set_index(item, value)` 接口（对应 +/- 微调对话框）
5. `globalCounting` 开时计数器放 app 级单例，关时放画布级
6. 大小、颜色走 MarkStyle
7. SerialType 先只做圆形数字一种，❓ 部分等真机确认后再补
