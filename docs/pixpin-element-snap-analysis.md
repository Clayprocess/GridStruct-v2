# PixPin 元素吸附实现分析（供 GridStruct 开发参考）

> 分析日期：2026-11-09
> 分析对象（本机安装副本，闭源）
>
> | 文件 | SHA-256 | 大小 |
> |---|---|---|
> | `PixPin.exe` | `fb051a6b1ebc7ac62679eae30b068caa1f48d00d205cd59c4108f73af160bb5a` | 6,849,480 B |
> | `UiRegionDetector.dll` | `015f4e4755847719361fc9f92f69d4c1bf568cd8367ca7c27e1ada08ada44139` | 102,200 B |
> | `PixWindowNotify.dll` | `22f53905b2b18aaa28bab17c9af4aebbd03c0ef78f81e2244263323a3136ed65` | 39,736 B |
>
> 方法：REA 6.8.0 + Ghidra 12.1.4 headless 反编译，**未运行被测程序**。
> 证据 ID：`ev_157bad5115209b86d1ac1dc6f4fa47894140f95305e8c54580c0084969949d50`（DLL 全量反编译清单）、`ev_0322389a7e763b7c5b535ebc49024b6a05ca6904d9574671079429072258ca4d`（binary overview）、`ev_10ba7d6b3e653ec43e9e825b90dfe9ed6919f2627138117373bea834c77339b0`（DirectGetRect 反编译）、`ev_b59fc5df28d8f98863af0a80584298076786136932c2604d2e9a13395ef9ca13`（PixPin.exe 调用层地址范围反编译）、`ev_83ff39cb22d29f4b0992871cfff84e01036236903f25bcfcb9a83f77d2fe1871`（PixWindowNotify.dll 全量反编译）。
>
> 边界：伪代码来自 Ghidra，不是原始源码；DLL 引用 PDB `UiRegionDetector.pdb` 但本机没有，函数名仅能依赖导出符号、RTTI 和日志字符串，其余为 `FUN_xxxx`。文中"实测"数字是在本机真实窗口上测得的本项目同源调用，不是 PixPin 内部计时。

## 1. 结论速览

1. 元素吸附的**引擎全部在 `UiRegionDetector.dll`**，`PixPin.exe` 只负责创建线程、投递请求、接收 `sigNewRectDetected` 并绘制高亮。
2. 数据结构是"**窗口快照表 → 每个窗口一个元素包装器**"：`WinRect{hwnd, rect, state, wrapper, processName, className, alive, expiry}`。
3. 元素包装器有三种实现：
   - `CarpUIElementUIA`：跨进程 `IAccessible` 树逐层点命中（主路径）；
   - `CarpUIElementHWND`：只返回窗口矩形（降级路径）；
   - `CarpUIElementSnapshot`：菜单窗口（`#32768`）及浏览器弹窗的菜单项矩形快照（补充命中目标）。
4. 请求在独立 QThread 中串行执行，入队时**合并队尾连续的同操作码请求**（保留最新一条）；每条请求后 `msleep(1)`。
5. 关键阈值：点命中深度 **30 层**；慢查询降级 **301 ms**；窗口级回退缓存 **1000 ms**；跨进程探活 `WM_NULL` **50 ms**；快照菜单树预算 **200 ms / 512 节点 / 11 层递归**；调用层首帧等待上限 **1000 ms**。
6. 大量**按进程/窗口类的特判**：59 个浏览器进程名（结果二次命中取更小矩形）、7 个特殊进程名（Unity/Epic/Mybase/Snap.Hutao/GoldenDict/openshot-qt/qgis-bin，统一强制窗口级 state=2，不做元素点命中）、桌面类窗口返回整屏、`yong_input` 类强制窗口级。
7. 性能上与本项目同源：单次跨进程点命中 2–4 ms，首次取 `IAccessible` 6–18 ms；差异主要在覆盖率与缓存策略，不在语言或 provider 速度。

## 2. 组件与职责

