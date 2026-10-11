# 阶段 B–D：OCR 数据、校正、贴图复用与任务状态

日期：2026-10-11。用户在阶段 A 完成后要求“继续推进后续阶段”。本记录对应[总 spec](specs/core-workflow-improvements.md) DATA、REVIEW、TRANSFER、TASK。源码实现已完成，无需用户操作；没有新增依赖或替换本地模型。

## 实现与数据语义

本文 `.scratch/` 下的日志、JSON 和截图仅在验收机器本地保留，不随 Git 提交。仓库提交复跑脚本、验收摘要及限制说明；脚本会重新生成相应产物。

- `OCRDocument` 持有独立原图快照、原始结果、派生布局、校正及草稿。`OCRResult.polygon` 保存模型输出的四点物理像素，`box` 为按原图 DPR 换算的逻辑矩形；稳定 UUID 作为条目标识，`raw_index` 保留推理顺序。原始文字、置信度和几何不被校正覆盖，返回的矩形也不共享内部可变对象。
- 版面继续使用现有几何分栏与行段算法；`relayout()` 不调用识别，校正引用稳定标识。低置信度只提示复核，不删除文字。空文字原始证据仍保存，不伪造可定位条目。
- 逐条/逐段校正联动原图、选字、复制全文与共享贴图。段落改写关联原始成员集合及整个图像区域，合并成一个可复制区域，不把新文字猜测分配到旧行框。原图像素不变。
- 任意全文编辑作为独立导出草稿，复制入口与校正结果分开。后续校正显示草稿未同步提示，不覆盖草稿。待应用输入仅缓存在当前复核窗口，跨窗口刷新不清掉输入；同条外部修改提示冲突。关闭窗口不会将未应用输入写成已确认校正。
- 相同截图转贴图共享文档，不推理。来源窗口销毁后，贴图仍可复核。缩放只改变显示变换。标注撤销栈变化清除截图旧结果；完成挂层前再次比较当前图像，涵盖尚未提交撤销栈的拖动/文字编辑。被拒绝的迟到结果不显示“完成”。
- 会话沿用 `user-data/pins/session.json` version 1 外层，图片记录增加 version 1 的 `ocr` 字段，保存原始结果、布局、逐条/逐段校正、草稿及修订号。通过原始 PNG 的尺寸、DPR、SHA-256 内容绑定；DPR 不等同贴图缩放。旧记录没有 OCR 字段仍可恢复；PNG 和会话继续使用 `QSaveFile` 原子提交。
- OCR 损坏或图像不匹配时仍恢复图片，明确提示，并暂停自动保存保护原 JSON。需要修复或移走提示中的会话文件后重启；不会自动覆盖校正。验收只使用合成数据和隔离会话，未迁移或删除实际配置/贴图。

## 验收映射

| 要求 | 已执行验证 | 结果 |
|---|---|---|
| DATA-01–05 | [check_ocr_data.py](../tools/check_ocr_data.py)：乱序、双栏/跨栏标题、稳定身份、原始证据、旋转四点、空结果、重排、图像深快照、损坏 payload；DPR 1/1.25/1.5/2/2.25 的 PNG 往返 | 通过 |
| REVIEW-01–07 | [check_ocr_review.py](../tools/check_ocr_review.py)：双窗口同步、原图选字/全文/草稿复制区别、低分保留、段落区域、旋转框、原图像素对照 | 通过 |
| REVIEW 交互保护 | 同脚本：外部草稿/无关条目/同条修改不丢待应用输入、冲突提示、默认第一项重复点击、逐条/逐段切换、段落恢复、多窗口焦点范围 | 通过 |
| TRANSFER-01–04 | [check_ocr_data.py](../tools/check_ocr_data.py)：正式截图→贴图回调、来源 DeferredDelete 后继续修改、提交/拖动中的图像变化及迟到丢弃；[check_ocr_pins.py](../tools/check_ocr_pins.py)：缩放后实际 itemAt 定位 | 通过；转移推理数 0 |
| TRANSFER-05–07 | [check_ocr_pins.py](../tools/check_ocr_pins.py)：250ms 校正/草稿保存、重启恢复、旧图片/文字会话、原子提交中断、版本/hash/layout ID 损坏 | 8/8；恢复推理数 0；损坏原 JSON 字节不变 |
| TASK-01–06 | [check_ocr_tasks.py](../tools/check_ocr_tasks.py)：真实 QThread/服务、受控替身 9 次实际调用；排队取消、执行取消/重试、最近请求、加载/识别、空/异常/超时、预算后完成、关闭来源及直接复制 | 通过 |
| 真实模型 | [check_ocr_local.py](../tools/check_ocr_local.py)：正式串行服务冷/热识别、原始四点、校正序列化、运行取消和退出；网络调用设为直接失败 | 通过；0 次网络请求 |
| 既有回归 | [smoke_check.py](../tools/smoke_check.py)：真实模型、截图/标注/贴图、设置和生命周期；[check_ocr_feedback.py](../tools/check_ocr_feedback.py)：可见 Windows 业务窗口明暗状态和全文入口 | 21/21；可见反馈 PASS |

