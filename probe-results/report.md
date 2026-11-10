# 元素吸附诊断报告（Windows 真机）

分析日期：2026-11-10
脚本：`tools/probe_snap_apps.py`（分支 `claude/project-thread-83w7rl`，提交 `7a8ac7c`），仅标准库，PerMonitorV2 DPI 感知，坐标为物理像素。

## 1. 环境

- Windows 11 企业版 10.0.26100
- 显示器：2 台
  - 主屏 2560×1440，缩放 100%（所有探测窗口都在主屏最大化）
  - 副屏（物理 1536×960），缩放 125%
- Office：Microsoft 365 Apps for enterprise（ClickToRun `O365ProPlusRetail`）16.0.20430.20180
- 程序版本说明：Excel/Word/PowerPoint/Outlook 为上述经典桌面版；Teams 为新版 `ms-teams.exe`（MSTeams Appx）；Zoom Workplace 已安装并运行；新版 Outlook（`olk.exe`）**未安装**。

## 2. 方法与偏差

- 每个程序窗口最大化并置于前台后，按任务给定网格取点；被其他窗口挡住的点由脚本跳过。
- 同进程的其他窗口处理：
  - Excel：真实工作簿窗口在采样期间被最小化，采样结束后恢复；只有新建空白工作簿被采样。
  - Edge：另有两个真实标签页窗口，被新开的表格页窗口完全覆盖，未产生采样点（汇总中的两条“没有可测的点”即这两个窗口）。
- Excel 合并单元格：任务示例 `B3:C3` 只是举例。为了让固定网格的采样点恰好落在合并区域内，实际合并的是 `B5:D5`（约 207×19 物理像素）。合并通过 COM 执行并关闭了 `DisplayAlerts`，没有弹出“仅保留左上角的值”确认框。
- 隐私：未打开任何真实文档/邮件/聊天；Excel、Word、PowerPoint 均为新建空白文件，内容为自行编造的假数据；脚本不记录元素名称、值或文字，只记录控件类型、类名、矩形和耗时。

## 3. 总览

| 程序 | 点数 | MSAA 最小元素面积中位 | UIA 最小元素面积中位 | 哪个更细 |
|---|---|---|---|---|
| Excel | 384 | 2,880,000（整个网格 client） | 1,368（单个单元格） | UIA |
| Word | 160 | 3,026,170（文档 client） | 2,253,334（正文 Edit / 表格 DataItem） | UIA |
| PowerPoint | 160 | 3,046,400（幻灯片窗格） | 3,046,400（幻灯片窗格） | 都只能到窗格 |
| Outlook（经典） | 160 | 2,222,883 | 2,222,883 | 相当（最内层同为阅读窗格） |
| Teams | 160 | 486,989 | 2,573,217 | MSAA |
| Zoom | 160 | 3,581,440（整窗，MSAA 调用失败） | 98,240（列表项） | UIA |
| Edge | 160 | 444,912 | 3,308,424 | 分场景：最细元素 UIA 更细（超链接 20×37）；每点最小面积中位 MSAA 更小 |

## 4. 各程序

### 4.1 Excel

终端汇总原文：

```
excel.exe [XLMAIN] 点数 384
  MSAA：命中中位 2.0ms，补父链中位 4.7ms，平均层数 6.1+8.1，最小元素面积中位 2880000.0
  UIA ：命中中位 3.4ms，含父链中位 14.6ms，平均层数 7.0，最小元素面积中位 1368.0
  UIA 最内层控件类型：{'Button': 10, 'TitleBar': 18, 'MenuItem': 8, 'Group': 8, 'ListItem': 5, 'Image': 1, 'Pane': 3, 'Edit': 22, 'DataItem': 288, 'TabItem': 1, 'Tab': 12, '50014': 8}
```

数据：`excel.jsonl`

- 鼠标在单元格上时，MSAA 最内层是 `client`，矩形 `[0, 245, 2560, 1125]`（2,880,000 px²），即整个网格客户区；所有单元格点都停在这一层，MSAA 取不到单元格。
- UIA 最内层是 `DataItem`（类名 `XLSpreadsheetCell`），中位 `72×19`（部分列宽 69），即单个单元格；288/384 个点落到 `DataItem`。
- 合并单元格（实测 `B5:D5`，合并区域约 207×19）：采样点 `(153, 300)` 位于合并区域内，UIA 返回的 `DataItem` 矩形是 `[98, 283, 69, 19]`，即左上单元格 `B5` 的大小，**不是**合并后的 207 宽。结论：Excel 的 UIA 对合并单元格只给出锚点单元格矩形。
- 耗时：MSAA 命中中位 2.0ms，UIA 含父链中位 14.6ms，无超过 300ms 的点。

