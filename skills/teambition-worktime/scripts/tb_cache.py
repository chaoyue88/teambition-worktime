#!/usr/bin/env python3
"""
Teambition 本地缓存模块
缓存用户、项目、任务列表，支持模糊搜索和自动过期。

Author: Sam <772662699@qq.com>
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tb_auth import TeambitionClient, load_config

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".teambition", "cache")
CACHE_TTL = 86400  # 24 小时


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"{name}.json")


def _read_cache(name: str) -> Optional[dict]:
    """读取缓存，过期返回 None"""
    path = _cache_path(name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) > CACHE_TTL:
            return None
        return data
    except (json.JSONDecodeError, IOError):
        return None


def _write_cache(name: str, items: list):
    """写入缓存"""
    _ensure_cache_dir()
    data = {"timestamp": time.time(), "items": items}
    path = _cache_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class TBCache:
    """Teambition 缓存管理器"""

    def __init__(self, config_path: str = None):
        self.config = load_config(config_path)
        self.org_id = self.config["organization_id"]
        self._client = None

    @property
    def client(self) -> TeambitionClient:
        if self._client is None:
            self._client = TeambitionClient(config=self.config)
        return self._client

    def ensure_operator_id(self):
        """确保配置中有 default_user_id，否则从成员列表中取第一个"""
        if self.config.get("default_user_id"):
            return
        members = self.get_members()
        if members:
            self.config["default_user_id"] = members[0]["id"]

    # ── 成员 ──

    def get_members(self, force_refresh: bool = False) -> list:
        """获取成员列表（优先缓存）"""
        if not force_refresh:
            cached = _read_cache("members")
            if cached:
                return cached["items"]

        all_members = []
        page_token = None
        while True:
            params = {"pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            result = self.client.get("/api/org/member/list", params=params)
            items = result.get("result", []) if isinstance(result, dict) else []
            for m in (items or []):
                all_members.append({
                    "id": m.get("userId", ""),
                    "name": m.get("name", ""),
                    "email": m.get("email", ""),
                })
            page_token = result.get("nextPageToken") if isinstance(result, dict) else None
            if not page_token or not items:
                break
            time.sleep(0.3)
        _write_cache("members", all_members)
        return all_members

    # ── 项目 ──

    def _fetch_all_project_ids(self) -> list:
        """获取所有项目 ID（分页遍历）"""
        all_ids = []
        page_token = None
        max_pages = 20
        for _ in range(max_pages):
            params = {"pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            result = self.client.get("/api/project/search", params=params)
            ids = result.get("result", []) if isinstance(result, dict) else []
            all_ids.extend(pid for pid in ids if isinstance(pid, str))
            page_token = result.get("nextPageToken") if isinstance(result, dict) else None
            if not page_token or not ids:
                break
            time.sleep(0.2)
        return all_ids

    def _fetch_project_info(self, project_id: str) -> dict:
        """获取单个项目详情"""
        try:
            info = self.client.get("/api/project/info", params={"projectId": project_id})
            p = info.get("result", {}) if isinstance(info, dict) else {}
            if p and isinstance(p, dict):
                return {"id": p.get("projectId", project_id), "name": p.get("name", "")}
        except Exception:
            pass
        return {"id": project_id, "name": ""}

    def get_projects(self, force_refresh: bool = False) -> list:
        """获取项目列表（优先缓存）。全量拉取使用并发加速。"""
        if not force_refresh:
            cached = _read_cache("projects")
            if cached:
                return cached["items"]

        print("  正在拉取项目列表（首次较慢，使用并发加速）...")
        all_ids = self._fetch_all_project_ids()
        all_projects = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self._fetch_project_info, pid): pid for pid in all_ids}
            done = 0
            for future in as_completed(futures):
                result = future.result()
                if result:
                    all_projects.append(result)
                done += 1
                if done % 100 == 0:
                    print(f"  已获取 {done}/{len(all_ids)} 个项目...")
        _write_cache("projects", all_projects)
        print(f"  项目列表已缓存: {len(all_projects)} 个")
        return all_projects

    def search_projects_api(self, keyword: str, max_results: int = 10) -> list:
        """通过 API 并发搜索项目：先拉全部 ID，再并发获取详情并过滤。"""
        keyword_lower = keyword.lower()
        all_ids = self._fetch_all_project_ids()
        results = []
        all_infos = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self._fetch_project_info, pid): pid for pid in all_ids}
            for future in as_completed(futures):
                info = future.result()
                if info and info["name"]:
                    all_infos.append(info)
                    if keyword_lower in info["name"].lower():
                        results.append(info)

        # 将获取到的所有项目写入缓存
        if all_infos:
            _write_cache("projects", all_infos)

        return results[:max_results]

    # ── 任务 ──

    def _get_tasklists(self, project_id: str) -> list:
        """获取项目下所有 tasklist"""
        all_tasklists = []
        page_token = None
        while True:
            params = {"projectId": project_id, "pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            result = self.client.get("/api/tasklist/query", params=params)
            items = result.get("result", []) if isinstance(result, dict) else []
            if not items:
                break
            all_tasklists.extend(items)
            page_token = result.get("nextPageToken") if isinstance(result, dict) else None
            if not page_token:
                break
            time.sleep(0.2)
        return all_tasklists

    def get_tasks(self, project_id: str) -> list:
        """获取项目下的全量任务列表（直接调 API，不使用缓存）。

        策略：遍历所有 tasklist，每次查询结果只保留 tasklistId 匹配的任务，
        合并去重——解决 /api/task/query?projectId 不返回全部任务的问题。
        """
        self.ensure_operator_id()
        tasklists = self._get_tasklists(project_id)
        all_tasks = []
        seen_ids = set()

        for tl in tasklists:
            tl_id = tl.get("tasklistId", "")
            if not tl_id:
                continue
            page_token = None
            while True:
                params = {"projectId": project_id, "tasklistId": tl_id, "pageSize": 100}
                if page_token:
                    params["pageToken"] = page_token
                try:
                    result = self.client.get("/api/task/query", params=params)
                except Exception as e:
                    print(f"  跳过 tasklist {tl_id}: {e}")
                    break
                tasks_raw = result.get("result", []) if isinstance(result, dict) else []
                for t in tasks_raw:
                    # 只保留真正属于本 tasklist 的任务，去重
                    if t.get("tasklistId", "") != tl_id:
                        continue
                    task_id = t.get("taskId", "")
                    if not task_id or task_id in seen_ids:
                        continue
                    seen_ids.add(task_id)
                    all_tasks.append({
                        "id": task_id,
                        "content": t.get("content", ""),
                        "isDone": t.get("isDone", False),
                        "projectId": project_id,
                        "tasklistId": tl_id,
                    })
                page_token = result.get("nextPageToken") if isinstance(result, dict) else None
                if not page_token or not tasks_raw:
                    break
                time.sleep(0.2)
            time.sleep(0.1)

        return all_tasks

    def get_all_tasks(self) -> list:
        """获取 config 配置项目的任务（直接调 API，不使用缓存）"""
        projects_map = self.config.get("projects", {})
        if not projects_map:
            print("  config 中未配置 projects，跳过")
            return []
        all_tasks = []
        for proj_name, proj_id in projects_map.items():
            try:
                tasks = self.get_tasks(proj_id)
                for t in tasks:
                    t["projectName"] = proj_name
                all_tasks.extend(tasks)
                print(f"  {proj_name}: {len(tasks)} 个任务")
            except Exception as e:
                print(f"  跳过项目 {proj_name}: {e}")
            time.sleep(0.3)
        return all_tasks

    # ── 搜索工具 ──

    def _fuzzy_match(self, keyword: str, candidates: list, name_field: str = None, threshold: float = 0.6) -> list:
        """
        子串匹配 + 字符相似度模糊匹配。

        Args:
            keyword: 搜索关键词
            candidates: 候选列表 (dict)
            name_field: 指定匹配字段名，None 则自动检测 content/name
            threshold: 相似度阈值 (0-1)

        Returns:
            [{id, name, score}, ...] 按 score 降序
        """
        keyword_lower = keyword.lower()
        results = []

        for c in candidates:
            name = c.get(name_field) if name_field else (c.get("content") or c.get("name", ""))
            if not name:
                continue
            name_lower = name.lower()

            if keyword_lower in name_lower:
                # 子串完全包含
                results.append({"id": c.get("id"), "name": name, "score": 1.0})
            elif name_lower in keyword_lower:
                # 反向包含（候选名是关键词的子串）
                results.append({"id": c.get("id"), "name": name, "score": 0.9})
            else:
                # 字符集相似度
                common = len(set(keyword_lower) & set(name_lower))
                sim = (common * 2) / (len(keyword_lower) + len(name_lower)) if (keyword_lower and name_lower) else 0
                if sim >= threshold:
                    results.append({"id": c.get("id"), "name": name, "score": sim})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def _search_in_cache_items(self, keyword: str, items: list, name_field: str = None) -> list:
        """在列表中按 精确子串 → 模糊 的顺序搜索"""
        keyword_lower = keyword.lower()
        field = name_field or "name"

        # 精确子串匹配
        exact = [item for item in items if keyword_lower in (item.get(field) or item.get("content") or item.get("name") or "").lower()]
        if exact:
            return exact

        # 模糊匹配
        return self._fuzzy_match(keyword, items, name_field=name_field, threshold=0.6)

    def _get_relevant_project_ids(self) -> list:
        """返回 config 中配置的项目 ID 列表（有填报记录的项目）"""
        projects_map = self.config.get("projects", {})
        return list(projects_map.values()) if projects_map else []

    def search(self, entity_type: str, keyword: str, project_id: str = None) -> list:
        """
        三级搜索：配置映射 → 本地缓存 → API 接口。
        每级内部先精确后模糊，找到匹配即停。

        entity_type: members / projects / tasks
        keyword: 搜索关键词
        project_id: 限定搜索范围（仅 tasks 有效），None 时只搜 config 配置的项目
        """
        keyword_lower = keyword.lower()
        config_key = {"members": "users", "projects": "projects", "tasks": "tasks"}.get(entity_type, "")

        # ── 第一级：配置映射 ──
        # 当指定 project_id 搜索任务时，跳过配置映射（映射中无法区分任务所属项目）
        config_map = self.config.get(config_key, {})
        if config_map and not (entity_type == "tasks" and project_id):
            # 精确匹配
            if keyword in config_map:
                return [{"name": keyword, "id": config_map[keyword]}]
            # 子串匹配
            substr = [{"name": k, "id": v} for k, v in config_map.items() if keyword_lower in k.lower()]
            if substr:
                return substr

        # ── 第二级：本地缓存 ──
        if entity_type == "members":
            items = self.get_members()
            results = self._search_in_cache_items(keyword, items)
            if results:
                return results

        elif entity_type == "projects":
            cached = _read_cache("projects")
            if cached:
                results = self._search_in_cache_items(keyword, cached["items"])
                if results:
                    return results

        elif entity_type == "tasks":
            # 任务不使用缓存，直接走 API（见下方第三级）
            pass

        # ── 第三级：API 接口 ──
        if entity_type == "projects":
            return self.search_projects_api(keyword)
        elif entity_type == "tasks":
            # 只在指定项目或 config 配置的项目里搜索，不做全局搜索
            search_pids = [project_id] if project_id else self._get_relevant_project_ids()
            self.ensure_operator_id()
            for pid in search_pids:
                try:
                    api_result = self.client.get("/api/task/query", params={"projectId": pid, "pageSize": 100})
                    tasks_raw = api_result.get("result", []) if isinstance(api_result, dict) else []
                    matched = [
                        {"id": t.get("taskId", ""), "content": t.get("content", ""), "name": t.get("content", "")}
                        for t in (tasks_raw or [])
                        if keyword_lower in t.get("content", "").lower()
                    ]
                    if matched:
                        return matched
                except Exception as e:
                    print(f"API 搜索失败 (项目 {pid}): {e}")
        elif entity_type == "members":
            pass

        return []

    # ── 名称解析（核心） ──

    def resolve_user(self, name: str) -> Optional[str]:
        """根据人名解析 user_id，返回 None 表示未找到"""
        # 精确匹配配置
        users_map = self.config.get("users", {})
        if name in users_map:
            return users_map[name]

        # 模糊搜索
        results = self.search("members", name)
        if len(results) == 1:
            return results[0]["id"]
        elif len(results) > 1:
            print(f"找到多个匹配 '{name}' 的成员:")
            for r in results:
                print(f"  {r.get('id', '')}  {r.get('name', '')}")
            return None
        return None

    def _lookup_task_by_id(self, task_id: str) -> Optional[dict]:
        """直接通过 task_id 查询任务详情（支持无 executor 的特殊任务）"""
        try:
            result = self.client.get("/api/task/query", params={"taskId": task_id})
            tasks = result.get("result", []) if isinstance(result, dict) else []
            if tasks:
                t = tasks[0]
                return {
                    "id": t.get("taskId", ""),
                    "content": t.get("content", ""),
                    "isDone": t.get("isDone", False),
                    "projectId": t.get("projectId", ""),
                    "tasklistId": t.get("tasklistId", ""),
                }
        except Exception:
            pass
        return None

    def search_tasks_in_project(self, project_id: str, task_keyword: str) -> list:
        """
        在指定项目内搜索任务（config → 缓存 → API）。
        返回 [{"id": ..., "content": ...}, ...]
        """
        keyword_lower = task_keyword.lower()

        # 先检查 config 中是否有该项目下匹配的任务（支持无 executor 的特殊任务）
        # 注意：项目名和任务名都可能含有 '-'，因此需要尝试所有分割点而非只 split("-", 1)
        tasks_map = self.config.get("tasks", {})
        for key, task_id in tasks_map.items():
            if "-" not in key:
                continue
            key_parts = key.split("-")
            for split_at in range(1, len(key_parts)):
                proj_part = "-".join(key_parts[:split_at])
                task_part = "-".join(key_parts[split_at:])
                resolved_pid = self.resolve_project(proj_part)
                if resolved_pid == project_id:
                    # 找到了正确的项目分割点
                    if keyword_lower in task_part.lower():
                        task_detail = self._lookup_task_by_id(task_id)
                        if task_detail:
                            current_name = task_detail.get("content", "")
                            if current_name and current_name.lower() not in key.lower():
                                print(f"  [提示] 配置任务 '{key}' 在 Teambition 中当前名称为 '{current_name}'")
                            return [task_detail]
                    break  # 找到了正确项目，但任务名不匹配，无需尝试其他分割点

        def _do_search(tasks: list) -> tuple:
            """返回 (exact_results, fuzzy_results)"""
            exact = [t for t in tasks if keyword_lower in t.get("content", "").lower()]
            if exact:
                return exact, []
            fuzzy = self._fuzzy_match(task_keyword, tasks, name_field="content", threshold=0.6)
            return [], fuzzy

        # 直接从 API 获取（无缓存）
        tasks = self.get_tasks(project_id)
        exact, fuzzy = _do_search(tasks)
        if exact:
            return exact

        if fuzzy:
            match_name = fuzzy[0].get("name") or fuzzy[0].get("content", "")
            score = fuzzy[0].get("score", 0)
            print(f"  [模糊匹配] '{task_keyword}' → '{match_name}' (相似度 {score:.0%})，请确认是否正确")
            return fuzzy

        # 完全找不到：展示项目下现有任务供参考
        print(f"  [未找到] 任务 '{task_keyword}' 在项目中不存在")
        if tasks:
            print(f"  该项目下共 {len(tasks)} 个任务，前10个：")
            for t in tasks[:10]:
                content = t.get("content", "")
                if content:
                    print(f"    - {content}")
        return []

    def resolve_task(self, task_key: str) -> Optional[str]:
        """
        根据任务键名解析 task_id。
        task_key 格式：'项目名-任务名' 或直接是任务名。

        核心逻辑：先定位项目，再在项目内搜索任务。
        """
        # 第一步：精确匹配配置（同时验证 task_id 仍有效，防止任务在 Teambition 中被删除或改名导致 ID 失效）
        tasks_map = self.config.get("tasks", {})
        if task_key in tasks_map:
            task_id = tasks_map[task_key]
            task_detail = self._lookup_task_by_id(task_id)
            if task_detail:
                current_name = task_detail.get("content", "")
                # 检测改名：若当前任务名不包含在 config key 中，说明 Teambition 中已改名
                if current_name and current_name.lower() not in task_key.lower():
                    print(f"  [提示] 配置键 '{task_key}' 对应的任务名称在 Teambition 中已更新为 '{current_name}'，建议更新 config.json 键名")
                return task_id
            else:
                print(f"  [警告] 配置键 '{task_key}' 对应的任务 (ID: {task_id}) 在 Teambition 中已不存在，将尝试重新搜索")
                # 不 return，继续后面的搜索流程

        # 第二步：尝试拆分 "项目名-任务名"，先定位项目再搜任务
        # 从右往左尝试不同的分割点（因为项目名和任务名本身可能含有 '-'）
        if "-" in task_key:
            parts = task_key.split("-")
            # 尝试不同的分割位置：第1个-、第2个-、...
            for split_at in range(1, len(parts)):
                project_part = "-".join(parts[:split_at])
                task_part = "-".join(parts[split_at:])

                project_id = self.resolve_project(project_part)
                if project_id:
                    print(f"  定位到项目: {project_part} ({project_id})")
                    results = self.search_tasks_in_project(project_id, task_part)
                    if len(results) == 1:
                        task_name = results[0].get('content') or results[0].get('name', '')
                        print(f"  定位到任务: {task_name} ({results[0]['id']})")
                        return results[0]["id"]
                    elif len(results) > 1:
                        print(f"  在项目 '{project_part}' 下找到多个匹配 '{task_part}' 的任务:")
                        for r in results:
                            r_name = r.get('content') or r.get('name', '')
                            print(f"    {r.get('id', '')}  {r_name}")
                        return None
                    else:
                        print(f"  在项目 '{project_part}' 下未找到匹配 '{task_part}' 的任务")
                        return None

        # 第三步：无项目上下文，在 config 配置的相关项目中搜索（不跨所有项目）
        relevant_pids = self._get_relevant_project_ids()
        if not relevant_pids:
            return None
        for pid in relevant_pids:
            results = self.search_tasks_in_project(pid, task_key)
            if len(results) == 1:
                task_name = results[0].get('content') or results[0].get('name', '')
                print(f"  定位到任务: {task_name} ({results[0]['id']})")
                return results[0]["id"]
            elif len(results) > 1:
                print(f"  在项目 {pid} 下找到多个匹配 '{task_key}' 的任务:")
                for r in results:
                    r_name = r.get('content') or r.get('name', '')
                    print(f"    {r.get('id', '')}  {r_name}")
                return None
        return None

    def resolve_project(self, name: str) -> Optional[str]:
        """根据项目名解析 project_id"""
        projects_map = self.config.get("projects", {})
        if name in projects_map:
            return projects_map[name]

        results = self.search("projects", name)
        if len(results) == 1:
            return results[0]["id"]
        elif len(results) > 1:
            print(f"找到多个匹配 '{name}' 的项目:")
            for r in results:
                print(f"  {r.get('id', '')}  {r.get('name', '')}")
            return None
        return None

    # ── 刷新 ──

    def refresh(self, entity_type: str = "all"):
        """刷新缓存（仅成员和项目，任务不缓存）"""
        if entity_type in ("all", "members"):
            print("刷新成员列表...")
            members = self.get_members(force_refresh=True)
            print(f"  缓存了 {len(members)} 个成员")

        if entity_type in ("all", "projects"):
            print("刷新项目列表...")
            projects = self.get_projects(force_refresh=True)
            print(f"  缓存了 {len(projects)} 个项目")

        print("缓存刷新完成。")


def main():
    parser = argparse.ArgumentParser(description="Teambition 缓存管理")
    parser.add_argument("--config", help="配置文件路径", default=None)

    sub = parser.add_subparsers(dest="action", help="操作类型")

    # 刷新缓存
    p1 = sub.add_parser("refresh", help="刷新缓存（成员、项目）")
    p1.add_argument("--type", default="all", choices=["all", "members", "projects"],
                     help="刷新类型（任务不缓存，实时查询）")

    # 搜索
    p2 = sub.add_parser("search", help="模糊搜索")
    p2.add_argument("--type", required=True, choices=["members", "projects", "tasks"],
                     help="搜索类型")
    p2.add_argument("--keyword", required=True, help="搜索关键词")

    # 列出缓存状态
    sub.add_parser("status", help="查看缓存状态")

    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        return

    cache = TBCache(config_path=args.config)

    if args.action == "refresh":
        cache.refresh(args.type)

    elif args.action == "search":
        results = cache.search(args.type, args.keyword)
        if not results:
            print(f"未找到匹配 '{args.keyword}' 的{args.type}")
        else:
            print(f"找到 {len(results)} 个结果:")
            for r in results:
                name = r.get("name", "") or r.get("content", "")
                print(f"  {r.get('id', '')}  {name}")

    elif args.action == "status":
        _ensure_cache_dir()
        import glob as glob_mod
        for f in sorted(glob_mod.glob(os.path.join(CACHE_DIR, "*.json"))):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                age_h = (time.time() - data.get("timestamp", 0)) / 3600
                count = len(data.get("items", []))
                expired = " (已过期)" if age_h > CACHE_TTL / 3600 else ""
                print(f"  {os.path.basename(f)}: {count} 条, {age_h:.1f}h 前更新{expired}")
            except (json.JSONDecodeError, IOError):
                print(f"  {os.path.basename(f)}: 读取失败")


if __name__ == "__main__":
    main()