```
PixPin.exe (Qt5)
  └─ TrigerGraphicRectSelectItem          截图选区控制器（UI 线程）
       ├─ initUiRegionDetector()          创建 UiRegionDetectorThreadWarp 并等首个结果
       ├─ getUiRegionDetectorRect()       status==2 时 resetCache 后同步请求
       └─ sigNewRectDetected(rect, pt, seq, queueMs, procMs, …)

UiRegionDetector.dll
  ├─ UiRegionDetectorThreadWarp (QThread, QObject)
  │    ├─ run()                            COM MTA + 请求循环
  │    ├─ AsyncGetWinRectByPoint / Parent / Child
  │    ├─ SyncGetWinRectByPoint           opcode 4，同步等待
  │    ├─ getFirstRect()                  读"已产生首个结果"缓存，不阻塞
  │    └─ resetCache / setIsOnlyWindow / StopAndDeleteLater
  ├─ UiRegionDetector                      窗口快照 + 命中 + 层级 + 缓存
  │    ├─ SnapshotAllWinRect()             EnumDesktopWindows 一次性快照
  │    ├─ WinGetWinRectByPoint()           点命中入口（含 only-window 判断）
  │    ├─ WinGetWinRectByPointUIA()        元素包装器命中 + 慢查询降级
  │    ├─ WinGetParantWinRect / Child      滚轮层级切换
  │    ├─ fallbackToHwnd                   窗口级回退（1000 ms）
  │    ├─ checkWinRectAlive                窗口移动/销毁检测
  │    └─ DirectGetRect                    静态直取（UIA + WindowFromPoint）
  └─ CarpUIElement 家族
       ├─ CarpUIElementUIA                  IAccessible 树
       ├─ CarpUIElementHWND                 窗口矩形
       └─ CarpUIElementSnapshot             菜单项矩形快照

PixWindowNotify.dll（符号完整，138 个函数）
  └─ SetWinEventHook(EVENT_SYSTEM_FOREGROUND / EVENT_SYSTEM_MINIMIZEEND)
       + QTimer：提供 activeWindowId / activeProcessName / activeProcessPath
       + refreshActiveWindow：查前台窗口、PID、进程全路径/文件名并广播
```

## 3. 启动与快照

### 3.1 线程启动

`UiRegionDetectorThreadWarp::run`（`0x180001ea0`）：

1. `CoInitializeEx(NULL, COINIT_MULTITHREADED)`；
2. `new UiRegionDetector(&params)`——**构造函数内直接调用 `SnapshotAllWinRect()`**；
3. 循环：加锁取队列第一个请求 → 解锁 → 按 opcode 执行 → 写完成标志 → `QThread::msleep(1)`。

请求队列的操作码：

| opcode | 动作 |
|---|---|
| 1 | 点命中（`WinGetWinRectByPoint`） |
| 2 | 父元素（`WinGetParantWinRect`） |
| 3 | 子元素（`WinGetChildWinRect`） |
| 4 | 同步点命中（`SyncGetWinRectByPoint`，带等待） |
| 5 | 窗口标题 |
| 6 | 进程名/类名 |
| 7 | `setIsOnlyWindow` |
| 8 | `resetCache` |

入队函数 `FUN_180001700` 在追加前会**从队尾连续弹出同 opcode 的旧请求**（只合并相邻尾部，不是全局 latest-wins），并记录序号与入队时间；信号 `sigNewRectDetected(QRect, QPoint, quint64 seq, qint64 queueMs, qint64 procMs, …)` 带出排队与处理耗时，用于内部诊断。

### 3.2 快照前的 Chromium 唤醒

`SnapshotAllWinRect` 开头调用 `FUN_180006e40`：

- 从 `PixWindowNotify` 取**前台窗口**与进程名；
- 若进程名命中浏览器名单，调用
  `AccessibleObjectFromWindow(hwnd, OBJID_CLIENT=0xFFFFFFFC, IID_IAccessible)`，并对根对象调用 `get_accName`（vtable +0x50）；
- 日志失败分支：`Failed to get IAccessible for Chrome window:`；
- 结果按 hwnd 缓存在 `QHash`，上限 **32** 条。

目的：Chromium 系程序只有在收到无障碍查询后才开启完整无障碍树，先"戳"一下可以降低首次点命中的空结果率。

### 3.3 窗口枚举与过滤

`SnapshotAllWinRect`（`0x1800070c0`）→ `EnumDesktopWindows(NULL, callback, this+0x38)`，回调 `FUN_180006490`：

