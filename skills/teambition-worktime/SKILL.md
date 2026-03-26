---
name: teambition-worktime
description: Teambition 工时填报自动化工具。用于在 Teambition 经典版中填写计划工时和实际工时。支持按项目名模糊搜索项目、按任务名搜索任务、按人名搜索成员、时间段批量填报。当用户提到"填工时"、"报工时"、"工时填报"、"Teambition 工时"、"计划工时"、"实际工时"、"批量填工时"、"团队工时"、"搜索项目"、"搜索任务"、"查找成员"、"这周工时"、"这个月的工时"、"时间段填报"等相关内容时，务必使用此 skill。即使用户只是提到"把今天的工作时间记录一下"、"帮我把这周的工时补上"、"帮我找下那个XX项目"、"这个任务叫什么来着"，也应该触发此 skill。
author: Sam <772662699@qq.com>
---

# Teambition 工时填报

通过 Teambition 开放平台 API，实现计划工时和实际工时的自动填报。支持单人/多人、单任务/多任务、按周/按天批量填报。

## 前置条件

配置支持**两层叠加**，后者覆盖前者：

1. **内置配置**（可选）：`references/config.json` — 随 skill 一起提交，适合团队共享的固定配置
2. **用户配置**（可选）：`~/.teambition/config.json` — 本地覆盖，适合存放个人敏感凭证或差异化设置

`users`/`projects`/`tasks` 字典会合并（两层都保留，用户配置优先）；`app_id`/`app_secret` 等标量字段直接覆盖。

至少有一层配置存在且包含 `app_id`、`app_secret`、`organization_id` 即可运行。

首次使用时引导用户阅读 `references/setup-guide.md` 完成配置。

## 配置文件中的名称-ID 映射

配置文件支持预设常用的名称到 ID 的映射，避免每次都调 API 查询：

```json
{
  "app_id": "xxx",
  "app_secret": "xxx",
  "organization_id": "xxx",
  "api_base": "https://open.teambition.com",
  "default_user_id": "",
  "users": {
    "李明": "user_id_1",
    "王芳": "user_id_2",
    "陈浩": "user_id_3",
    "刘洋": "user_id_4"
  },
  "projects": {
    "技术中台项目": "project_id_1",
    "示例业务项目": "project_id_2"
  },
  "tasks": {
    "技术中台项目-平台日常管理": "task_id_1",
    "技术中台项目-基础设施运维": "task_id_2",
    "示例业务项目-功能开发&缺陷修复": "task_id_3"
  }
}
```

**重要：config 键名是本地别名，task_id 才是稳定标识**

- `tasks` 中的键（如 `"技术中台项目-平台日常管理"`）只是**本地别名**，用于方便人类阅读和匹配输入
- **task_id（值）是稳定的**：即使任务在 Teambition 网页中改名，task_id 不变，工时仍能正常填报
- 当系统通过 config 键命中 task_id 时，会自动调 API **验证 task_id 有效性**并检测改名：
  - 若任务在 Teambition 中已改名 → 打印提示，建议更新 config.json 键名，但**正常继续填报**
  - 若 task_id 已失效（任务被删除）→ 打印警告，**自动降级为按名称搜索**
- 任务键名推荐用 `项目名-任务名` 格式，方便识别；项目名和任务名均可含连字符（`-`）

## 本地缓存

脚本自动在 `~/.teambition/cache/` 下缓存用户列表、项目列表和任务列表，默认有效期 24 小时。

- 缓存文件：`members.json`、`projects.json`、`tasks_{projectId}.json`
- 自动：首次查询时自动拉取并缓存
- 手动更新：运行 `python scripts/tb_cache.py refresh --type all`
- 支持的 type：`members`、`projects`、`tasks`、`all`

当用户说"更新缓存"、"刷新列表"时，调用缓存刷新。

## 核心工作流程

### 1. 填写本周计划工时（最常用）

