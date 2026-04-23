# M&S Men Clothing 图片抓取脚本

本项目用于批量抓取 M&S 男装（Men/Clothing）商品图，并将多张图拼接成单张图片，最后同步到共享盘。

## 功能概览

- 按分类批量遍历商品列表页（支持翻页）。
- 商品页提取信息：`colour`、`price`、`composition`、`product code`。
- 下载多张原图并拼接，底部追加商品文本信息。
- 输出策略：先保存到本地，再传共享盘；共享盘校验成功后删除本地文件。
- 断点续跑：分类进度和单商品下载进度都会落盘。
- 去重策略：URL 归一化去重 + 进度去重 + 款号文件名去重。
- 日志策略：每次启动先清理旧 `.log`，本次统一写入 `logs/ms_downloader.log`。

## 目录与文件

- `ms_downloader.py`：主脚本。
- `M&S_temp/progress/*.json`：分类进度文件（已完成 URL）。
- `M&S_temp/<category>/<product_code>/progress.json`：单商品下载进度。
- `logs/ms_downloader.log`：本次运行日志（每次运行会覆盖旧日志）。

## 运行环境

- Windows + Python 3.10+
- 建议安装 Pillow（未安装时脚本会尝试 PowerShell + System.Drawing 回退方案）

## 快速开始

1. 在项目目录运行：

```powershell
py -3 ms_downloader.py
```

2. 后台运行（推荐）：

```powershell
Start-Process -FilePath py -ArgumentList '-3','-u','ms_downloader.py' -WorkingDirectory 'C:\Users\Administrator\PycharmProjects\PythonProject'
```

3. 查看日志：

```powershell
Get-Content .\logs\ms_downloader.log -Tail 100
```

## 关键配置（`ms_downloader.py`）

- `CATEGORIES`：要遍历的分类入口。
- `REQUEST_DELAY_SECONDS`：请求间隔秒数（建议 `1.0`，更稳）。
- `LOCAL_OUTPUT_ROOT`：本地落盘目录。
- `SHARED_OUTPUT_ROOT`：共享盘目录。
- `MAX_RETRIES` / `BACKOFF_BASE_SECONDS`：网络失败重试策略。
- `MIN_OUTPUT_FILE_BYTES` / `MAX_OUTPUT_FILE_BYTES`：输出图片体积控制。

## 去重与断点续跑说明

### 1) URL 去重

商品 URL 会归一化（仅保留 `color` 参数），同款重复链接只处理一次。

### 2) 分类进度去重

每个分类有 `progress/<category>.json`，其中 `completed` 记录已处理 URL。再次运行会直接跳过。

### 3) 同款文件去重

商品以 `product_code.png` 命名：

- 共享盘已存在：直接跳过。
- 仅本地存在：尝试补同步到共享盘，成功后删除本地。

### 4) 单商品下载进度

单商品目录内 `progress.json` 记录已下载图片 URL，对中断恢复更友好。

## 运行流程

1. 启动时清理旧日志并初始化目录。
2. 启动时先执行本地历史文件补同步到共享盘。
3. 按分类抓取列表页，构建商品 URL 池。
4. 逐商品下载图片并拼图。
5. 本地图片传共享盘并做大小一致性校验。
6. 校验通过后删除本地文件并更新进度。

## 常见问题

### 日志看起来不更新

- 使用 `-u` 参数启动 Python，减少缓冲影响：
  - `py -3 -u ms_downloader.py`

### 出现 `WinError 2` 或 `Permission denied`

- 常见原因是多实例并发抢同一文件。
- 建议同一时间只运行一个实例。

### 抓取速度慢

- 先确认网络和共享盘稳定性。
- 可微调 `REQUEST_DELAY_SECONDS`，但过小会增加反爬风险（`429/503`）。

### 图片体积过大

- 脚本已自动做缩放搜索控制体积。
- 若仍超限，会在日志里输出 warning（例如最小缩放仍超过 30MB）。

## 注意事项

- 本脚本涉及目标网站请求频率控制，建议保持温和抓取频率，避免触发反爬。
- 如果共享盘短时不可写，脚本会保留本地文件，后续可自动补同步。