脚本刷新 `QEvent.DeferredDelete` 后再验证共享文档，收集 Qt 信号异常。未创建 `tests/` 或 `test_*.py`。

## 任务预算与实测

排队不启动超时；冷加载与推理共用原有 60 秒执行预算，从 worker 发出实际阶段开始计算。完成信号也复核耗时，避免繁忙 GUI 接受超预算结果。进度条为不确定模式，不显示推测百分比。运行取消立即结束窗口等待，ONNX 同步推理在后台结束，后继排队可见等待原因。

本机两次真实冷启动观察约 11.4–14.7 秒，热态约 1.0–1.1 秒。最后一次加载阶段开始 31ms、识别阶段开始 12094ms、完成 14687ms；热态完成 1047ms。样本只有两行英文，耗时包含线程和 GUI 调度，不是大图指标或 P95。

热态识别中取消，窗口等待在一个时钟刻度内结束；Windows 时钟分辨率不能将日志的 0ms 解释为无成本。退出等待同步推理结束实测 860ms，线程结束后重复收尾在一个刻度内完成。证据：[results.json](../.scratch/ocr-stages-bcd/local/results.json)、[输出](../.scratch/ocr-stages-bcd/local/run.txt)。异常底层推理阻塞时，退出等待没有硬上限；60 秒是窗口等待预算，不是杀线程保证。

## 视觉证据和复跑

明暗复核、草稿及待应用冲突提示截图已逐张自查：[复核目录](../.scratch/ocr-review-20261011/)。按钮、状态文字无截断。贴图及进度窗分别见[贴图目录](../.scratch/ocr-workflow-20261011/)和[进度目录](../.scratch/ocr-stages-bcd/tasks/)。截图仅辅助视觉检查，不代替业务准确率。

```powershell
& .venv\Scripts\python.exe -X utf8 tools/check_ocr_data.py
& .venv\Scripts\python.exe -X utf8 tools/check_ocr_review.py
& .venv\Scripts\python.exe -X utf8 tools/check_ocr_pins.py
& .venv\Scripts\python.exe -X utf8 tools/check_ocr_tasks.py
& .venv\Scripts\python.exe -X utf8 tools/check_ocr_local.py
& .venv\Scripts\python.exe -X utf8 tools/check_ocr_feedback.py
```

受限环境复跑 `smoke_check.py` 时将 TEMP/TMP 指向工作区临时目录，确保 Qt 原子 INI 写入有目录权限。可见反馈脚本使用正常 Windows 平台，其他脚本使用 offscreen。

## 尚未覆盖

复杂业务版面阅读顺序、中文字符错误率及扫描件质量需脱敏样本另测。实际多屏混合 DPI、大图 OCR 时延、冷启动统计分布及一周日常稳定性未在 B–D 验证。没有重新构建或验收新 ZIP，旧便携包不包含本次源码功能。
