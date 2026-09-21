# 云端盯盘器（GitHub Actions 版）——不依赖你的电脑
#   每 15 分钟跑一次：①算全池链条方向 ②看白名单开盘区间突破 ③命中就写信号与待答复
#   基准价（baseline）：在每个时段收盘后（15:02-15:12 / 23:02-23:12 北京时间）自动刷新并提交
#   作者注：新浪实时接口区分大小写，必须用大写代码（如 nf_MA2610）
import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "out"
BASE_CSV = DATA / "baseline.csv"          # 基准价（收盘后刷新）
MAIN_CSV = DATA / "主力合约.csv"
CHAIN_CSV = DATA / "品种产业链.csv"
LOG_CSV = OUT / "snapshot_log.csv"
SIG_CSV = OUT / "signals.csv"
BRIEF = OUT / "待答复_最新.txt"

# 白名单：品种 -> (链名, 主力合约, 乘数, 最小跳动, 单边手续费)
WHITE = {
    "甲醇": ("煤化工-氯碱", "MA2610", 10, 1.0, 2.0),
    "豆粕": ("油脂油料", "M2701", 10, 1.0, 1.5),
    "螺纹": ("黑色-成材", "RB2701", 10, 1.0, 3.1),
    "热卷": ("黑色-成材", "HC2701", 10, 1.0, 3.3),
    "淀粉": ("玉米-淀粉-养殖", "CS2611", 10, 1.0, 1.5),
}
MAX_RISK = 300
CST = timezone(timedelta(hours=8))


def push(title, body):
    """Server酱推送到微信（支持 Turbo 与 Server酱³ 两种 SendKey）"""
    key = (os.environ.get("SERVERCHAN_KEY") or "").strip()
    if not key:
        print("（未配置 SERVERCHAN_KEY，跳过推送）")
        return False
    urls = []
    m = re.match(r"sctp(\d+)t", key, re.I)
    if m:                                   # Server酱³: sctp<uid>t<token>
        urls.append("https://%s.push.ft07.com/send/%s.send" % (m.group(1), key))
    urls.append("https://sctapi.ftqq.com/%s.send" % key)   # Turbo
    for u in urls:
        try:
            data = urllib.parse.urlencode({"title": title[:32], "desp": body}).encode()
            req = urllib.request.Request(u, data=data,
                                         headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
            print("推送返回:", raw[:200])
            if '"code":0' in raw.replace(" ", ""):
                print("推送成功 ✓")
                return True
        except Exception as e:
            print("推送尝试失败(%s): %s" % (u.split("/")[2], str(e)[:60]))
    return False


def now_cst():
    return datetime.now(CST)


def http(url, timeout=20, gbk=True):
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return raw.decode("gbk" if gbk else "utf-8", "ignore")


def load_csv(path, **kw):
    return pd.read_csv(path, encoding="utf-8-sig", **kw)


def realtime(code):
    """单品种实时：返回 (最新价, 时间)"""
    try:
        raw = http("https://hq.sinajs.cn/list=nf_" + code.upper(), timeout=15)
        m = re.search(r'="([^"]*)"', raw)
        if not m or not m.group(1):
            return None
        f = m.group(1).split(",")
        return float(f[8])
    except Exception:
        return None


def realtime_batch(codes):
    """返回 {代码: (最新价, 昨结算价)}。昨结价由交易所行情给出，无需自己维护基准文件。"""
    out = {}
    for i in range(0, len(codes), 30):
        batch = codes[i:i + 30]
        try:
            raw = http("https://hq.sinajs.cn/list=" + ",".join("nf_" + c.upper() for c in batch))
        except Exception:
            continue
        for line in raw.split("\n"):
            m = re.match(r'var hq_str_nf_(\w+)="([^"]*)"', line.strip())
            if m and m.group(2):
                f = m.group(2).split(",")
                try:
                    last = float(f[8])
                    prev = float(f[10]) if len(f) > 10 and f[10] else None
                    out[m.group(1).upper()] = (last, prev)
                except Exception:
                    pass
        time.sleep(0.3)
    return out


def bars15(sym):
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
           "var%20t=/InnerFuturesNewService.getFewMinLine?symbol=" + sym + "&type=15")
    try:
        raw = http(url, gbk=False)
        m = re.search(r"\((\[.*\])\)", raw, re.S)
        return json.loads(m.group(1)) if m else []
    except Exception:
        return []


def session_bars(bars, kind):
    """kind: 'night' 或 'day'，取最近一个完整/进行中的该时段"""
    if not bars:
        return []
    today = now_cst().strftime("%Y-%m-%d")
    yest = (now_cst() - timedelta(days=1)).strftime("%Y-%m-%d")
    if kind == "night":
        cand = [b for b in bars if b["d"][:10] in (today, yest) and b["d"][11:16] >= "21:00"]
    else:
        cand = [b for b in bars if b["d"][:10] == today and "09:00" <= b["d"][11:16] <= "15:00"]
    if not cand:
        return []
    lastday = cand[-1]["d"][:10]
    return [b for b in cand if b["d"][:10] == lastday]