**典型输入格式：**
```
请帮{人名}填写本周的计划工时，{项目名}-{任务名} 每天计划投入X小时；{项目名}-{任务名} 每天计划投入Y小时
```

**示例 1（单人多任务）：**
> 请帮李明填写本周的计划工时，技术中台项目-平台日常管理 每天计划投入1小时；技术中台项目-基础设施运维，计划每天投入1小时

**示例 2（多人相同任务）：**
> 请帮王芳、陈浩、刘洋填写本周的计划工时，技术中台项目-平台日常管理 计划每天投入1小时；示例业务项目-功能开发&缺陷修复，计划每天投入8小时

**处理流程：**
1. 解析出人名列表、任务列表和每日计划工时
2. 根据"本周"计算出周一到周五的日期（跳过周末）
3. 通过配置映射或模糊搜索确定 user_id、task_id
4. 对每个人、每个任务、每个工作日，调用 `log_planned_hours` 创建计划工时记录（通过 `/api/plantime/create` 接口）
5. 汇总报告成功/失败数

**调用方式：**
```bash
python scripts/tb_worktime.py fill-weekly-planned \
  --users "李明" \
  --tasks "技术中台项目-平台日常管理:1,技术中台项目-基础设施运维:1" \
  --week current
```

`--week` 支持 `current`（本周）、`next`（下周）、`2026-03-23`（指定某周）。

多人时用逗号分隔：`--users "王芳,陈浩,刘洋"`

### 2. 填写实际工时（默认按计划工时填报）

**核心规则：用户说"填实际工时"、"补实际工时"但未指定具体工时数时，直接调用 `fill-actual-from-planned`，无需询问任务或工时。**

自动从 Teambition 读取该用户的计划工时，对尚未填写实际工时的条目逐一填报，已有记录则跳过。

**默认日期规则（重要）：**
- **未指定日期时，默认只填今天**，即 `--start <今天> --end <今天>`
- 用户明确说"本周"、"这周"时，才使用 `--week current`（结束日期自动 cap 到今天）
- 用户明确说"昨天"时，用 `--start <昨天> --end <昨天>`

**典型触发场景：**
- "帮黄超补充下实际工时" → 只填今天（`--start <今天> --end <今天>`）
- "帮李明补昨天的实际工时" → 只填昨天（`--start <昨天> --end <昨天>`）
- "帮李明填本周实际工时" → 本周，结束日期 cap 到今天（`--week current`）
- "帮王芳补上周的实际工时" → 计算上周日期范围

```bash
# 默认：只填今天（最常用）
python scripts/tb_worktime.py fill-actual-from-planned \
  --users "李明" \
  --start 2026-03-26 --end 2026-03-26

# 明确要求本周（到今天）
python scripts/tb_worktime.py fill-actual-from-planned \
  --users "李明" \
  --week current

# 指定日期范围
python scripts/tb_worktime.py fill-actual-from-planned \
  --users "李明" \
  --start 2026-03-16 --end 2026-03-20
```

**仅当用户明确指定了任务和工时数时**，才使用 `fill-range-actual` 或 `log-actual`：

```bash
# 用户明确说"X任务填Y小时"
python scripts/tb_worktime.py log-actual \
  --user "李明" \
  --task-key "技术中台项目-平台日常管理" \
  --hours 1 \
  --date 2026-03-24 \
  --desc "需求开发和问题跟进"

# 用户指定多任务+具体工时（含工作进展）
python scripts/tb_worktime.py fill-range-actual \
  --users "李明" \
  --tasks "技术中台项目-平台日常管理:1:需求开发和问题跟进,技术中台项目-基础设施运维:0.5" \
  --start 2026-03-24 --end 2026-03-24
```

### 3. 批量填写实际工时（按日期范围，指定工时）

**示例：**
> 帮李明补填上周一到周五的实际工时，技术中台项目-平台日常管理 每天1小时