### 4.2 Word

终端汇总原文：

```
winword.exe [OpusApp] 点数 160
  MSAA：命中中位 1.8ms，补父链中位 1.5ms，平均层数 7.5+9.5，最小元素面积中位 3026170.0
  UIA ：命中中位 4.5ms，含父链中位 118.7ms，平均层数 5.3，最小元素面积中位 2253334.5
  UIA 最内层控件类型：{'TabItem': 4, 'Tab': 11, 'MenuItem': 1, 'Document': 80, 'Custom': 8, 'Edit': 44, 'DataItem': 12}
```

数据：`word.jsonl`

- UIA 更细：正文点最内层是 `Edit`（1273×1009），表格点能到 `DataItem`（178×56）。
- MSAA 平均层数虽多（7.5+9.5），但表格与正文点上最内层都停在 `client`（`[0, 180, 2543, 1190]`，3,026,170 px²），取不到表格单元和文字。
- 耗时：UIA 含父链中位 118.7ms，2/160 个点超过 300ms（最大 310.1ms）。

### 4.3 PowerPoint

终端汇总原文：

```
powerpnt.exe [PPTFrameClass] 点数 160
  MSAA：命中中位 173.6ms，补父链中位 142.3ms，平均层数 5.8+7.8，最小元素面积中位 3046400.0
  UIA ：命中中位 269.7ms，含父链中位 797.6ms，平均层数 3.5，最小元素面积中位 3046400.0
  UIA 最内层控件类型：{'TabItem': 5, 'Tab': 9, 'Button': 1, 'MenuItem': 1, 'Pane': 144}
```

数据：`powerpoint.jsonl`

- 两者都只能到 `Pane`（3,046,400 px²，即整个幻灯片窗格）；UIA 144/160 个点停在 `Pane`，MSAA 同样拿不到幻灯片里的文本框和表格。
- 所有程序里最慢：UIA 含父链 160/160 个点超过 300ms，最大 1897.9ms；MSAA 单次命中 16/160 个点超过 300ms，最大 1237.8ms。

### 4.4 Outlook（经典版）

终端汇总原文：

```
outlook.exe [rctrl_renwnd32] 点数 160
  MSAA：命中中位 8.6ms，补父链中位 12.0ms，平均层数 10.9+14.1，最小元素面积中位 2222883.0
  UIA ：命中中位 4.2ms，含父链中位 26.4ms，平均层数 8.6，最小元素面积中位 2222883.0
  UIA 最内层控件类型：{'Button': 13, 'TabItem': 2, 'Tab': 13, 'Pane': 13, 'DataItem': 7, 'Edit': 94, 'Custom': 7, 'Image': 10, 'Group': 1}
```

数据：`outlook.jsonl`

- MSAA 与 UIA 的最小元素面积中位相同（2,222,883）；最内层通常同为阅读窗格级 `Edit`/`client`（`[417, 316, 2111, 1053]`），两者都能到按钮、标签页、列表项，但没有哪一方明显更细。
- 16 个点（两列 x=233/394，y=344…1329）的 MSAA `accHitTest` 返回 `HRESULT 0x80004001`（E_NOTIMPL），见第 5 节。
- 耗时：无超过 300ms 的点。

### 4.5 Teams（新版）

终端汇总原文：

```
ms-teams.exe [TeamsWebView] 点数 160
  MSAA：命中中位 5.1ms，补父链中位 138.4ms，平均层数 6.2+20.8，最小元素面积中位 486989.0
  UIA ：命中中位 3.4ms，含父链中位 26.6ms，平均层数 18.3，最小元素面积中位 2573217.0
  UIA 最内层控件类型：{'Document': 1, 'Group': 136, 'Button': 3, 'Text': 4, 'TreeItem': 14, 'Image': 2}
```

数据：`teams.jsonl`

- MSAA 更细：每点最小元素面积中位 486,989（能到聊天列表等局部区域）；UIA 大多停在整页 `Group`（136/160）。
- 但 MSAA 补父链慢：中位 138.4ms，5/160 个点超过 300ms（最大 417.0ms）。

