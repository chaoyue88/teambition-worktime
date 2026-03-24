# Teambition 开放平台配置指南

## 第一步：创建企业内部应用

1. 登录 Teambition 企业账号（需管理员权限）
2. 点击左上角菜单 → 进入企业"管理后台"
3. 找到"应用管理" → "应用商店" → "开放平台"
4. 点击"立即创建" → 选择"企业内部应用"
5. 填写应用名称（如"工时自动填报"）和描述

## 第二步：获取应用凭证

在应用详情页 → "应用凭证和基础信息"中获取：

- **App ID** (`app_id`)
- **App Secret** (`app_secret`)

> 提示：可以按 F12 在页面源码中直接复制这两个值

## 第三步：配置应用权限

进入"应用开发" → "应用权限"，勾选以下权限：

- [x] 读取企业信息
- [x] 读取项目信息
- [x] 读取任务信息
- [x] 写入任务信息（工时填报需要）
- [x] 读取成员信息
- [x] 工时管理（如有此选项）

## 第四步：发布应用

1. 进入"应用发布"
2. 填写版本号（如 1.0.0）和版本描述
3. 点击"发布"
4. 企业管理员会收到通知，确认安装

## 第五步：获取组织 ID

组织 ID 可以从以下方式获取：

- 浏览器访问 Teambition 企业页面，URL 中 `/organization/` 后面的字符串就是 `organization_id`
- 例如：`https://www.teambition.com/organization/61db9af2148974246bexxxx` 中的 `61db9af2148974246bexxxx`

## 第六步：创建配置文件

**方式一（推荐）：** 用脚本自动初始化

```bash
python scripts/tb_auth.py init
```

这会在 `~/.teambition/config.json` 创建配置模板，并自动设置文件权限为 600（仅所有者可读写），然后编辑填入实际值即可。

**方式二：** 手动创建

```bash
mkdir -p ~/.teambition
chmod 700 ~/.teambition
```

创建 `~/.teambition/config.json`：

```json
{
  "app_id": "从第二步获取",
  "app_secret": "从第二步获取",
  "organization_id": "从第五步获取",
  "api_base": "https://open.teambition.com",
  "default_user_id": ""
}
```

然后设置文件权限：
```bash
chmod 600 ~/.teambition/config.json
```

`default_user_id` 可留空，脚本会通过 API 自动获取当前用户 ID。

> **安全提示：** 配置文件含 `app_secret`，请勿提交到 Git 仓库。`~/.teambition/` 目录权限为 700，文件权限为 600，确保只有你自己能读取。

**配置文件搜索顺序：**
1. 命令行 `--config` 指定的路径
2. `~/.teambition/config.json`（推荐）
3. 当前工作目录下的 `tb-worktime-config.json`（兼容旧方式）

## API 服务器说明

| 版本 | API Base URL |
|------|-------------|
| 中国服务器（经典版） | `https://open.teambition.com` |
| 海外服务器 | `https://us.teambition.com/api` |

## 认证方式

Teambition 开放平台使用 **JWT + App Token** 认证：

1. 用 `app_id` 和 `app_secret` 通过 HS256 签名生成 JWT
2. JWT payload 包含 `iat`（签发时间）、`_appId`、`exp`（过期时间）
3. 将 JWT 作为 `Authorization: Bearer <token>` 请求头发送

脚本 `tb_auth.py` 已封装此流程，无需手动处理。

## 常见问题

### Q: 提示 401 错误
A: 检查 `app_id` 和 `app_secret` 是否正确，确认应用已发布并安装

### Q: 提示 403 错误
A: 检查应用权限配置，确保勾选了工时相关权限

### Q: 找不到开放平台入口
A: 确认你的 Teambition 是企业版，个人版不支持开放平台
