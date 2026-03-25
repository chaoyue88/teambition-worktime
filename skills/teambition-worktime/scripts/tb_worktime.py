#!/usr/bin/env python3
"""
Teambition 工时操作模块
支持计划工时（按周填报）、实际工时记录、查询、批量填报等功能。
"""

import argparse
import json
import sys
import time
import os
from datetime import datetime, date, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tb_auth import TeambitionClient, load_config
from tb_cache import TBCache


def hours_to_ms(hours: float) -> int:
    """小时转毫秒（API 层面使用毫秒）"""
    return int(hours * 3600000)


def ms_to_hours(ms: int) -> float:
    """毫秒转小时"""
    return round(ms / 3600000, 2)


def format_date(d: str = None) -> str:
    """格式化日期为 ISO 8601，默认今天"""
    if d is None:
        d = date.today().isoformat()
    if len(d) == 10:
        d = f"{d}T00:00:00.000Z"
    return d


def get_weekdays(reference: str = "current") -> list:
    """
    获取一周的工作日（周一到周五）日期列表。

    Args:
        reference: "current" 本周, "next" 下周, 或 "YYYY-MM-DD" 指定某周
    Returns:
        ["2026-03-23", "2026-03-24", ...] 共 5 个工作日
    """
    if reference == "current":
        today = date.today()
    elif reference == "next":
        today = date.today() + timedelta(weeks=1)
    else:
        today = date.fromisoformat(reference)

    # 找到这一周的周一
    monday = today - timedelta(days=today.weekday())
    return [(monday + timedelta(days=i)).isoformat() for i in range(5)]


def get_date_range(start: str, end: str, weekdays_only: bool = True) -> list:
    """获取日期范围内的日期列表"""
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    dates = []
    current = start_d
    while current <= end_d:
        if not weekdays_only or current.weekday() < 5:
            dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