1. 同 hwnd 去重（参数列表扫描）；
2. `GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TRANSPARENT(0x20)` → 跳过；
3. `IsWindowVisible` 为假 → 跳过；
4. `DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED(14), …)` 非零 → 跳过（UWP 幽灵窗口）；
5. 类名在**黑名单 QStringList** 中 → 跳过（名单内容运行时构造，静态未解析）；
6. 矩形：`DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS(9))`，无效则 `GetWindowRect`；转成**闭区间**（right-1, bottom-1）；与屏幕区域求交；
7. 记录：hwnd、rect、默认 state=1、类名（`GetClassNameW`）、进程名（`OpenProcess(0x410)` + `K32GetModuleBaseNameW`，按 hwnd 缓存）；
8. 若 `process == explorer.exe` 且 `class ∈ {Progman, WorkerW, WorkerA}` → 标记"桌面"，命中时返回**整屏 `availableGeometry`**；
9. 类名 `yong_input` → 直接进入 HWND-only 状态（state=2）。

快照后第二次遍历：对 class `#32768` 的窗口，以及"进程名∈浏览器名单 且 `style & 0x80c00000 == 0x80000000`"的弹窗，构建 `CarpUIElementSnapshot`（菜单项矩形）并标记 state=3（免探测、免 COM 点命中）。

- 本进程窗口直接构建；
- 跨进程窗口先过 `IsHungAppWindow` + `SendMessageTimeoutW(WM_NULL, 50ms)`，**响应通过后同样构建快照**；探测失败则保持 state=1，退回普通点命中。
- 因此"浏览器弹窗"二次遍历在跨进程时也有一次最多 50 ms 的同步探测成本。

> 对照本项目：窗口过滤（不可见/透明/挂起/DWM 边框）与我们一致；差异是 PixPin 额外做了进程名缓存、Chromium 唤醒、菜单矩形快照和桌面特判。

### 3.4 前台窗口通知（PixWindowNotify.dll）

快照唤醒依赖这个 DLL（`UiRegionDetector.dll` 直接导入 `PixWindowNotify::instance / activeWindowId / activeProcessName`）：

- 安装两个 out-of-context 钩子：`EVENT_SYSTEM_FOREGROUND(0x3)`、`EVENT_SYSTEM_MINIMIZEEND(0x17)`；
- 钩子回调只把事件 hwnd 写进实例字段并 `QTimer::start(1ms)`，把真正的查询推迟到消息循环（避免在钩子回调里做跨进程调用）；
- 刷新时：`GetForegroundWindow` → `GetWindowThreadProcessId` → `OpenProcess(0x410)` + `K32GetModuleFileNameExW` 取全路径 → 按 `\` 切出文件名；变化时发 `activeWindowChanged / activeProcessChanged / sigWindowStateChanged`；
- `setEnhancePinTopmost` 启用后另有一个 200 ms 定时器，用于置顶增强，与元素吸附无直接关系。

## 4. 点命中主路径

`UiRegionDetector::WinGetWinRectByPoint`（`0x180008030`）：

1. 在快照表里按 rect 找第一个包含鼠标点的窗口（**线性扫描**）；
2. `isOnlyWindow == true` → 直接返回窗口矩形；
3. `checkWinRectAlive(entry)`：
   - state==3（本进程快照）→ 直接 true；
   - 否则 `IsWindow` + 重新取窗口矩形，判断**旧缓存 rect 是否仍被包含**（窗口被移动/缩放即失效）；
   - 失效时释放该窗口的元素包装器；
4. 调用 `WinGetWinRectByPointUIA`。

`WinGetWinRectByPointUIA`（`0x1800078e0`）关键流程（伪代码转写）：

```c
if (cached_level_rect_contains(point))            // 上一层的父/子缓存仍有效
    return cached_level_rect;

if (entry->expiry > GetTickCount64())             // 窗口级回退缓存（+1000ms）
    return entry->rect;

t0 = GetTickCount64();
wrapper->getRectAtPoint(point, &result);          // vtable +0x10，走 IAccessible
t1 = GetTickCount64();

