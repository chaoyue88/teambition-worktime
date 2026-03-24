#!/usr/bin/env python3
"""
Teambition 认证模块
通过 JWT 签名生成 appAccessToken，用于调用 Teambition 开放平台 API。
"""

import json
import time
import sys
import os

try:
    import jwt
except ImportError:
    print("需要安装 PyJWT: pip install PyJWT")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("需要安装 requests: pip install requests")
    sys.exit(1)


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".teambition")
CONFIG_FILE = "config.json"

# skill 内置配置路径：scripts/ 的上级目录下的 references/config.json
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILTIN_CONFIG = os.path.join(_SKILL_DIR, "references", "config.json")


def init_config_dir():
    """创建配置目录并设置权限（仅所有者可读写）"""
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR, mode=0o700)
        print(f"已创建配置目录: {CONFIG_DIR}")
    return CONFIG_DIR


def _merge_config(base: dict, override: dict) -> dict:
    """
    将 override 合并到 base，返回新 dict。
    users/projects/tasks 等字典型字段做浅合并（override 优先），其余字段直接覆盖。
    """
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = {**result[k], **v}
        else:
            result[k] = v
    return result


def load_config(config_path: str = None) -> dict:
    """
    加载配置，支持内置配置 + 用户配置两层叠加。

    加载顺序（后者覆盖前者）：
      1. skill 内置配置：references/config.json（可选）
      2. 用户配置：指定路径 → ~/.teambition/config.json → 当前目录

    users/projects/tasks 字典做合并，其余字段直接覆盖。
    """
    # 第一层：内置配置
    config = {}
    if os.path.exists(BUILTIN_CONFIG):
        with open(BUILTIN_CONFIG, "r", encoding="utf-8") as f:
            config = json.load(f)

    # 第二层：用户配置（可选）
    user_paths = []
    if config_path:
        user_paths.append(config_path)
    user_paths += [
        os.path.join(CONFIG_DIR, CONFIG_FILE),
        os.path.join(os.getcwd(), "tb-worktime-config.json"),
    ]

    for p in user_paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            config = _merge_config(config, user_cfg)
            break  # 只取第一个找到的用户配置

    if not config:
        print("找不到配置文件，搜索过以下位置：")
        for p in [BUILTIN_CONFIG] + user_paths:
            print(f"  - {p}")
        print(f"\n推荐放在 {os.path.join(CONFIG_DIR, CONFIG_FILE)}")
        print("参考 references/setup-guide.md 创建配置")
        sys.exit(1)

    # 基本校验
    required = ["app_id", "app_secret", "organization_id"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        print(f"配置文件缺少必填字段: {', '.join(missing)}")
        sys.exit(1)

    config.setdefault("api_base", "https://open.teambition.com")
    return config


def get_app_token(app_id: str, app_secret: str, ttl: int = 3600) -> str:
    """
    生成 Teambition App Access Token (JWT)
    
    Args:
        app_id: 应用 ID
        app_secret: 应用密钥
        ttl: token 有效期（秒），默认 1 小时
    
    Returns:
        JWT token 字符串
    """
    now = int(time.time())
    payload = {
        "iat": now,
        "_appId": app_id,
        "exp": now + ttl,
    }
    headers = {
        "typ": "jwt",
        "alg": "HS256",
    }
    token = jwt.encode(payload, app_secret, algorithm="HS256", headers=headers)
    # PyJWT >= 2.0 返回 str，旧版返回 bytes
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token


class TeambitionClient:
    """Teambition API 客户端"""
    
    def __init__(self, config: dict = None, config_path: str = None):
        self.config = config or load_config(config_path)
        self.api_base = self.config["api_base"].rstrip("/")
        self.token = None
        self.token_expires = 0
    
    def _ensure_token(self):
        """确保 token 有效，过期则重新生成"""
        now = time.time()
        if self.token and now < self.token_expires - 60:  # 提前 60 秒刷新
            return
        self.token = get_app_token(
            self.config["app_id"],
            self.config["app_secret"],
        )
        self.token_expires = now + 3600
    
    def _headers(self, operator_id: str = None) -> dict:
        """构建请求头"""
        self._ensure_token()
        h = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-Tenant-Id": self.config["organization_id"],
            "X-Tenant-Type": "organization",
        }
        op_id = operator_id or self.config.get("default_user_id")
        if op_id:
            h["X-Operator-Id"] = op_id
        return h
    
    def _check_response(self, resp, path: str) -> dict:
        """检查 API 响应，新版 API 在 HTTP 200 中返回错误"""
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            code = data.get("code")
            err = data.get("errorMessage", "")
            error_code = data.get("errorCode", "")
            # 有些 API code=200 但 errorCode 非空表示有错误
            if code and code != 200 and err:
                raise RuntimeError(f"API 错误 [{path}]: {code} - {err}")
            # 有些 API code=200 但 errorCode 非空表示业务错误
            if error_code and err:
                raise RuntimeError(f"API 错误 [{path}]: {err}")
        return data

    def get(self, path: str, params: dict = None, operator_id: str = None) -> dict:
        """GET 请求"""
        url = f"{self.api_base}{path}"
        resp = requests.get(url, params=params, headers=self._headers(operator_id))
        return self._check_response(resp, path)

    def post(self, path: str, data: dict = None, operator_id: str = None) -> dict:
        """POST 请求"""
        url = f"{self.api_base}{path}"
        resp = requests.post(url, json=data, headers=self._headers(operator_id))
        return self._check_response(resp, path)

    def put(self, path: str, data: dict = None, operator_id: str = None) -> dict:
        """PUT 请求"""
        url = f"{self.api_base}{path}"
        resp = requests.put(url, json=data, headers=self._headers(operator_id))
        return self._check_response(resp, path)

    def delete(self, path: str, operator_id: str = None) -> bool:
        """DELETE 请求"""
        url = f"{self.api_base}{path}"
        resp = requests.delete(url, headers=self._headers(operator_id))
        resp.raise_for_status()
        return resp.status_code in (200, 204)


def main():
    """命令行入口"""
    import argparse
    parser = argparse.ArgumentParser(description="Teambition 认证工具")
    parser.add_argument("action", nargs="?", default="verify",
                        choices=["init", "verify"],
                        help="init=创建配置文件, verify=验证token(默认)")
    args = parser.parse_args()

    if args.action == "init":
        config_dir = init_config_dir()
        config_file = os.path.join(config_dir, CONFIG_FILE)
        if os.path.exists(config_file):
            print(f"配置文件已存在: {config_file}")
            return
        template = {
            "app_id": "",
            "app_secret": "",
            "organization_id": "",
            "api_base": "https://open.teambition.com",
            "default_user_id": ""
        }
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(template, f, indent=2, ensure_ascii=False)
        os.chmod(config_file, 0o600)  # 仅所有者可读写
        print(f"✅ 配置文件已创建: {config_file}")
        print(f"   权限已设为 600（仅所有者可读写）")
        print(f"   请编辑填入 app_id、app_secret、organization_id")
    else:
        config = load_config()
        token = get_app_token(config["app_id"], config["app_secret"])
        print(f"✅ Token 生成成功 (前 20 字符): {token[:20]}...")
        print(f"   API Base: {config['api_base']}")
        print(f"   Organization: {config['organization_id']}")
        print(f"   配置文件位置: {CONFIG_DIR}/{CONFIG_FILE}")


if __name__ == "__main__":
    main()