```bash
python scripts/tb_worktime.py fill-range-actual \
  --users "李明" \
  --tasks "技术中台项目-平台日常管理:1" \
  --start 2026-03-16 --end 2026-03-20
```

### 5. 搜索项目、任务、成员

当用户不确定确切名称时，先通过模糊搜索找到目标：

```bash
# 模糊搜索项目
python scripts/tb_cache.py search --type projects --keyword "示例业务项目"

# 模糊搜索任务
python scripts/tb_cache.py search --type tasks --keyword "日常管理"

# 模糊搜索成员
python scripts/tb_cache.py search --type members --keyword "黄"
```

搜索会先查本地缓存，缓存未命中时调 API。

### 6. 刷新缓存

```bash
python scripts/tb_cache.py refresh --type all
```

## 名称解析流程

当用户输入人名/项目名/任务名时，按此顺序解析：

1. **精确匹配配置映射** — 查 config.json 中的 users/projects/tasks 字典
2. **模糊匹配本地缓存** — 对缓存列表做子串匹配
3. **API 在线搜索** — 缓存未命中时调 API 搜索并更新缓存
4. **多结果提示用户确认** — 如果匹配到多个结果，列出让用户选择

## 脚本说明

| 脚本 | 用途 |
|------|------|
| `scripts/tb_auth.py` | 认证模块：JWT 签名、TeambitionClient |
| `scripts/tb_worktime.py` | 工时操作：计划/实际工时的填写、查询、批量操作 |
| `scripts/tb_cache.py` | 缓存管理：本地缓存的读写、搜索、刷新 |

所有脚本支持命令行调用和 import 调用。

## 错误处理

- **401**：token 过期，自动重新获取
- **404**：任务不存在，提示确认名称
- **403**：无权限，提示检查应用权限
- **429**：限流，等待后重试（内置 300ms 间隔）
- **名称未找到**：列出相似结果供用户选择

## 日期计算指引

| 用户说法 | 计算方式 | 命令示例 |
|---------|---------|---------|
| 本周 | `--week current` | `fill-weekly-planned --week current` 或 `fill-weekly-actual --week current` |
| 下周 | `--week next` | `fill-weekly-planned --week next` |
| 上周 | 计算上周一和周五的日期 | `fill-range-actual --start <上周一> --end <上周五>` |
| 今天 | 今日日期 | `log-actual --date <今天>` 或 `fill-range-actual --start <今天> --end <今天>` |
| 指定某周 | 该周任意一天即可 | `fill-weekly-planned --week 2026-03-16` |
| 指定日期范围 | 直接用 start/end | `fill-range-actual --start 2026-03-01 --end 2026-03-07` |

"上周"日期计算方法：今天是周X，则上周一 = 今天减去 `(今天weekday + 7)` 天，上周五 = 今天减去 `(今天weekday + 3)` 天。例如今天是 2026-03-24（周二），上周一 = 2026-03-16，上周五 = 2026-03-20。

## 注意事项

- 工时单位统一为**小时**，支持小数（如 0.5、1.5）
- "本周"指当前自然周的周一到周五
- 批量操作自动控制请求频率，避免触发 API 限流
- 计划工时一般周一填写当周的；实际工时一般当天结束或隔天填写
- 同一天填写多个任务的实际工时，用 `fill-range-actual --start DATE --end DATE` 比多次 `log-actual` 更简洁
- 实际工时（worktime）创建前会自动检查同用户同任务同日是否已存在记录，已存在则跳过（避免重复）
- **工作进展（description）**：实际工时提交时会附带工作描述。`fill-actual-from-planned` 自动用任务名作为描述；`fill-range-actual`/`log-actual` 支持在 `--tasks` 中用第三段指定进展（格式 `任务名:工时:进展`），未指定时自动用任务名代替。如用户提供了进展描述，应将其传入对应字段
- 任务名在 Teambition 改动后，脚本会自动刷新缓存重新搜索；若仍找不到，会打印该项目下的任务列表供参考
- 首次使用建议先用测试任务验证配置