if ((entry->state != 1 || t1 - t0 < 301) && result != NULL) {
    if (entry->state == 1 && is_browser(entry->processName)) {
        wrapper->getRectAtPoint(point, &second);  // 二次命中
        if (second_contains_point && second_is_smaller)
            result = second;                      // Chromium 首次常返回大容器
    }
    cache_wrapper(result);
    rect = result ∩ entry->rect;
    if (valid(rect)) return rect;
}
return fallbackToHwnd(entry);                     // 设 expiry=now+1000，用 CarpUIElementHWND
```

要点：

- **301 ms** 只对普通窗口（state==1）生效；桌面/本进程快照不受限。
- 浏览器二次命中是"**同一进程名命中 59 个名单之一** → 再查一次 → 取更小且含点的矩形"。
- 命中失败或超时 → `fallbackToHwnd`：创建 `CarpUIElementHWND` 包装器并缓存 **1000 ms**，期间只返回窗口矩形。
- 缓存位置：`this+0x50`（包装器）、`this+0x40`（当前 hwnd）、`this+0x60/0x64`（层级缓存 rect）。

### 4.1 IAccessible 逐层命中

`CarpUIElementUIA` 构造时 `AccessibleObjectFromWindow(hwnd, OBJID_WINDOW=0, IID_IAccessible)`，命中函数 `FUN_180004da0`：

- 最多 **30** 次循环（`while (i < 0x1e)`）；
- 每层 `accHitTest(x, y)`（vtable +0xC0 = 索引 24）；
- 返回 `VT_DISPATCH` → `QueryInterface(IAccessible)` 后下降一层；
- 返回 `VT_I4` → 用 `accLocation(childId)` 取矩形，构造叶子包装器；
- 叶子矩形不含点 → 调用包装器 `childAtPoint`（vtable +0x18）再细化；
- 任一层失败 → 日志 `[CarpUIElementUIA::elementFromPoint] Accessible hit test failed`，返回空。

与本项目 `_Accessibility.hit_path` 的差异：PixPin 是"边下降边建包装器、最后取叶子"，本项目是"一次拿完整路径 + 自行去重排序"；深度上限 30 vs 31，实际等价。

### 4.2 菜单矩形快照

`CarpUIElementSnapshot::elementFromHWND`（`0x180003b50`）+ 递归 `FUN_1800034b0`：

- `AccessibleObjectFromWindow(hwnd, OBJID_CLIENT=0xFFFFFFFC)`；
- 递归 `AccessibleChildren`：
  - 深度上限 **11 层递归**（入口深度从 1 开始，`depth > 11` 时直接返回）；
  - 节点预算 **512**；
  - 总时间预算 **200 ms**（`QElapsedTimer::hasExpired(200)`）；
  - 只收集 `accRole ∈ {MENUPOPUP(11), MENUITEM(12)}` 且不含 `INVISIBLE(0x8000)/OFFSCREEN(0x10000)` 的矩形；
  - 只对 `index == 0` 的元素继续递归子节点；
- 去重后形成矩形列表，日志 `Captured menu rectangles: N elapsedMs: T`。

用途：菜单窗口（class `#32768`）和浏览器弹窗在快照阶段预先枚举菜单项矩形，后续点命中直接读这份缓存；本进程窗口无需再走跨进程 COM。

### 4.3 层级切换（滚轮）

- `WinGetParantWinRect`（`0x180008170`）：取当前包装器 → `parentAtPoint`（vtable +0x18）；父为空或矩形无效 → 回退到窗口级包装器并缓存结果；否则更新包装器并向内求交。
- `WinGetChildWinRect`（`0x180008410`）：对称逻辑，取子元素并选更小的有效矩形。
- 两者都把结果写进 `this+0x60/0x64` 的层级缓存；**鼠标离开缓存矩形即失效**。
- 与项目内 `step_level` 的"同元素内移动保持层级"语义一致。

### 4.4 缓存清理

- `UiRegionDetector::resetCache`（`0x180001490`）：清包装器、当前 hwnd、层级缓存；
- `ThreadWarp::resetCache`：投递 opcode 8，异步执行；
- 快照开始时还会清空两个静态 `QMap`（进程名缓存与窗口映射）。

## 5. 调用层（PixPin.exe 侧）

