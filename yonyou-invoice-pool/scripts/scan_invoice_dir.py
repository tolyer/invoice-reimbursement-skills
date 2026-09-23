#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_invoice_dir.py —— 解析结构化发票目录，生成《票袋录入计划》骨架。

目录约定（由 invoice-organizer skill 归档产出）：

    发票目录-<行程区间>/
    ├── 01-飞机含专车-HKD2873.00/
    │   ├── ★票据本体/QQ邮箱-机票订单确认¥2484.pdf
    │   └── ☆附件/01-...png  02-...pdf ...
    ├── 06-补助-HKD9996.00/
    │   ├── ★票据本体/说明-手录行程无票据本体.txt
    │   └── ☆附件/01-...png
    └── 报销材料核验台账*.md（可选，优先作为字段来源）

用法：
    python3 scan_invoice_dir.py <发票目录> [--out plan.md] [--json plan.json]

输出：
    - 人类可读的计划表（stdout）
    - --out   写入 Markdown 计划骨架（含待确认清单）
    - --json  写入机器可读 JSON，供后续录入步骤直接消费

设计原则：
    - 只读取，绝不移动/重命名/删除任何原始文件。
    - 能从文件名推导的字段才推导；日期、发生地、备注一律留空标 TODO，
      交由台账或用户确认，绝不臆测。
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------- 常量与规则

# 行目录名： 01-飞机含专车-HKD2873.00
ROW_DIR_RE = re.compile(r"^(\d{1,2})[-–](.+?)[-–]HKD([\d,]+(?:\.\d{1,2})?)\s*$")

VOUCHER_DIR_MARK = "★票据本体"
ATTACH_DIR_MARK = "☆附件"

# 账单类型（平台 radio）：小票(非中国大陆) / 手录行程
# 消费类型（平台 radio）：飞机 / 住宿（境内/境外）/ 其他交通 / 补助 ...
CONSUME_RULES = [
    # (关键词正则, 账单类型, 消费类型)
    (r"补助|补贴|津贴", "手录行程", "补助"),
    (r"飞机|机票|航班|航空|含专车", "小票(非中国大陆)", "飞机"),
    (r"住宿|酒店|水单|客房", "小票(非中国大陆)", "住宿（境内/境外）"),
    (r"grab|打车|出租车|网约车|用车|专车|交通", "小票(非中国大陆)", "其他交通"),
    (r"火车|高铁|动车|铁路", "小票(非中国大陆)", "火车"),
    (r"餐饮|餐费|用餐", "小票(非中国大陆)", "餐饮"),
]

# 原币币种识别（按文件名线索，优先级从高到低）
CURRENCY_RULES = [
    (r"MYR|RM|马币|林吉特", "MYR"),
    (r"USD|美刀|美元|US\$", "USD"),
    (r"HKD|港币|HK\$", "HKD"),
    (r"CNY|RMB|¥|￥|人民币", "CNY"),
    (r"SGD|新币|新元", "SGD"),
]

