#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
出差补助天数计算器

规则（见 references/账单分类与勾稽规则.md 第五节）：
  1. 出发日 = 0.5 天，返回日 = 0.5 天，中间整日 = 1.0 天
  2. 周末（周六/周日）默认剔除，计 0 天
  3. 法定节假日默认不自动扣减，但会输出提醒请用户二次确认（--deduct-holiday 可直接扣）
  4. 调休上班日（日历中标记为 workday）逢周末时按工作日计入

用法：
  python3 calc_subsidy.py 2026-07-20 2026-08-06
  python3 calc_subsidy.py 2026-07-20 2026-08-06 --rate 75 --fx 7.84
  python3 calc_subsidy.py 2026-09-18 2026-10-08 --deduct-holiday
"""
import argparse
import os
import sys
from datetime import date, timedelta

WD = ["一", "二", "三", "四", "五", "六", "日"]
DEFAULT_CAL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "references", "节假日日历.md"
)


def parse_date(s):
    return date(*map(int, s.replace("/", "-").split("-")))


def load_calendar(path):
    """从 节假日日历.md 的 DATA 区块读取节假日/调休安排。"""
    hol, work = {}, {}
    if not os.path.exists(path):
        return hol, work
    lines = open(path, encoding="utf-8").read().splitlines()
    inside = False
    for ln in lines:
        ln = ln.strip()
        if ln == "<!-- DATA:START -->":
            inside = True
            continue
        if ln == "<!-- DATA:END -->":
            break
        if inside and ln and not ln.startswith("#"):
            parts = [p.strip() for p in ln.split(",")]
            if len(parts) < 4:
                continue
            s, e, name, kind = parse_date(parts[0]), parse_date(parts[1]), parts[2], parts[3]
            d = s
            while d <= e:
                (hol if kind == "holiday" else work)[d] = name
                d += timedelta(days=1)
    return hol, work


def calculate(start, end, rate=75.0, fx=None, deduct_holiday=False,
              weekend_travel_day=False, holidays=None, workdays=None):
    holidays = holidays or {}
    workdays = workdays or {}
    rows, total, hits = [], 0.0, []
    cur = start
    while cur <= end:
        nominal = 0.5 if cur in (start, end) else 1.0
        weekend = cur.weekday() >= 5
        hol_name = holidays.get(cur)
        is_workday_override = cur in workdays and weekend
        is_edge = cur in (start, end)

        count, reason = nominal, ""
        if holiday_deduct := (hol_name and deduct_holiday):
            count, reason = 0.0, f"法定节假日（{hol_name}），已扣除"
        elif weekend and is_workday_override:
            count, reason = nominal, f"周末但为调休上班日，计入"
        elif weekend:
            if is_edge and weekend_travel_day:
                count, reason = nominal, "周末（出发/落地日按开关仍计半天）"
            else:
                count, reason = 0.0, "周末，不计入"
        if hol_name and not deduct_holiday and not (weekend and not is_workday_override):
            hits.append((cur, hol_name))
        total += count
        rows.append((cur, WD[cur.weekday()], nominal, count, reason))
        cur += timedelta(days=1)
    return rows, total, total * rate, (total * rate * fx if fx else None), hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--rate", type=float, default=75.0, help="日标准 USD/天，默认 75")
    ap.add_argument("--fx", type=float, default=None, help="USD→HKD 汇率，如 7.84")
    ap.add_argument("--deduct-holiday", action="store_true", help="节假日自动扣除，不再提醒")
    ap.add_argument("--weekend-travel-day", action="store_true",
                    help="出发/落地日逢周末时仍计半天（默认不计）")
    ap.add_argument("--calendar", default=DEFAULT_CAL, help="节假日日历文件路径")
    a = ap.parse_args()

    start, end = parse_date(a.start), parse_date(a.end)
    if end < start:
        sys.exit("返回日期早于出发日期")

    hol, work = load_calendar(a.calendar)
    rows, days, usd, hkd, hits = calculate(
        start, end, a.rate, a.fx, a.deduct_holiday, a.weekend_travel_day, hol, work)

    print(f"\n区间 {start} → {end}   日历天数 {(end - start).days + 1} 天"
          f"   日标准 USD {a.rate:g}/天\n")
    print(f"{'日期':<12}{'星期':<6}{'名义':>6}{'计入':>7}   说明")
    print("-" * 62)
    for d, w, n, c, r in rows:
        mark = "  " if c > 0 else "· "
        print(f"{mark}{d}   周{w}  {n:>5.1f}{c:>7.1f}   {r}")
    print("-" * 62)
    print(f"计入天数：{days:g} 天")
    print(f"补助金额：USD {usd:,.2f}" + (f"  →  HKD {hkd:,.2f}（汇率 {a.fx}）" if hkd else ""))

    if hits:
        print("\n⚠️  出差区间跨越法定节假日，请二次确认是否计入：")
        for d, n in hits:
            print(f"    - {d}（周{WD[d.weekday()]}）{n}")
        print("    当前口径：节假日未扣除。若公司规定不发，请加 --deduct-holiday 重算。")
    else:
        print("\n✅ 区间内无法定节假日。")


if __name__ == "__main__":
    main()