反编译范围：`PixPin.exe` 的 `0x14014e000–0x14015b000`（83 个函数）。exe 没有导出 C++ 符号，但 Qt 元对象调用和日志字符串保留了 `TrigerGraphicRectSelectItem` 的类名/方法名；以下结论都来自这两类证据。

### 5.1 创建与首次等待

`initUiRegionDetector`（`0x1401568e0`），由 `beginRegionEdit` / `setSelectMode` 调用：

1. 没有检测器时 `new UiRegionDetectorThreadWarp(params)`；已有则复用。
2. `params` 关键内容：
   - 全屏矩形（`PixScreenManager::wholeRect`）；
   - 忽略 HWND 列表：选区控件自身 + 所有子控件的 `winId()`（防止吸附到自己的遮罩）；
   - 是否仅窗口（由选择类型/全局配置决定）；
   - 初始鼠标点（相对选区控件原点）。
3. 构造完成后在 **UI 线程忙等首个结果**：

   ```
   QElapsedTimer start;
   loop:
       getFirstRect(&rect)          // 互斥锁读缓存，非阻塞
       if (有结果) break;
       if (hasExpired(1000)) { 日志 timeoutMs=1000; break; }   // 超时也继续
       msleep(1); processEvents(1);
   connect(sigNewRectDetected, slot);
   ```

   即首帧最多阻塞 **1000 ms**，期间持续泵 UI 事件；超时不算失败，带着空矩形继续。
4. 选区结束/重建时 `StopAndDeleteLater` 销毁检测器。

### 5.2 鼠标移动

- 只有 `status == 1`（选区进行中）、检测器和首个结果都就绪时，hover 事件才调用 `AsyncGetWinRectByPoint(当前点)`；窗口模式下由检测器的 `isOnlyWindow` 直接返回整窗；
- 每次 hover 都投递，调用层没有节流；靠 DLL 队列"合并队尾同 opcode + 1 ms"吸收。

### 5.3 结果应用

`sigNewRectDetected(rect, requestPoint, seq, queueMs, procMs)` 的槽：

- 校验 `status == 1` 且首个结果标志已置位；
- **过期结果守卫**：仅当"请求点 == 当前鼠标点"或"返回矩形包含当前鼠标点"时才应用，否则丢弃；
- 应用时对高亮做 120 ms 插值动画（`elapsed/120`，上限 1），并裁剪到屏幕与当前选区范围。

### 5.4 滚轮层级

滚轮处理（`0x1401540f0`）：

- 仅选择类型为元素吸附（`+0x20 == 2`）、`status == 1`（选区进行中）且修饰键配置开启时生效；
- `delta > 0`（上滚）→ `AsyncGetParantWinRect(point)`；`delta < 0` → `AsyncGetChildWinRect(point)`；
- 有 `WheelEventLimit(event, 50, false)` 与滚动相位去抖，避免一次滚动触发多次层级切换。

### 5.5 缓存重置与同步查询

- `setSelectMode`、`setStatus(2)`（已有选区/预设恢复）、`getUiRegionDetectorRect`（`status == 2` 时）都会先 `resetCache()`；
- `getUiRegionDetectorRect` 随后调用 `SyncGetWinRectByPoint`；
- **同步查询没有超时**：DLL 侧 `FUN_180001a50` 用 `PeekMessageW + msleep(1)` 自旋，直到 worker 写回结果或线程停止。调用线程（通常是 UI 线程）会被整段阻塞。该 API 的调用方不在本次反编译范围内，触发频率未确认。

### 5.6 与本项目调用层对照

| 环节 | PixPin | GridStruct |
|---|---|---|
| 首个结果 | UI 线程忙等最多 1000 ms（`processEvents` 泵消息） | 不等待，命中异步到达 |
| 移动请求 | 每次 hover 都投递 | `detect()` + 1 s 复用计时器 |
| 过期结果 | 槽内按当前鼠标位置校验 | worker 只保留最新请求 + `_shown_key/point` 校验 |
| 卡死窗口 | 301 ms 降级 + 线程保留 | 300 ms 降级 + 2 s 丢弃工作线程（最多 2 个） |
| 同步查询 | 无超时自旋 | 无此路径 |
| 自身窗口排除 | 创建时传入自身 + 子控件 HWND | `ignored_hwnds`（同思路） |