# 汇率截图文件名中的数值：CNY0.8646 / MYR1.93 / USD7.84
RATE_RE = re.compile(
    r"(CNY|MYR|USD|HKD|SGD)\s*[:：]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)

# 附件里可识别为「汇率凭证」的文件
RATE_FILE_RE = re.compile(r"汇率|牌价|汇率参考|exchange|rate", re.IGNORECASE)


def detect_consume_type(desc: str):
    """从目录描述推断 (账单类型, 消费类型)。推断不出返回 (None, None)。"""
    for pattern, bill_type, consume_type in CONSUME_RULES:
        if re.search(pattern, desc, re.IGNORECASE):
            return bill_type, consume_type
    return None, None


def detect_currency(files):
    """从一组文件名中推断原币币种。返回 (币种, 命中文件名)。"""
    for pattern, cur in CURRENCY_RULES:
        for name in files:
            if re.search(pattern, name, re.IGNORECASE):
                return cur, name
    return None, None


def detect_rate(files):
    """从汇率截图中提取 (币种对, 数值, 文件名)。"""
    for name in files:
        if not RATE_FILE_RE.search(name):
            continue
        m = RATE_RE.search(name)
        if m:
            return m.group(1).upper(), float(m.group(2)), name
    # 放宽：文件名里直接带 币种+数字
    for name in files:
        m = RATE_RE.search(name)
        if m:
            return m.group(1).upper(), float(m.group(2)), name
    return None, None, None


def list_files(path):
    """列出目录下文件（不含子目录、隐藏文件），按文件名排序。"""
    if not os.path.isdir(path):
        return []
    out = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        if name.startswith(".") or os.path.isdir(full):
            continue
        out.append({"name": name, "path": os.path.abspath(full),
                    "ext": os.path.splitext(name)[1].lower()})
    return out


def find_ledger(root):
    """在目录根层（及父目录）找核验台账 md/xlsx。"""
    candidates = []
    for base in (root, os.path.dirname(root)):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            low = name.lower()
            if low.startswith("."):
                continue
            if re.search(r"台账|ledger|核验", name) and low.endswith((".md", ".xlsx", ".xls", ".csv")):
                candidates.append(os.path.abspath(os.path.join(base, name)))
    return candidates


# ---------------------------------------------------------------- 主解析

def scan(root):
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        raise SystemExit(f"[错误] 目录不存在: {root}")

    rows = []
    for entry in sorted(os.listdir(root)):
        full = os.path.join(root, entry)
        if entry.startswith(".") or not os.path.isdir(full):
            continue
        m = ROW_DIR_RE.match(entry)
        if not m:
            continue

        seq, desc, amount = m.group(1), m.group(2).strip(), m.group(3)
        hkd = float(amount.replace(",", ""))

        voucher_files, attach_files = [], []
        for sub in sorted(os.listdir(full)):
            subpath = os.path.join(full, sub)
            if not os.path.isdir(subpath):
                continue
            if VOUCHER_DIR_MARK in sub:
                voucher_files = list_files(subpath)
            elif ATTACH_DIR_MARK in sub:
                attach_files = list_files(subpath)

        # 票据本体：.txt 说明文件视为「无票据本体」
        real_vouchers = [f for f in voucher_files if f["ext"] not in (".txt", ".md")]
        note_only = [f for f in voucher_files if f["ext"] in (".txt", ".md")]

        bill_type, consume_type = detect_consume_type(desc)
        all_names = [f["name"] for f in real_vouchers + attach_files]
        currency, currency_hit = detect_currency(all_names)
        rate_cur, rate_val, rate_file = detect_rate([f["name"] for f in attach_files])

        rows.append({
            "seq": int(seq),
            "dir_name": entry,
            "desc": desc,
            "hkd_amount": round(hkd, 2),
            "bill_type": bill_type,               # 小票(非中国大陆) / 手录行程
            "consume_type": consume_type,         # 飞机 / 住宿（境内/境外）/ 其他交通 / 补助
            "src_currency": currency,
            "src_currency_hit": currency_hit,
            "rate": {"currency": rate_cur, "value": rate_val, "file": rate_file},
            "voucher": real_vouchers[0] if real_vouchers else None,
            "voucher_note": note_only[0]["name"] if note_only else None,
            "voucher_missing": not real_vouchers,
            "voucher_extra": real_vouchers[1:],   # 票据本体多于 1 个，需人工裁决
            "attachments": attach_files,
            # 以下字段无法从目录推导，必须人工/台账提供
            "invoice_date": None,                 # 开票日期 TODO
            "occur_date": None,                   # 发生日期 TODO
            "place": None,                        # 发生地  TODO
            "remark": None,                       # 备注    TODO
            "_todo": [],
        })

    rows.sort(key=lambda r: r["seq"])

    # 标注待确认项
    for r in rows:
        t = r["_todo"]
        if not r["bill_type"]:
            t.append("消费类型无法从目录名推断，需人工指定")
        if not r["src_currency"]:
            t.append("原币币种无法推断，需人工指定")
        if not r["rate"]["value"] and r["src_currency"] not in (None, "HKD"):
            t.append(f"缺少 {r['src_currency']} 汇率凭证截图")
        if r["voucher_missing"]:
            if r["bill_type"] == "手录行程":
                t.append("手录行程无票据本体（预期内，不需上传）")
            else:
                t.append("★票据本体为空，平台将拒绝保存，须补材料")
        if r["voucher_extra"]:
            t.append("★票据本体多于 1 个，平台只收 1 张，需人工裁决")
        if not r["attachments"]:
            t.append("☆附件为空，建议至少补充支付凭证")
        t.append("待填：开票日期 / 发生日期 / 发生地 / 备注")

    return {
        "root": root,
        "total_hkd": round(sum(r["hkd_amount"] for r in rows), 2),
        "row_count": len(rows),
        "attachment_count": sum(len(r["attachments"]) for r in rows),
        "ledger_files": find_ledger(root),
        "rows": rows,
    }


# ---------------------------------------------------------------- 渲染

def render_md(plan):
    L = []
    L.append("# 票袋录入计划（待确认）\n")
    L.append(f"- 源目录：`{plan['root']}`")
    L.append(f"- 行数：**{plan['row_count']}**　附件总数：**{plan['attachment_count']}**"
             f"　金额合计：**HK${plan['total_hkd']:,.2f}**")
    if plan["ledger_files"]:
        L.append("- 检出台账（优先作为字段来源）：")
        for p in plan["ledger_files"]:
            L.append(f"  - `{p}`")
    L.append("")
    L.append("## 一、逐行录入清单\n")
    L.append("| # | 目录 | 账单类型 | 消费类型 | 开票日期 | 发生日期 | 含税金额(HKD) | 原币 | 汇率 | 发生地 | 备注 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in plan["rows"]:
        rate = f"{r['rate']['value']}" if r["rate"]["value"] else "?"
        L.append(
            f"| {r['seq']} | {r['desc']} | {r['bill_type'] or '?'} | {r['consume_type'] or '?'} | "
            f"TODO | TODO | {r['hkd_amount']:,.2f} | {r['src_currency'] or '?'} | {rate} | TODO | TODO |"
        )
    L.append(f"| | **合计** | | | | | **{plan['total_hkd']:,.2f}** | | | | |")
    L.append("")
    L.append("## 二、文件上传清单\n")
    for r in plan["rows"]:
        L.append(f"### {r['seq']}. {r['dir_name']}\n")
        v = r["voucher"]
        if v:
            L.append(f"- **票据本体（上传发票）**：`{v['name']}`")
        elif r["voucher_note"]:
            L.append(f"- **票据本体**：无（目录内说明文件：`{r['voucher_note']}`）")
        else:
            L.append("- **票据本体**：⚠️ 缺失")
        if r["attachments"]:
            L.append(f"- **附件（原文件目录，{len(r['attachments'])} 份）**：")
            for a in r["attachments"]:
                L.append(f"  - `{a['name']}`")
        else:
            L.append("- **附件**：⚠️ 无")
        L.append("")
    L.append("## 三、待确认 / 数据缺口\n")
    gap_found = False
    for r in plan["rows"]:
        if r["_todo"]:
            gap_found = True
            L.append(f"**{r['seq']}. {r['desc']}**")
            for t in r["_todo"]:
                L.append(f"- {t}")
            L.append("")
    if not gap_found:
        L.append("无。\n")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="解析结构化发票目录，生成票袋录入计划")
    ap.add_argument("dir", help="发票目录路径，如 /path/to/发票目录-0720-0806")
    ap.add_argument("--out", help="输出 Markdown 计划文件路径")
    ap.add_argument("--json", dest="json_out", help="输出 JSON 文件路径")
    args = ap.parse_args()

    plan = scan(args.dir)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print(f"[ok] JSON  -> {args.json_out}", file=sys.stderr)

    md = render_md(plan)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[ok] 计划  -> {args.out}", file=sys.stderr)
    else:
        print(md)


if __name__ == "__main__":
    main()