### 4.6 Zoom

终端汇总原文：

```
zoom.exe [ZPPTMainFrmWndClassEx] 点数 160
  MSAA：命中中位 1.9ms，补父链中位 2.1ms，平均层数 2+3，最小元素面积中位 3581440.0
  UIA ：命中中位 2.8ms，含父链中位 30.1ms，平均层数 20.3，最小元素面积中位 98240.0
  UIA 最内层控件类型：{'TabItem': 1, 'Pane': 29, 'SplitButton': 1, 'List': 50, 'ListItem': 61, 'Group': 11, 'Text': 3, 'Image': 4}
```

数据：`zoom.jsonl`

- UIA 明显更细：能到 `ListItem`/`List`/`Text`（最小面积中位 98,240）。
- MSAA 在所有 160 个点上，第一层取到 `window` 后，第二层 `accHitTest` 就返回 `HRESULT 0x80070057`（E_INVALIDARG），等价于只能拿整个窗口。见第 5 节。
- 耗时：无超过 300ms 的点。

### 4.7 Edge（对照组）

终端汇总原文：

```
msedge.exe [Chrome_WidgetWin_1] 点数 160
  MSAA：命中中位 5.7ms，补父链中位 28.0ms，平均层数 2.0+8.2，最小元素面积中位 444912.0
  UIA ：命中中位 5.0ms，含父链中位 29.5ms，平均层数 13.8，最小元素面积中位 3308424.0
  UIA 最内层控件类型：{'ToolBar': 2, 'Edit': 13, 'Button': 1, 'Group': 105, 'List': 11, 'Hyperlink': 20, 'Text': 7, 'ListItem': 1}
msedge.exe [Chrome_WidgetWin_1]：没有可测的点（窗口被挡住？）
msedge.exe [Chrome_WidgetWin_1]：没有可测的点（窗口被挡住？）
```

数据：`edge.jsonl`（页面：`https://www.runoob.com/html/html-tables.html`，维基百科在办公网不可达）

- UIA 的**最细元素**更细：20 个点能到 `Hyperlink`（示例 20×37），另有 `ListItem`、`Text`；MSAA 最深通常到 `List`（示例 1409×37）或在容器 `pane` 停住。
- 但按“每点最小元素面积中位”，MSAA 更小（444,912 vs 3,308,424）：因为 105 个落在页面空白处的点，UIA 最内层是整页 `Group`（示例 `[42, 90, 2499, 5956]`），面积巨大。
- 结论：网页场景 MSAA 到容器级就停，UIA 能到具体链接/文本，但空白区域 UIA 的“最内层”可能是整页容器。按需选区时两者要配合。
- 耗时：无超过 300ms 的点。

## 5. 报错原文

脚本没有崩溃，所有运行退出码为 0；以下是脚本按点捕获的 HRESULT：

- Zoom（160/160 个点）：

```
["error", "HRESULT 0x80070057"]
```

  出现位置：MSAA 命中链 `["window", ...]` 之后的下一层 `accHitTest`。

- Outlook（16/160 个点，x=233/394 两列，y=344…1329）：

```
["error", "HRESULT 0x80004001"]
```

  出现位置：MSAA 命中链在 `["window", [106, 235, 300, 1135]]` 之后的 `accHitTest`。

## 6. 超过 300ms 的单次调用

| 程序 | MSAA 命中 | MSAA 补父链 | UIA 命中 | UIA 含父链 | 最大单次 |
|---|---|---|---|---|---|
| PowerPoint | 16/160 | 16/160 | 7/160 | 160/160 | UIA 1897.9ms |
| Teams | 0 | 5/160 | 0 | 0 | MSAA 父链 417.0ms |
| Word | 0 | 0 | 0 | 2/160 | UIA 含父链 310.1ms |
| Excel / Outlook / Zoom / Edge | 0 | 0 | 0 | 0 | ≤ 278.0ms（Edge MSAA 父链） |

## 7. 文件清单

- `excel.jsonl` / `word.jsonl` / `powerpoint.jsonl` / `outlook.jsonl` / `teams.jsonl` / `zoom.jsonl` / `edge.jsonl`
- `<程序>.summary.txt`：脚本终端汇总原文（GBK 编码）
- `report.md`：本报告