## 6. 常量与策略汇总

| 项 | 值 | 位置 |
|---|---|---|
| IAccessible 点命中最大深度 | 30 层 | `FUN_180004da0` |
| 快照递归最大深度 | 11 层（入口深度 1） | `FUN_1800034b0` |
| 快照节点预算 | 512 | `FUN_180003b50` |
| 快照时间预算 | 200 ms | `QElapsedTimer::hasExpired(200)` |
| 跨进程探活 | `SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, 3, 50ms)` | 快照/查询/DirectGetRect |
| 慢查询降级阈值 | 301 ms（仅 state==1） | `WinGetWinRectByPointUIA` |
| 窗口级回退缓存 | 1000 ms | `fallbackToHwnd`、死窗口路径 |
| 浏览器名单 | 59 个进程名 | `FUN_1800067d0` 懒加载 QHash |
| 进程名缓存上限 | 32 条 hwnd | `FUN_180006e40` |
| 请求节流 | 每请求 `msleep(1)` | `UiRegionDetectorThreadWarp::run` |
| 请求合并 | 队尾连续同 opcode 只保留最新 | `FUN_180001700` |
| 桌面类 | `Progman / WorkerW / WorkerA` + `explorer.exe` | 快照回调 |
| HWND-only 类 | `yong_input` | `FUN_1800061c0` |
| 特殊进程强制 HWND-only | `Unity.exe`、`EpicGamesLauncher.exe`、`Mybase.exe`、`Snap.Hutao.exe`、`GoldenDict.exe`、`openshot-qt.exe`、`qgis-bin.exe`（7 项映射到同一处理函数 `0x1800060a0`：`mov dword [rdx+0x18], 2; ret`） | `FUN_180001000` 静态初始化 + `FUN_1800061c0` 分派 |
| 首帧等待上限 | 1000 ms（1 ms 轮询 + `processEvents`） | `PixPin.exe` `initUiRegionDetector` |

## 7. 性能特征（本机实测 + 静态推断）

实测（同一台机器，真实可见窗口，MSAA/UIA 调用序列与本项目 `capture/snap.py` 一致）：

| 指标 | 数值 |
|---|---|
| 单次跨进程点命中（Excel 5 层） | 4.1 ms |
| 复用 `IAccessible` 根对象后的点命中（Chromium/Excel） | 2.3–4.1 ms |
| 首次查询（含 `AccessibleObjectFromWindow`） | 6.1–18.5 ms |
| `EnumWindows`+`GetWindowRect` 快照（13 窗） | 0.95 ms |
| 快照表线性命中一次 | 0.19 µs |
| `WindowFromPoint` | 7.5 µs |

推断（静态结构决定，未在 PixPin 内直接计时）：

- 点命中成本由跨进程 COM 往返主导；C++ 与 Python 在这条链路上没有可见差别。
- 浏览器二次命中会让**浏览器窗口的单次查询成本翻倍**（两次 `accHitTest`），换来更小/更准的矩形。
- 快照阶段额外成本：每个窗口 `OpenProcess + K2GetModuleBaseNameW`（毫秒级以下）+ 菜单/浏览器弹窗最多 200 ms 的树枚举。
- 请求队列合并队尾连续同 opcode 请求 + 1 ms 节流，快速移动鼠标不会无限堆积；若队列里夹着其他 opcode，旧请求不会被替换。

## 8. 与本项目（GridStruct）逐项对照

