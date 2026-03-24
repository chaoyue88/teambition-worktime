# Teambition API 参考 — 工时相关

本文档整理了 Teambition 开放平台中与工时填报相关的 API 端点。

> 注意：Teambition 的 API 文档维护质量不稳定，以下信息基于实际测试整理（2026-03-23 更新），如有变动以官方文档为准。

## 认证

### 获取 App Access Token

无需 HTTP 请求，本地 JWT 签名即可：

```python
import jwt, time

def get_app_token(app_id: str, app_secret: str, ttl: int = 3600) -> str:
    now = int(time.time())
    payload = {
        "iat": now,
        "_appId": app_id,
        "exp": now + ttl
    }
    return jwt.encode(payload, app_secret, algorithm="HS256")
```

### 请求头

```
Authorization: Bearer <app_access_token>
X-Tenant-Id: <organization_id>       # 必需：企业组织 ID
X-Tenant-Type: organization           # 必需：固定值
X-Operator-Id: <user_id>             # 部分端点必需：代表哪个用户操作
Content-Type: application/json
```

> **重要**：`X-Tenant-Id` 和 `X-Tenant-Type` 是新版 API 必需的头部，缺少会导致 421 MisdirectedRequest 错误。

## 组织 API

### 获取组织信息

```
GET /api/org/info?orgId={organizationId}
```

## 成员 API

### 获取企业成员列表

```
GET /api/org/member/list
```

查询参数：
- `pageSize`: 每页数量（默认 20，最大 100）
- `pageToken`: 分页游标

响应字段：
- `userId`: 用户 ID
- `memberId`: 成员 ID
- `name`: 用户名
- `email`: 邮箱
- `phone`: 手机号
- `isDisabled`: 是否禁用

## 项目 API

### 搜索项目（返回 ID 列表）

```
GET /api/project/search
```

查询参数：
- `pageSize`: 每页数量
- `pageToken`: 分页游标

> 注意：`keyword` 参数不生效，总是返回全部项目 ID 列表。

响应：
- `result`: 项目 ID 字符串数组
- `count`: 总数
- `nextPageToken`: 下一页游标

### 获取项目详情

```
GET /api/project/info?projectId={projectId}
```

响应字段：
- `projectId`: 项目 ID
- `name`: 项目名称
- `created`: 创建时间
- `isArchived`: 是否归档

## 任务 API

### 查询任务列表

```
GET /api/task/query
```

> **需要 `X-Operator-Id` 头部**

查询参数：
- `projectId`: 限定项目（可选）
- `pageSize`: 每页数量
- `pageToken`: 分页游标

响应字段：
- `taskId`: 任务 ID
- `content`: 任务标题
- `executorId`: 执行人 ID
- `isDone`: 是否完成
- `projectId`: 所属项目 ID
- `created`: 创建时间

### 获取任务详情

```
GET /api/task/info?taskId={taskId}
```

### 更新任务

```
POST /api/task/update
```

请求体：
```json
{
  "taskId": "任务ID",
  "estimatedTime": 28800000
}
```

## 工时 API

### 创建工时记录

```
POST /api/worktime/create
```

请求体：
```json
{
  "objectId": "任务ID",
  "objectType": "task",
  "worktime": 3600000,
  "date": "2026-03-23",
  "startDate": "2026-03-23",
  "endDate": "2026-03-23",
  "userId": "执行人ID",
  "submitterId": "提交人ID",
  "description": "工时说明"
}
```

字段说明：
- `worktime`: 工时时长，单位为**毫秒**（1小时 = 3600000ms）
- `date`/`startDate`/`endDate`: 日期，格式 `YYYY-MM-DD`
- `userId`: 工时执行人
- `submitterId`: 工时提交人
- `objectId`: 关联的任务 ID
- `objectType`: 固定 `"task"`

> **重要**：API 网关会剥离 JSON body 中以下划线 `_` 开头的字段。虽然 API schema 定义了 `_objectId`、`_userId` 等字段，但必须用不带下划线的 `objectId`、`userId` 发送。

### 查询任务工时记录列表

```
GET /api/worktime/list/task/{taskId}
```

查询参数：
- `pageSize`: 每页数量
- `pageToken`: 分页游标

响应字段：
- `worktimeId`: 工时记录 ID
- `objectId`: 任务 ID
- `userId`: 执行人 ID
- `submitterId`: 提交人 ID
- `worktime`: 工时（毫秒）
- `date`: 日期
- `description`: 描述
- `createdAt`: 创建时间

### 按用户查询计划工时记录

```
GET /api/plantime/query
```

查询参数：
- `userId`: 用户 ID（必需）
- `startDate`: 开始日期（格式 `YYYY-MM-DD`，必需）
- `endDate`: 结束日期（格式 `YYYY-MM-DD`，必需）
- `pageSize`: 每页数量
- `pageToken`: 分页游标

响应字段：
- `plantimeId`: 计划工时记录 ID
- `objectId`: 任务 ID
- `userId`: 用户 ID
- `plantime`: 计划工时（毫秒）
- `date`: 日期
- `createdAt` / `updatedAt`: 时间戳

> 注意：返回的 `date` 字段包含时区信息（如 `2026-03-23T00:00:00.000Z`），取前 10 位即为 `YYYY-MM-DD`。

### 按用户查询实际工时记录

```
GET /api/worktime/query
```

查询参数（均为必需）：
- `userId`: 用户 ID
- `startDate`: 开始日期（格式 `YYYY-MM-DD`）
- `endDate`: 结束日期（格式 `YYYY-MM-DD`，与 startDate 间隔不超过 90 天）
- `pageSize`: 每页数量
- `pageToken`: 分页游标

### 工时聚合查询

```
POST /api/worktime/aggregation/datesUsers
```

请求体：
```json
{
  "userIds": ["用户ID1", "用户ID2"],
  "subscriberId": "订阅者ID",
  "startDate": "2026-03-01",
  "endDate": "2026-03-31",
  "filter": {}
}
```

## API 速率限制

Teambition 对 API 请求有频率限制（具体限额未公开文档化），建议：
- 批量操作时每次请求间隔 200-500ms
- 出现 429 状态码时等待 5 秒后重试
- 使用并发时控制并发数不超过 10

## 已知问题

1. **旧版 API 已废弃**：`/api/v1/` 前缀的端点返回 421 MisdirectedRequest，必须使用新版 `/api/` 前缀
2. **工时单位是毫秒**：不是分钟，1小时 = 3600000ms
3. **下划线字段被剥离**：API 网关会从 JSON body 中移除 `_` 开头的字段，必须用无下划线版本
4. **`X-Operator-Id` 必需**：任务查询等端点要求此头部，否则返回 400
5. **项目搜索不支持关键词过滤**：`/api/project/search` 的 `keyword` 参数无效
6. **工时删除/更新接口不可用**：`/api/worktime/delete` 和 `/api/worktime/update` 返回 421
7. **计划工时 (plantime/create) 是累加模式**：重复调用会叠加工时值，不是覆盖。创建前必须查询去重
8. **计划工时删除用 HTTP DELETE**：`DELETE /api/plantime/{plantimeId}` 有效，但 `POST /api/plantime/delete` 返回 421
9. **计划工时更新不可用**：`POST /api/plantime/update` 和 `PUT /api/plantime/update` 均不可用
10. **API 返回 code=200 但 errorCode 非空**：部分 API（如 plantime/create 超过 24h 限制）HTTP 200 但 errorCode 和 errorMessage 包含真实错误信息