def atr60(sym):
    """用 60 分钟线估 ATR：直接抓新浪 60 分钟接口（云端无本地缓存）"""
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
           "var%20t=/InnerFuturesNewService.getFewMinLine?symbol=" + sym + "&type=60")
    try:
        raw = http(url, gbk=False)
        m = re.search(r"\((\[.*\])\)", raw, re.S)
        d = json.loads(m.group(1))
        h = np.array([float(x["h"]) for x in d])
        l = np.array([float(x["l"]) for x in d])
        c = np.array([float(x["c"]) for x in d])
        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        return float(np.mean(tr[-14:]))
    except Exception:
        return None


def append_csv(path, header, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    newfile = not path.exists()
    with io.open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if newfile:
            w.writerow(header)
        w.writerow(row)


def refresh_baseline(prices):
    """收盘后把最新价写成新基准"""
    main = load_csv(MAIN_CSV)
    rows = []
    for _, r in main.iterrows():
        mc = str(r["maincode"]).upper()
        if mc in prices:
            rows.append((now_cst().strftime("%Y-%m-%d"), r["contcode"], r["name"], mc, prices[mc]))
    with io.open(BASE_CSV, "w", encoding="utf-8-sig", newline="") as f:
        f.write("日期,代码,名称,主力合约,昨收价\n")
        for row in rows:
            f.write("%s,%s,%s,%s,%s\n" % row)
    print("基准价已刷新：%d 条 @ %s" % (len(rows), now_cst().strftime("%Y-%m-%d %H:%M")))


def evaluate():
    """跑一轮：抓价 → 算链条 → 查白名单 → 有信号就写文件+推送。返回本轮价格表。"""
    OUT.mkdir(parents=True, exist_ok=True)
    # 推送自检：手动运行工作流时把 PUSH_TEST 设为 yes，可立刻验证微信能否收到
    if (os.environ.get("PUSH_TEST") or "").strip().lower() in ("yes", "1", "true", "y"):
        ok = push("【测试】云端盯盘已连通", "这是一条测试推送。\n如果你在微信/手机收到这条消息，说明推送链路正常。\n"
                                          "时间：%s\n（周一开盘后，只有出现信号才会推送）"
                  % now_cst().strftime("%Y-%m-%d %H:%M:%S"))
        print("推送自检结果：" + ("成功 ✓" if ok else "失败 ✗（检查 SERVERCHAN_KEY）"))
    main_df = load_csv(MAIN_CSV)
    codes = [str(r["maincode"]).upper() for _, r in main_df.iterrows() if str(r["maincode"])]
    # 主力合约从 data/主力合约.csv 动态解析：换月时只需更新该文件，白名单自动跟随
    mc_by_name = {str(r["name"]): str(r["maincode"]).upper() for _, r in main_df.iterrows()}
    quotes = realtime_batch(codes)
    if len(quotes) < 20:
        print("实时行情获取失败（%d 个），退出" % len(quotes))
        return {}
    prices = {k: v[0] for k, v in quotes.items()}
    stamp = now_cst()
    hm = stamp.strftime("%H:%M")
    main_of = {str(r["contcode"]).upper(): str(r["maincode"]).upper() for _, r in main_df.iterrows()}
    # 涨跌 = 最新价 / 昨结算价 − 1（交易所行情自带，无需维护基准文件）
    chg = {k: (v[0] / v[1] - 1) * 100 for k, v in quotes.items()
           if v[1] and v[1] > 0}
    chain = {}
    for r in csv.DictReader(io.open(CHAIN_CSV, encoding="utf-8-sig")):
        mc = main_of.get(r["代码"].upper())
        if mc in chg:
            chain.setdefault(r["链条"], []).append(chg[mc])
    chain_avg = {k: float(np.mean(v)) for k, v in chain.items()}
    # 白名单检查
    kind = "night" if (hm >= "20:55" or hm <= "03:00") else "day"
    print("云端盯盘 %s ｜ 时段 %s ｜ 链条前4：%s" % (
        stamp.strftime("%Y-%m-%d %H:%M"), kind,
        " ".join("%s%+.2f%%" % (k, v) for k, v in sorted(chain_avg.items(), key=lambda x: -x[1])[:4])))
    for name, (chname, sym_default, mult, tick, fee) in WHITE.items():
        sym = mc_by_name.get(name, sym_default)
        sb = session_bars(bars15(sym), kind)
        if len(sb) < 3:
            print("  %-4s 本时段K线不足（%d 根）" % (name, len(sb)))
            continue
        hi = max(float(b["h"]) for b in sb[:2])
        lo = min(float(b["l"]) for b in sb[:2])
        px = float(sb[-1]["c"])
        tbar = sb[-1]["d"][11:16]
        cavg = chain_avg.get(chname)
        a = atr60(sym)
        risk = a * mult if a else None
        allow = None if cavg is None else ("空" if cavg < 0 else "多")
        state = "上破" if px > hi else ("下破" if px < lo else "区间内")
        flag = ""
        if state != "区间内" and a and risk and allow:
            want = "多" if state == "上破" else "空"
            if allow == want and risk <= MAX_RISK:
                stop = px - a if want == "多" else px + a
                tgt = px + 1.5 * a if want == "多" else px - 1.5 * a
                flag = "  >>> 信号 %s %s 入场 %.1f 止损 %.1f 目标 %.1f 风险 %.0f元" % (
                    name, want, px, stop, tgt, risk)
                append_csv(SIG_CSV, ["时间", "品种", "方向", "入场", "止损", "目标", "风险元", "链均", "时段"],
                           [stamp.strftime("%Y-%m-%d %H:%M:%S"), name, want, round(px, 1),
                            round(stop, 1), round(tgt, 1), round(risk), round(cavg, 2), kind])
                BRIEF.write_text(
                    "【云端信号】%s ｜ %s\n%s %s ｜ 入场 %.1f ｜ 止损 %.1f ｜ 目标 %.1f ｜ 风险 %.0f 元\n"
                    "链条 %s %+.2f%% ｜ 区间 %.1f~%.1f ｜ 最近K %s\n"
                    % (stamp.strftime("%Y-%m-%d %H:%M:%S"), kind, name, want, px, stop, tgt,
                       risk, chname, cavg, lo, hi, tbar), encoding="utf-8-sig")
                push("【短线信号】%s %s" % (name, want),
                     "品种：%s（%s）\n方向：%s\n入场：%.1f\n止损：%.1f\n目标：%.1f\n"
                     "风险：%.0f 元\n链条：%s %+.2f%%\n时段：%s\n区间：%.1f ~ %.1f\n\n"
                     "（打开仓库 out/待答复_最新.txt 复制给助手复核）"
                     % (name, sym, want, px, stop, tgt, risk, chname, cavg, kind, lo, hi))
            elif allow != want:
                flag = "  (突破但链条反向 → 放弃)"
            elif risk > MAX_RISK:
                flag = "  (突破但风险 %.0f 元 >300 → 放弃)" % risk
        print("  %-4s %8.1f 区间 %.1f~%.1f %-4s 允许%-3s 风险%-7s%s"
              % (name, px, lo, hi, state, allow or "-",
                 ("%.0f元" % risk) if risk else "-", flag))
        append_csv(LOG_CSV, ["时间", "品种", "现价", "下沿", "上沿", "状态", "允许方向", "链均", "风险元"],
                   [stamp.strftime("%Y-%m-%d %H:%M:%S"), name, round(px, 1), round(lo, 1),
                    round(hi, 1), state, allow or "-",
                    round(cavg, 2) if cavg is not None else "", round(risk) if risk else ""])
    return prices


def in_session():
    """当前是否在可交易时段（北京时间）：日盘 08:40-15:05 / 夜盘 20:40-23:05"""
    d = now_cst()
    if d.weekday() >= 5:
        return None
    hm = d.strftime("%H:%M")
    if "08:40" <= hm <= "15:05":
        return ("day", "15:05")
    if "20:40" <= hm <= "23:05":
        return ("night", "23:05")
    return None


def main():
    """mode=session：守住整个时段（自己循环，不依赖 GitHub 每 15 分钟触发）
       mode=once   ：只跑一轮（默认）"""
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MODE") or "once").lower()
    sess = in_session()
    if mode != "session" or not sess:
        if mode == "session" and not sess:
            print("当前不在交易时段（%s），只跑一轮。" % now_cst().strftime("%H:%M"))
        evaluate()
        return
    kind, end_hm = sess
    MAX_MIN = 170                      # GitHub 单次任务上限 6 小时，这里每轮只守 ~2h50m，由下一个 cron 接力
    print("===== 守住 %s 时段，最晚到 %s（北京时间），本轮最长 %d 分钟 =====" % (kind, end_hm, MAX_MIN))
    t0 = time.time()
    n = 0
    while True:
        d = now_cst()
        hm = d.strftime("%H:%M")
        if hm > end_hm or (time.time() - t0) / 60 > MAX_MIN:
            break
        try:
            evaluate()
            n += 1
        except Exception as e:
            print("本轮出错（继续）：%s" % str(e)[:120])
        d = now_cst()
        if d.strftime("%H:%M") > end_hm or (time.time() - t0) / 60 > MAX_MIN:
            break
        time.sleep(300)                # 每 5 分钟一轮
    print("本轮结束，共跑 %d 轮（下一轮 cron 会接力）" % n)


if __name__ == "__main__":
    main()