| 维度 | PixPin | GridStruct 当前实现 | 影响 |
|---|---|---|---|
| 元素来源 | IAccessible 点命中（主）+ 菜单/弹窗快照 + HWND 回退 + UIA 直取 | 仅 IAccessible 点命中 | 覆盖率：PixPin 对菜单/异常应用更稳 |
| 深度上限 | 30 | 31 | 等同 |
| 请求模型 | 队列 + 同 opcode 合并 + 1 ms 节流 | 单 worker + 只留最新请求 | 近似，本项目丢弃更彻底 |
| 结果复用 | 包装器缓存 + 窗口级回退 1000 ms + 层级缓存 | 命中路径缓存 1 s + 慢窗口降级 | 语义接近 |
| 慢查询 | 301 ms → 窗口级（仅普通窗口） | 300 ms → 窗口级；2 s 丢弃线程 | 阈值同源；本项目多了卡死线程处理 |
| 探活 | `IsHungAppWindow` + `WM_NULL` 50 ms | `SendMessageTimeoutW` 100 ms | PixPin 更激进，卡窗口更快降级 |
| 浏览器处理 | 59 进程名单 + Chromium 唤醒 + 二次命中取小 | 无 | 本项目在 Chromium 上常只能拿整窗 |
| 特殊进程 | 7 个进程名 → 强制 HWND 级（不做点命中） | 无 | 对已知无障碍不可用的程序更稳，但名单需维护 |
| 桌面 | explorer + 3 个类 → 整屏 | 窗口矩形（桌面窗口覆盖屏幕） | 结果近似 |
| 菜单 | 菜单/浏览器弹窗的菜单项矩形快照 | 无 | 菜单内细粒度命中缺失 |
| 失败兜底 | HWND 包装器 + 1000 ms | 整窗结果 + 1 s 复用 | 近似 |

## 9. 可直接借鉴 / 不建议照搬

可直接借鉴：

1. **浏览器名单 + 二次命中取小**：名单是公开进程名集合，实现成本低，能显著改善 Chromium 系程序的吸附粒度。
2. **Chromium 无障碍唤醒**：快照阶段对前台浏览器做一次 `AccessibleObjectFromWindow + get_accName`，降低首查空结果率。
3. **桌面特判**：`explorer.exe + Progman/WorkerW/WorkerA` 直接返回屏幕可用区域。
4. **窗口级回退带 TTL（1000 ms）**：避免每次移动都重试已知无元素窗口。
5. **菜单矩形快照**：菜单窗口预先收集菜单项矩形，层级/菜单吸附更细。
6. **信号携带耗时**：`queueMs / procMs` 便于线上定位慢窗口。
7. **已知不可用程序直接降级窗口级**：Unity/Epic 等 7 个进程名跳过元素点命中，避免每次移动都吃满慢查询阈值。

不建议照搬：

1. **静态进程名单维护**：59 + 7 个名字需要持续更新，建议用"可配置名单 + 默认集合"。
2. **301 ms 阈值硬编码**：不同机器/应用差异大，建议可配置。
3. **菜单快照的 200 ms 预算写死**：复杂菜单可能提前截断，需日志暴露。
4. **包装器缓存依赖外部 `resetCache`**：调用方忘记重置会显示旧元素；本项目用时间 TTL 更安全。

## 10. 未解问题

1. 快照窗口黑名单（`QStringList`）的内容未静态解析（运行时构造）。
2. `getUiRegionDetectorRect`（同步查询）的调用方不在本次反编译范围内，触发频率和是否在 UI 线程调用未确认；DLL 侧该路径没有超时。
3. 高亮渲染（遮罩、边框动画）不在该 DLL 内，未逐函数分析。
4. 上述"推断"项建议用真实应用做 A/B 计时验证，例如 Chrome / UWP 设置 / Unity 程序各 100 次点命中。

## 11. 复现方式

```bash
# 反编译全部函数（441 个，约 1.5 分钟）
rea decompile-all "C:\path\to\PixPin\UiRegionDetector.dll" \
  --provider ghidra --output <不存在的目录> --format json

# 单个函数
rea decompile "C:\path\to\PixPin\UiRegionDetector.dll" \
  --procedure DirectGetRect --provider ghidra --format json

# 调用层（PixPin.exe 无符号，按地址范围反编译）
REA_GHIDRA_STARTUP_TIMEOUT_MS=1500000 \
rea decompile-all "C:\path\to\PixPin\PixPin.exe" \
  --provider ghidra --address-start 0x14014e000 --address-end 0x14015b000 \
  --limit 200 --output <不存在的目录> --format json
```

运行前设置：`GHIDRA_INSTALL_DIR`、`JAVA_HOME`（JDK 21）。exe 导入较慢，需放宽 `REA_GHIDRA_STARTUP_TIMEOUT_MS`（默认 330000 ms 不够）。