class WorktimeManager:
    """工时管理器"""

    def __init__(self, config_path: str = None):
        self.cache = TBCache(config_path=config_path)
        self.client = self.cache.client
        self.config = self.cache.config
        self.org_id = self.config["organization_id"]

    # ── 名称解析（代理到缓存） ──

    def resolve_user(self, name: str) -> str:
        """解析人名到 user_id，失败时抛异常"""
        uid = self.cache.resolve_user(name)
        if not uid:
            raise ValueError(f"无法解析用户 '{name}'，请检查配置或使用搜索功能")
        return uid

    def resolve_task(self, task_key: str) -> str:
        """解析任务键名到 task_id，失败时抛异常"""
        tid = self.cache.resolve_task(task_key)
        if not tid:
            raise ValueError(f"无法解析任务 '{task_key}'，请检查配置或使用搜索功能")
        return tid

    # ── 计划工时 ──

    def set_planned_hours(self, task_id: str, hours: float) -> dict:
        """设置任务的计划工时（预估 estimatedTime）"""
        ms = hours_to_ms(hours)
        result = self.client.post(
            "/api/task/update",
            data={"taskId": task_id, "estimatedTime": ms},
        )
        print(f"  [OK] 计划工时已设置: {hours}h (任务 {task_id})")
        return result

    def get_planned_hours(self, task_id: str) -> float:
        """查询任务的计划工时"""
        result = self.client.get("/api/task/info", params={"taskId": task_id})
        task_data = result.get("result", {}) if isinstance(result, dict) else {}
        estimated = (task_data or {}).get("estimatedTime", 0) or 0
        return ms_to_hours(estimated)

    # ── 计划工时记录（plantime） ──

    def get_planned_records(self, task_id: str) -> list:
        """查询任务的所有计划工时记录"""
        result = self.client.get(
            f"/api/plantime/list/task/{task_id}",
            params={"pageSize": 100},
        )
        return result.get("result", []) if isinstance(result, dict) else []

    def log_planned_hours(
        self,
        task_id: str,
        hours: float,
        user_id: str = None,
        work_date: str = None,
        description: str = "",
        _existing_records: list = None,
    ) -> dict:
        """
        记录计划工时。

        注意：plantime/create 是累加模式，重复调用会叠加工时。
        因此先查询是否已有该用户+日期的记录，已存在则跳过。

        _existing_records: 预查询的已有记录（批量填报时传入，避免重复查询）
        """
        if hours <= 0:
            raise ValueError(f"工时必须大于 0，当前值: {hours}")
        date_str = work_date or date.today().isoformat()
        if len(date_str) > 10:
            date_str = date_str[:10]
        operator_id = self.config.get("default_user_id", "")
        target_user_id = user_id or operator_id

        # 去重检查
        existing = _existing_records if _existing_records is not None else self.get_planned_records(task_id)
        for rec in (existing or []):
            rec_date = (rec.get("date", "") or "")[:10]
            rec_user = rec.get("userId", "")
            if rec_date == date_str and rec_user == target_user_id:
                existing_hours = ms_to_hours(rec.get("plantime", 0))
                print(f"  [SKIP] 已存在计划工时: {existing_hours}h @ {date_str} (用户 {target_user_id})")
                return rec

        data = {
            "objectId": task_id,
            "objectType": "task",
            "plantime": hours_to_ms(hours),
            "startDate": date_str,
            "endDate": date_str,
            "date": date_str,
            "userId": target_user_id,
            "submitterId": operator_id,
        }

        result = self.client.post(
            "/api/plantime/create",
            data=data,
            operator_id=operator_id,
        )
        # 新 API 返回 {code, result: [...]}
        err = result.get("errorMessage", "") if isinstance(result, dict) else ""
        if err and err != "获取计划工时成功":
            raise RuntimeError(f"计划工时创建失败: {err}")
        print(f"  [OK] 计划工时记录: {hours}h @ {date_str} (任务 {task_id})")
        return result

    # ── 实际工时 ──

    def log_actual_hours(
        self,
        task_id: str,
        hours: float,
        user_id: str = None,
        work_date: str = None,
        description: str = "",
        _existing_records: list = None,
    ) -> dict:
        """记录实际工时（自动去重：同用户同任务同日已有记录则跳过）"""
        if hours <= 0:
            raise ValueError(f"工时必须大于 0，当前值: {hours}")
        date_str = work_date or date.today().isoformat()
        # 确保日期格式为 YYYY-MM-DD
        if len(date_str) > 10:
            date_str = date_str[:10]
        op_id = user_id or self.config.get("default_user_id", "")

        # 去重检查（worktime/create 不会自动去重，重复调用会叠加）
        existing = _existing_records if _existing_records is not None else self.get_actual_hours(task_id)
        for rec in (existing or []):
            rec_date = (rec.get("date", "") or "")[:10]
            rec_user = rec.get("user_id", "")
            if rec_date == date_str and rec_user == op_id:
                existing_hours = rec.get("hours", 0)
                print(f"  [SKIP] 已存在实际工时: {existing_hours}h @ {date_str} (用户 {op_id})")
                return rec

        data = {
            "objectId": task_id,
            "objectType": "task",
            "worktime": hours_to_ms(hours),
            "date": date_str,
            "startDate": date_str,
            "endDate": date_str,
            "userId": op_id,
            "submitterId": op_id,
            "description": description,
        }

        result = self.client.post(
            "/api/worktime/create",
            data=data,
            operator_id=op_id,
        )
        # 新 API 返回 {code, result: [...]}
        if isinstance(result, dict) and result.get("errorMessage"):
            raise RuntimeError(f"工时创建失败: {result['errorMessage']}")
        print(f"  [OK] 工时记录: {hours}h @ {date_str} (任务 {task_id})")
        return result

    def get_actual_hours(self, task_id: str) -> list:
        """查询任务的工时记录（按任务）"""
        result = self.client.get(
            f"/api/worktime/list/task/{task_id}",
            params={"pageSize": 100},
        )
        records = result.get("result", []) if isinstance(result, dict) else []
        return [
            {
                "id": r.get("worktimeId"),
                "hours": ms_to_hours(r.get("worktime", 0)),
                "date": r.get("date", ""),
                "user_id": r.get("userId", ""),
                "task_id": r.get("objectId", task_id),
                "description": r.get("description", ""),
            }
            for r in (records or [])
        ]

    def get_planned_by_user(self, user_id: str, start_date: str, end_date: str) -> list:
        """按用户+日期范围查询计划工时（/api/plantime/query），一次获取全部记录"""
        all_records = []
        page_token = None
        while True:
            params = {"userId": user_id, "startDate": start_date, "endDate": end_date, "pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            result = self.client.get("/api/plantime/query", params=params)
            records = result.get("result", []) if isinstance(result, dict) else []
            all_records.extend(records or [])
            page_token = result.get("nextPageToken") if isinstance(result, dict) else None
            if not page_token or not records:
                break
            time.sleep(0.2)
        return all_records

    def get_actual_by_user(self, user_id: str, start_date: str, end_date: str) -> list:
        """按用户+日期范围查询实际工时（/api/worktime/query），一次获取全部记录"""
        all_records = []
        page_token = None
        while True:
            params = {"userId": user_id, "startDate": start_date, "endDate": end_date, "pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            result = self.client.get("/api/worktime/query", params=params)
            records = result.get("result", []) if isinstance(result, dict) else []
            all_records.extend(records or [])
            page_token = result.get("nextPageToken") if isinstance(result, dict) else None
            if not page_token or not records:
                break
            time.sleep(0.2)
        return all_records

    def _get_task_label(self, task_id: str) -> str:
        """将 task_id 解析为 '项目名-任务名' 标签，优先 config 映射，回退 API 查询"""
        # 先查 config tasks 反向映射（快速路径）
        for key, tid in self.config.get("tasks", {}).items():
            if tid == task_id:
                return key
        # 调 API 获取任务名和项目 ID（用 task/query?taskId= 兼容无 executor 的特殊任务）
        try:
            result = self.client.get("/api/task/query", params={"taskId": task_id})
            tasks = result.get("result", []) if isinstance(result, dict) else []
            task = tasks[0] if tasks else {}
            task_name = task.get("content", task_id)
            proj_id = task.get("projectId", "")
            proj_name = next((k for k, v in self.config.get("projects", {}).items() if v == proj_id), proj_id)
            return f"{proj_name}-{task_name}"
        except Exception:
            return task_id

    # ── 按周填写计划工时（核心功能） ──

    def fill_weekly_planned(
        self,
        user_names: list,
        task_entries: list,
        week: str = "current",
        delay: float = 0.3,
    ) -> dict:
        """
        按周填写计划工时（为每个工作日创建计划工时记录）。

        注意：计划工时通过 /api/plantime/create 创建每日记录，而非任务级 estimatedTime。
        计算方式：每个工作日创建一条计划工时记录

        Args:
            user_names: 人名列表 ["李明", "王芳"]
            task_entries: 任务条目 [{"key": "项目-任务", "hours": 1.0}, ...]  # hours 是每日计划工时
            week: "current", "next", 或 "YYYY-MM-DD"
            delay: 请求间隔秒数

        Returns:
            {"success": N, "failed": N, "errors": [...], "details": [...]}
        """
        weekdays = get_weekdays(week)
        results = {"success": 0, "failed": 0, "errors": [], "details": []}

        # 预解析所有 user_id 和 task_id
        user_ids = {}
        for name in user_names:
            try:
                user_ids[name] = self.resolve_user(name)
            except ValueError as e:
                results["errors"].append(str(e))
                results["failed"] += len(task_entries) * len(weekdays)

        task_ids = {}
        for entry in task_entries:
            try:
                task_ids[entry["key"]] = self.resolve_task(entry["key"])
            except ValueError as e:
                results["errors"].append(str(e))
                results["failed"] += len(user_names) * len(weekdays)

        # 预查询已有 plantime 记录（每个任务查一次，避免 N*M 次查询）
        existing_by_task = {}
        for entry in task_entries:
            tid = task_ids.get(entry["key"])
            if tid:
                existing_by_task[tid] = self.get_planned_records(tid)

        print(f"\n{'='*60}")
        print(f"填写计划工时: {', '.join(user_names)}")
        print(f"周期: {weekdays[0]} ~ {weekdays[-1]} ({len(weekdays)} 个工作日)")
        print(f"任务: {len(task_entries)} 个")
        print(f"{'='*60}")

        for name in user_names:
            uid = user_ids.get(name)
            if not uid:
                continue

            print(f"\n-- {name} --")
            for entry in task_entries:
                tid = task_ids.get(entry["key"])
                if not tid:
                    continue

                hours = entry["hours"]
                existing = existing_by_task.get(tid, [])
                for day in weekdays:
                    try:
                        self.log_planned_hours(
                            task_id=tid,
                            hours=hours,
                            user_id=uid,
                            work_date=day,
                            _existing_records=existing,
                        )
                        results["success"] += 1
                        results["details"].append({
                            "user": name, "task": entry["key"],
                            "date": day, "hours": hours, "status": "ok",
                        })
                    except Exception as e:
                        error_msg = f"{name}/{entry['key']}/{day}: {e}"
                        print(f"  [FAIL] {error_msg}")
                        results["errors"].append(error_msg)
                        results["failed"] += 1
                        results["details"].append({
                            "user": name, "task": entry["key"],
                            "date": day, "hours": hours, "status": "failed",
                        })

                    if delay:
                        time.sleep(delay)

        total = results["success"] + results["failed"]
        print(f"\n{'='*60}")
        print(f"完成: 成功 {results['success']}/{total}, 失败 {results['failed']}/{total}")
        if results["errors"]:
            print(f"错误详情:")
            for err in results["errors"]:
                print(f"  - {err}")
        print(f"{'='*60}")

        return results

    # ── 按日期范围填写计划工时 ──

    def fill_range_planned(
        self,
        user_names: list,
        task_entries: list,
        start_date: str,
        end_date: str,
        delay: float = 0.3,
    ) -> dict:
        """
        按日期范围填写计划工时。

        Args:
            user_names: 人名列表
            task_entries: [{"key": "项目-任务", "hours": 1.0}, ...]  # hours 是每日计划工时
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            delay: 请求间隔
        """
        dates = get_date_range(start_date, end_date, weekdays_only=True)
        results = {"success": 0, "failed": 0, "errors": [], "details": []}

        user_ids = {}
        for name in user_names:
            try:
                user_ids[name] = self.resolve_user(name)
            except ValueError as e:
                results["errors"].append(str(e))

        task_ids = {}
        for entry in task_entries:
            try:
                task_ids[entry["key"]] = self.resolve_task(entry["key"])
            except ValueError as e:
                results["errors"].append(str(e))

        # 预查询已有 plantime 记录
        existing_by_task = {}
        for entry in task_entries:
            tid = task_ids.get(entry["key"])
            if tid:
                existing_by_task[tid] = self.get_planned_records(tid)

        print(f"\n{'='*60}")
        print(f"填写计划工时: {', '.join(user_names)}")
        print(f"日期: {start_date} ~ {end_date} ({len(dates)} 个工作日)")
        print(f"任务: {len(task_entries)} 个")
        print(f"{'='*60}")

        for name in user_names:
            uid = user_ids.get(name)
            if not uid:
                continue

            print(f"\n-- {name} --")
            for entry in task_entries:
                tid = task_ids.get(entry["key"])
                if not tid:
                    continue

                hours = entry["hours"]
                existing = existing_by_task.get(tid, [])
                for day in dates:
                    try:
                        self.log_planned_hours(
                            task_id=tid,
                            hours=hours,
                            user_id=uid,
                            work_date=day,
                            _existing_records=existing,
                        )
                        results["success"] += 1
                        results["details"].append({
                            "user": name, "task": entry["key"],
                            "date": day, "hours": hours, "status": "ok",
                        })
                    except Exception as e:
                        error_msg = f"{name}/{entry['key']}/{day}: {e}"
                        print(f"  [FAIL] {error_msg}")
                        results["errors"].append(error_msg)
                        results["failed"] += 1
                        results["details"].append({
                            "user": name, "task": entry["key"],
                            "date": day, "hours": hours, "status": "failed",
                        })

                    if delay:
                        time.sleep(delay)

        total = results["success"] + results["failed"]
        print(f"\n{'='*60}")
        print(f"完成: 成功 {results['success']}/{total}, 失败 {results['failed']}/{total}")
        if results["errors"]:
            print(f"错误详情:")
            for err in results["errors"]:
                print(f"  - {err}")
        print(f"{'='*60}")

        return results

    # ── 按日期范围填写实际工时 ──

    def fill_range_actual(
        self,
        user_names: list,
        task_entries: list,
        start_date: str,
        end_date: str,
        delay: float = 0.3,
    ) -> dict:
        """
        按日期范围填写实际工时。

        Args:
            user_names: 人名列表
            task_entries: [{"key": "项目-任务", "hours": 1.0}, ...]
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            delay: 请求间隔

        Returns:
            同 fill_weekly_planned
        """
        dates = get_date_range(start_date, end_date, weekdays_only=True)
        results = {"success": 0, "failed": 0, "errors": [], "details": []}

        user_ids = {}
        for name in user_names:
            try:
                user_ids[name] = self.resolve_user(name)
            except ValueError as e:
                results["errors"].append(str(e))

        task_ids = {}
        for entry in task_entries:
            try:
                task_ids[entry["key"]] = self.resolve_task(entry["key"])
            except ValueError as e:
                results["errors"].append(str(e))

        # 预查询已有实际工时记录（每个任务查一次，避免重复提交）
        existing_by_task = {}
        for entry in task_entries:
            tid = task_ids.get(entry["key"])
            if tid:
                existing_by_task[tid] = self.get_actual_hours(tid)

        print(f"\n{'='*60}")
        print(f"填写实际工时: {', '.join(user_names)}")
        print(f"日期: {start_date} ~ {end_date} ({len(dates)} 个工作日)")
        print(f"{'='*60}")

        for name in user_names:
            uid = user_ids.get(name)
            if not uid:
                continue

            print(f"\n-- {name} --")
            for entry in task_entries:
                tid = task_ids.get(entry["key"])
                if not tid:
                    continue

                hours = entry["hours"]
                for day in dates:
                    try:
                        self.log_actual_hours(
                            task_id=tid,
                            hours=hours,
                            user_id=uid,
                            work_date=day,
                            description="",
                            _existing_records=existing_by_task.get(tid, []),
                        )
                        results["success"] += 1
                        results["details"].append({
                            "user": name, "task": entry["key"],
                            "date": day, "hours": hours, "status": "ok",
                        })
                    except Exception as e:
                        error_msg = f"{name}/{entry['key']}/{day}: {e}"
                        print(f"  [FAIL] {error_msg}")
                        results["errors"].append(error_msg)
                        results["failed"] += 1
                        results["details"].append({
                            "user": name, "task": entry["key"],
                            "date": day, "hours": hours, "status": "failed",
                        })

                    if delay:
                        time.sleep(delay)

        total = results["success"] + results["failed"]
        print(f"\n完成: 成功 {results['success']}/{total}, 失败 {results['failed']}/{total}")
        if results["errors"]:
            print("错误详情:")
            for err in results["errors"]:
                print(f"  - {err}")
        return results

    # ── 按周填写实际工时 ──

    def fill_weekly_actual(
        self,
        user_names: list,
        task_entries: list,
        week: str = "current",
        delay: float = 0.3,
    ) -> dict:
        """
        按周填写实际工时（复用 fill_range_actual，自动计算工作日）。

        Args:
            user_names: 人名列表
            task_entries: [{"key": "项目-任务", "hours": 1.0}, ...]
            week: "current", "next", 或 "YYYY-MM-DD"
            delay: 请求间隔秒数
        """
        weekdays = get_weekdays(week)
        return self.fill_range_actual(
            user_names, task_entries, weekdays[0], weekdays[-1], delay
        )

    # ── 计划工时扫描（跨项目，不依赖 config tasks） ──

    def _scan_planned_items(self, user_ids: dict, dates: set) -> list:
        """
        通过 /api/plantime/query 按用户+日期范围直接获取计划工时，
        不依赖 config tasks 字典，也不需要遍历项目/任务列表。

        Args:
            user_ids: {人名: user_id}
            dates: 目标日期集合 {"2026-03-23", ...}

        Returns:
            [(task_label, task_id, user_name, user_id, date, hours), ...]
            task_label 格式: "项目名-任务名"
        """
        if not dates:
            return []
        start_date = min(dates)
        end_date = max(dates)
        items = []

        for name, uid in user_ids.items():
            records = self.get_planned_by_user(uid, start_date, end_date)

            # 批量解析涉及的 task_id → label（每个 task_id 只查一次）
            task_ids = list({r.get("objectId", "") for r in records if r.get("objectId")})
            task_label_map = {tid: self._get_task_label(tid) for tid in task_ids}

            for rec in records:
                task_id = rec.get("objectId", "")
                rec_date = rec.get("date", "")[:10]
                if rec_date not in dates:
                    continue
                hours = rec.get("plantime", 0) / 3600000
                if hours <= 0:
                    continue
                label = task_label_map.get(task_id, task_id)
                items.append((label, task_id, name, uid, rec_date, hours))

        return items

    # ── 按计划工时填写实际工时 ──

    def fill_actual_from_planned(
        self,
        user_names: list,
        start_date: str,
        end_date: str,
        delay: float = 0.3,
    ) -> dict:
        """
        按计划工时填写实际工时。

        自动读取指定用户在日期范围内的计划工时记录，
        对尚未填写实际工时的条目进行填报，已有实际工时则跳过。

        Args:
            user_names: 人名列表
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            delay: 请求间隔秒数

        Returns:
            {"success": N, "skipped": N, "failed": N, "errors": [...], "details": [...]}
        """
        dates = set(get_date_range(start_date, end_date, weekdays_only=True))
        results = {"success": 0, "skipped": 0, "failed": 0, "errors": [], "details": []}

        # 解析用户
        user_ids = {}
        for name in user_names:
            try:
                user_ids[name] = self.resolve_user(name)
            except ValueError as e:
                results["errors"].append(str(e))

        if not user_ids:
            return results

        # 通过 API 扫描所有项目下的任务计划工时，不依赖 config tasks
        planned_items = self._scan_planned_items(user_ids, dates)

        if not planned_items:
            print("未找到符合条件的计划工时记录")
            return results

        # 通过 /api/worktime/query 一次性预查各用户已有实际工时（比逐任务查更高效）
        existing_set = set()  # (user_id, task_id, date)
        for name, uid in user_ids.items():
            records = self.get_actual_by_user(uid, start_date, end_date)
            for r in records:
                existing_set.add((uid, r.get("objectId", "") or r.get("task_id", ""), r.get("date", "")[:10]))

        print(f"\n{'='*60}")
        print(f"按计划工时填写实际工时: {', '.join(user_names)}")
        print(f"日期: {start_date} ~ {end_date}")
        print(f"计划条目: {len(planned_items)} 条")
        print(f"{'='*60}")

        for task_key, task_id, name, uid, day, hours in sorted(planned_items, key=lambda x: (x[4], x[0])):
            already = (uid, task_id, day) in existing_set
            if already:
                print(f"  [SKIP] {day}  {task_key}: 已有实际工时")
                results["skipped"] += 1
                results["details"].append({
                    "user": name, "task": task_key,
                    "date": day, "hours": hours, "status": "skipped",
                })
                continue

            try:
                self.log_actual_hours(
                    task_id=task_id,
                    hours=hours,
                    user_id=uid,
                    work_date=day,
                    description="",
                    _existing_records=[],  # 已在上方用 existing_set 去重，此处传空跳过内部查询
                )
                existing_set.add((uid, task_id, day))  # 更新本地已填集合防止批量内重复
                results["success"] += 1
                results["details"].append({
                    "user": name, "task": task_key,
                    "date": day, "hours": hours, "status": "ok",
                })
            except Exception as e:
                error_msg = f"{name}/{task_key}/{day}: {e}"
                print(f"  [FAIL] {error_msg}")
                results["errors"].append(error_msg)
                results["failed"] += 1
                results["details"].append({
                    "user": name, "task": task_key,
                    "date": day, "hours": hours, "status": "failed",
                })

            if delay:
                time.sleep(delay)

        total = results["success"] + results["failed"]
        print(f"\n{'='*60}")
        print(f"完成: 填报 {results['success']}, 跳过(已填) {results['skipped']}, 失败 {results['failed']}")
        if results["errors"]:
            print("错误详情:")
            for err in results["errors"]:
                print(f"  - {err}")
        print(f"{'='*60}")

        return results

    # ── 批量操作（通用） ──

    def batch_log_hours(self, entries: list, delay: float = 0.3) -> dict:
        """
        批量记录工时（通用接口）。

        Args:
            entries: [{"task_id": "xxx", "hours": 1.0, "user_id": "...", "date": "...", "description": ""}, ...]
        """
        results = {"success": 0, "failed": 0, "errors": []}

        for i, entry in enumerate(entries):
            try:
                self.log_actual_hours(
                    task_id=entry["task_id"],
                    hours=entry["hours"],
                    user_id=entry.get("user_id"),
                    work_date=entry.get("date"),
                    description=entry.get("description", ""),
                )
                results["success"] += 1
            except Exception as e:
                error_msg = f"条目 {i+1} (任务 {entry.get('task_id')}): {e}"
                print(f"  [FAIL] {error_msg}")
                results["errors"].append(error_msg)
                results["failed"] += 1

            if delay and i < len(entries) - 1:
                time.sleep(delay)

        print(f"\n批量填报完成: 成功 {results['success']}, 失败 {results['failed']}")
        return results

    # ── 查询 ──

    def list_projects(self) -> list:
        return self.cache.get_projects()

    def search_tasks(self, keyword: str, project_id: str = None) -> list:
        if project_id:
            tasks = self.cache.get_tasks(project_id)
            keyword_lower = keyword.lower()
            return [t for t in tasks if keyword_lower in t.get("content", "").lower()]
        return self.cache.search("tasks", keyword)

    def list_members(self) -> list:
        return self.cache.get_members()


def parse_task_entries(tasks_str: str) -> list:
    """
    解析任务字符串为条目列表。
    格式: "任务键名:工时,任务键名:工时"
    示例: "技术中台项目-平台日常管理:1,技术中台项目-基础设施运维:1"
    """
    entries = []
    for part in tasks_str.split(","):
        part = part.strip()
        if ":" not in part:
            raise ValueError(f"任务格式错误 '{part}'，需要 '任务名:工时' 格式")
        key, hours_str = part.rsplit(":", 1)
        entries.append({"key": key.strip(), "hours": float(hours_str.strip())})
    return entries


def main():
    parser = argparse.ArgumentParser(description="Teambition 工时管理工具")
    parser.add_argument("--config", help="配置文件路径", default=None)

    sub = parser.add_subparsers(dest="action", help="操作类型")

    # 按周填写计划工时
    p1 = sub.add_parser("fill-weekly-planned", help="按周填写计划工时（周一到周五，或指定日期范围）")
    p1.add_argument("--users", required=True, help="人名，逗号分隔")
    p1.add_argument("--tasks", required=True, help="任务:工时，逗号分隔。如 '项目-任务:1,项目-任务:8'")
    p1.add_argument("--week", default="current", help="current/next/YYYY-MM-DD")
    p1.add_argument("--start", help="开始日期 YYYY-MM-DD（与 --end 配合替代 --week）")
    p1.add_argument("--end", help="结束日期 YYYY-MM-DD")

    # 记录单条实际工时
    p2 = sub.add_parser("log-actual", help="记录单条实际工时")
    p2.add_argument("--user", help="人名")
    p2.add_argument("--task-key", required=True, help="任务键名（项目-任务）")
    p2.add_argument("--hours", type=float, required=True, help="工时（小时）")
    p2.add_argument("--date", help="日期 YYYY-MM-DD，默认今天")
    p2.add_argument("--desc", default="", help="描述")

    # 按日期范围填写计划工时
    p3a = sub.add_parser("fill-range-planned", help="按日期范围填写计划工时")
    p3a.add_argument("--users", required=True, help="人名，逗号分隔")
    p3a.add_argument("--tasks", required=True, help="任务:工时，逗号分隔")
    p3a.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    p3a.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")

    # 按周填写实际工时
    p_wa = sub.add_parser("fill-weekly-actual", help="按周填写实际工时（周一到周五）")
    p_wa.add_argument("--users", required=True, help="人名，逗号分隔")
    p_wa.add_argument("--tasks", required=True, help="任务:工时，逗号分隔")
    p_wa.add_argument("--week", default="current", help="current/next/YYYY-MM-DD")

    # 按计划工时填写实际工时（自动读取计划，跳过已填）
    p_afp = sub.add_parser("fill-actual-from-planned",
                            help="按计划工时填写实际工时（自动读取计划，跳过已填条目）")
    p_afp.add_argument("--users", required=True, help="人名，逗号分隔")
    p_afp.add_argument("--week", default="current", help="current/next/YYYY-MM-DD，默认本周")
    p_afp.add_argument("--start", help="开始日期 YYYY-MM-DD（与 --end 配合替代 --week）")
    p_afp.add_argument("--end", help="结束日期 YYYY-MM-DD")
    p_afp.add_argument("--include-today", action="store_true",
                        help="结束日期 cap 到今天（默认 cap 到昨天）")

    # 按日期范围填写实际工时
    p3 = sub.add_parser("fill-range-actual", help="按日期范围填写实际工时")
    p3.add_argument("--users", required=True, help="人名，逗号分隔")
    p3.add_argument("--tasks", required=True, help="任务:工时，逗号分隔")
    p3.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    p3.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")

    # 设置计划工时（预估）
    p4 = sub.add_parser("set-planned", help="设置任务预估工时")
    p4.add_argument("--task-key", required=True, help="任务键名")
    p4.add_argument("--hours", type=float, required=True, help="计划工时（小时）")

    # 查询计划工时（按用户+周）
    p_qp = sub.add_parser("query-planned", help="查询某用户某周的计划工时记录")
    p_qp.add_argument("--user", help="人名（默认使用 default_user_id）")
    p_qp.add_argument("--week", default="current", help="current/next/YYYY-MM-DD，默认本周")

    # 查询工时
    p5 = sub.add_parser("query", help="查询任务工时")
    p5.add_argument("--task-key", required=True, help="任务键名")

    # 列出项目
    sub.add_parser("list-projects", help="列出企业项目")

    # 搜索任务
    p7 = sub.add_parser("search-task", help="搜索任务")
    p7.add_argument("--keyword", required=True, help="搜索关键词")
    p7.add_argument("--project", help="限定项目名称")

    # 列出成员
    sub.add_parser("list-members", help="列出企业成员")

    # 批量填报（JSON 文件）
    p9 = sub.add_parser("batch", help="从 JSON 文件批量填报")
    p9.add_argument("--file", required=True, help="JSON 文件路径")

    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        return

    mgr = WorktimeManager(config_path=args.config)

    if args.action == "fill-weekly-planned":
        user_names = [n.strip() for n in args.users.split(",")]
        task_entries = parse_task_entries(args.tasks)
        # 如果指定了 --start/--end，使用日期范围模式
        if hasattr(args, "start") and args.start and hasattr(args, "end") and args.end:
            mgr.fill_range_planned(user_names, task_entries, args.start, args.end)
        else:
            mgr.fill_weekly_planned(user_names, task_entries, week=args.week)

    elif args.action == "log-actual":
        task_id = mgr.resolve_task(args.task_key)
        user_id = mgr.resolve_user(args.user) if args.user else None
        mgr.log_actual_hours(task_id, args.hours, user_id=user_id,
                             work_date=args.date, description=args.desc)

    elif args.action == "fill-range-planned":
        user_names = [n.strip() for n in args.users.split(",")]
        task_entries = parse_task_entries(args.tasks)
        mgr.fill_range_planned(user_names, task_entries, args.start, args.end)

    elif args.action == "fill-range-actual":
        user_names = [n.strip() for n in args.users.split(",")]
        task_entries = parse_task_entries(args.tasks)
        mgr.fill_range_actual(user_names, task_entries, args.start, args.end)

    elif args.action == "fill-weekly-actual":
        user_names = [n.strip() for n in args.users.split(",")]
        task_entries = parse_task_entries(args.tasks)
        mgr.fill_weekly_actual(user_names, task_entries, week=args.week)

    elif args.action == "fill-actual-from-planned":
        user_names = [n.strip() for n in args.users.split(",")]
        include_today = getattr(args, "include_today", False)
        cap_date = date.today().isoformat() if include_today else (date.today() - timedelta(days=1)).isoformat()
        if hasattr(args, "start") and args.start and hasattr(args, "end") and args.end:
            end = min(args.end, cap_date)
            mgr.fill_actual_from_planned(user_names, args.start, end)
        else:
            weekdays = get_weekdays(args.week)
            end = min(weekdays[-1], cap_date)
            mgr.fill_actual_from_planned(user_names, weekdays[0], end)

    elif args.action == "set-planned":
        task_id = mgr.resolve_task(args.task_key)
        mgr.set_planned_hours(task_id, args.hours)

    elif args.action == "query-planned":
        user_name = args.user or None
        if user_name:
            user_id = mgr.resolve_user(user_name)
            user_ids = {user_name: user_id}
        else:
            default_uid = mgr.config.get("default_user_id")
            if not default_uid:
                print("错误：未指定用户且配置中无 default_user_id")
                sys.exit(1)
            user_ids = {"(default)": default_uid}
        dates = set(get_weekdays(args.week))
        items = mgr._scan_planned_items(user_ids, dates)
        if items:
            for task_label, _, name, _, d, hours in sorted(items, key=lambda x: (x[4], x[0])):
                print(f"  {d}  {task_label}: {hours}h")
        else:
            print("未找到计划工时记录")

    elif args.action == "query":
        task_id = mgr.resolve_task(args.task_key)
        planned = mgr.get_planned_hours(task_id)
        actual_records = mgr.get_actual_hours(task_id)
        total_actual = sum(r["hours"] for r in actual_records)
        print(f"计划工时: {planned}h")
        print(f"实际工时: {total_actual}h ({len(actual_records)} 条记录)")
        for r in actual_records:
            print(f"   - {r['date'][:10]}: {r['hours']}h {r['description']}")

    elif args.action == "list-projects":
        projects = mgr.list_projects()
        for p in projects:
            print(f"  {p.get('id', '')}  {p.get('name', '')}")

    elif args.action == "search-task":
        project_id = None
        if args.project:
            project_id = mgr.cache.resolve_project(args.project)
        tasks = mgr.search_tasks(args.keyword, project_id=project_id)
        for t in tasks:
            done = "[done]" if t.get("isDone") else ""
            print(f"  {t.get('id', '')}  {t.get('content', '')} {done}")

    elif args.action == "list-members":
        members = mgr.list_members()
        for m in members:
            print(f"  {m['id']}  {m['name']}  {m.get('email', '')}")

    elif args.action == "batch":
        with open(args.file, "r", encoding="utf-8") as f:
            entries = json.load(f)
        mgr.batch_log_hours(entries)


if __name__ == "__main__":
    main()
