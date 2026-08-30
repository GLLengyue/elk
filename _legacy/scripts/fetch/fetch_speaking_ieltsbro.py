#!/usr/bin/env python3
"""
雅思哥口语题库抓取 —— 基线快照 / 换季 diff。

零第三方依赖（只用标准库），任何 Python 3.9+ 都能直接跑。

两个坑（API 的非直觉行为，务必保留注释）：
  坑1: part 参数不是 1/2/3 —— part=0 → Part1，part=1 → Part2&3，part=2 → 返回空
  坑2: 部分响应含裸控制字符，必须 json.loads(s, strict=False)，标准模式会抛
       Invalid control character

合规: 该端点是第三方反向代理，无官方授权，无 Terms。
      抓取结果仅作本地缓存，不对外分发。
      音频 mp3 只记 URL 与时长元数据，不下载、不入库 blob、不外发。

输出:
  state/ielts.db                                  两张表（oral_topics / oral_questions）
  data/structured/speaking/seasons/{season}/snapshot-{YYYY-MM-DD}.jsonl
  state/cache/ieltsbro/{part}_{topicId}.json       原始响应缓存，支持断点续跑与换季 diff

用法:
  python3 scripts/fetch/fetch_speaking_ieltsbro.py
  python3 scripts/fetch/fetch_speaking_ieltsbro.py --limit 8          # 冒烟
  python3 scripts/fetch/fetch_speaking_ieltsbro.py --season 2026-09-12
  python3 scripts/fetch/fetch_speaking_ieltsbro.py --no-cache         # 换季时强制重抓
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---- 路径与常量 ----------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "state" / "ielts.db"
CACHE_DIR = ROOT / "state" / "cache" / "ieltsbro"
SNAP_DIR = ROOT / "data" / "structured" / "speaking" / "seasons"

BASE = "https://ielts-bro-proxy.duzhuo.icu/ielts-bro"

# catalog 参数用中文（已验证）；oralTopCatalog 回传 1-4
CATALOG_CN = ("人物", "事物", "事件", "地点")
CATALOG_EN = {1: "person", 2: "object", 3: "event", 4: "place"}

# 坑1：part=0 → Part1；part=1 → Part2&3；part=2 → 返回空
PARTS = (0, 1)

# Part1 必考三剑客：永不轮换，换季 diff 时应忽略，否则制造噪声。
# 用精确短语而非「工作/学习」这类宽泛词，否则 Part2 人物类题组会被误伤。
ALWAYS_ON_KEYWORDS = ("work or stud", "hometown", "accommodation")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CST = timezone(timedelta(hours=8))
CONCURRENCY = 4
RETRIES = 3


def now_cst() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def is_always_on(name: str, part_grp: int) -> bool:
    """Part1 必考三剑客，永不轮换：Work or studies / Hometown / Accommodation。

    只对 Part1 生效。Part2 人物类题组（如「完美工作」「在团队中工作」「语言学习」）
    名字里常含「工作/学习」，但会随季轮换，不能误标 —— 早期版本用宽泛关键词
    匹配导致 6 个 Part2 题组被误伤，故这里收紧为精确短语 + part_grp 门槛。
    """
    if part_grp != 0:
        return False
    low = (name or "").lower()
    return any(k in low for k in ALWAYS_ON_KEYWORDS)


def parse_cue_card(text: str) -> tuple[str | None, list[str]]:
    """把 'Describe X\\nYou should say:\\nWhat it is\\n...' 拆成 prompt + bullets。"""
    if not text:
        return None, []
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return None, []
    prompt = lines[0].strip()
    bullets: list[str] = []
    for ln in lines[1:]:
        s = ln.strip()
        if s.lower().startswith("you should say"):
            continue
        s = s.lstrip("-•*·").strip()
        if s:
            bullets.append(s)
    return prompt, bullets


def extract_audio(lst: list[dict] | None) -> dict:
    """questionOralList: type 1=英音 2=美音 3=印度音。只取 URL 与时长，不下载。"""
    out: dict = {"uk": None, "us": None, "in": None, "seconds_uk": None}
    for a in lst or []:
        key = {1: "uk", 2: "us", 3: "in"}.get(a.get("type"))
        if not key:
            continue
        out[key] = a.get("oralUrl")
        if key == "uk" and a.get("seconds") is not None:
            out["seconds_uk"] = a.get("seconds")
    return out


# ---- HTTP ---------------------------------------------------------------

def get_json(url: str) -> dict | None:
    """带指数退避的 GET。坑2：strict=False 处理裸控制字符。"""
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw, strict=False)
        except Exception as exc:  # noqa: BLE001 - 网络层统一退避
            if attempt == RETRIES - 1:
                print(f"    ! FAIL {url.split('?')[-1][:60]} :: {exc}", file=sys.stderr)
                return None
            time.sleep(1.5 ** attempt)
    return None


def fetch_topic_lists() -> list[dict]:
    """8 个 list 请求：4 catalogs × 2 parts。"""
    topics: list[dict] = []
    for part in PARTS:
        for cn in CATALOG_CN:
            url = f"{BASE}/topic-list?catalog={urllib.parse.quote(cn)}&part={part}"
            data = get_json(url)
            if not data or data.get("status") != 0:
                print(f"  ! list 失败 catalog={cn} part={part}", file=sys.stderr)
                continue
            content = data.get("content") or {}
            lst = content.get("list") or []
            total = content.get("total")
            tag = "Part1" if part == 0 else "Part2&3"
            if total is not None and len(lst) < total:
                print(f"  ! 可能分页截断 {tag}/{cn}: {len(lst)}/{total}", file=sys.stderr)
            print(f"  list {tag}/{cn}: {len(lst)} 题组")
            for it in lst:
                it["_part_grp"] = part
                topics.append(it)
            time.sleep(0.3)
    return topics


def fetch_detail(topic_id: str, part_grp: int, use_cache: bool) -> dict | None:
    cache_file = CACHE_DIR / f"{part_grp}_{topic_id}.json"
    if use_cache and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 缓存损坏则重抓
            pass
    data = get_json(f"{BASE}/topic-detail?topicID={topic_id}&part={part_grp}")
    if data and data.get("status") == 0:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def fetch_all_details(keys: list[tuple[int, str]], use_cache: bool) -> dict:
    details: dict = {}
    done = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        fut2key = {ex.submit(fetch_detail, tid, pg, use_cache): (pg, tid)
                   for pg, tid in keys}
        for fut in as_completed(fut2key):
            k = fut2key[fut]
            try:
                details[k] = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"    ! {k} :: {exc}", file=sys.stderr)
                details[k] = None
            done += 1
            if done % 25 == 0:
                print(f"    ... {done}/{len(keys)}")
    return details


# ---- SQLite -------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS oral_topics (
  topic_id           TEXT NOT NULL,
  part_grp           INTEGER NOT NULL,
  season             TEXT,
  topic_name         TEXT,
  catalog_code       INTEGER,
  catalog_en         TEXT,
  if_new             INTEGER,
  question_count     INTEGER,
  recent_exam_count  INTEGER,
  oral_nums          TEXT,
  time_tag           TEXT,
  update_date        TEXT,
  always_on_rotation INTEGER DEFAULT 0,
  fetched_at         TEXT,
  PRIMARY KEY (topic_id, part_grp)
);

CREATE TABLE IF NOT EXISTS oral_questions (
  question_id TEXT NOT NULL PRIMARY KEY,
  topic_id    TEXT NOT NULL,
  oral_part   INTEGER NOT NULL,
  order_idx   INTEGER,
  text        TEXT,
  is_cue_card INTEGER DEFAULT 0,
  cue_prompt  TEXT,
  cue_bullets TEXT,
  audio_uk    TEXT,
  audio_us    TEXT,
  audio_in    TEXT,
  audio_sec   INTEGER,
  fetched_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_oq_topic  ON oral_questions(topic_id);
CREATE INDEX IF NOT EXISTS ix_oq_part   ON oral_questions(oral_part);
CREATE INDEX IF NOT EXISTS ix_ot_season ON oral_topics(season);
"""


def upsert(con: sqlite3.Connection, topics: list[dict], details: dict, season: str) -> int:
    ts = now_cst()
    n_q = 0
    for t in topics:
        tid = str(t.get("oralTopicId"))
        pg = t["_part_grp"]
        name = t.get("oralTopicName") or ""
        ccode = t.get("oralTopCatalog")
        con.execute(
            "INSERT OR REPLACE INTO oral_topics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, pg, season, name, ccode, CATALOG_EN.get(ccode),
             t.get("ifNew"), t.get("questionCount"), t.get("recentExamCount"),
             t.get("oralNums"), t.get("timeTag"), t.get("updateDate"),
             1 if is_always_on(name, pg) else 0, ts),
        )

        det = details.get((pg, tid)) or {}
        vol = ((det.get("content") or {}).get("oralQuestionDetailVOList") or [])
        if vol:
            # 先清旧题再插，保证换季后题目增删能正确反映
            con.execute("DELETE FROM oral_questions WHERE topic_id=?", (tid,))
        for i, q in enumerate(vol):
            opart = q.get("oralPart")
            text = q.get("oralQuestion") or ""
            aud = extract_audio(q.get("questionOralList"))
            cprompt, cbullets = (None, [])
            if opart == 2:
                cprompt, cbullets = parse_cue_card(text)
            con.execute(
                "INSERT OR REPLACE INTO oral_questions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(q.get("oralQuestionId")), tid, opart, i, text,
                 1 if opart == 2 else 0, cprompt,
                 json.dumps(cbullets, ensure_ascii=False),
                 aud["uk"], aud["us"], aud["in"], aud["seconds_uk"], ts),
            )
            n_q += 1
    con.commit()
    return n_q


# ---- 快照 ---------------------------------------------------------------

def write_snapshot(con: sqlite3.Connection, season: str, date_str: str):
    out_dir = SNAP_DIR / season
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"snapshot-{date_str}.jsonl"
    ts = now_cst()
    rows = con.execute(
        "SELECT topic_id, part_grp, topic_name, catalog_en, if_new, question_count, "
        "recent_exam_count, oral_nums, time_tag, update_date, always_on_rotation "
        "FROM oral_topics WHERE season=? ORDER BY part_grp, catalog_en, topic_id",
        (season,),
    ).fetchall()

    with out.open("w", encoding="utf-8") as f:
        for (tid, pg, name, cat, ifnew, qc, rec, nums, ttag, upd, always) in rows:
            qs = con.execute(
                "SELECT oral_part, order_idx, text, cue_prompt, cue_bullets, "
                "audio_uk, audio_us, audio_in, audio_sec FROM oral_questions "
                "WHERE topic_id=? ORDER BY oral_part, order_idx", (tid,),
            ).fetchall()

            p1: list[dict] = []
            p2cue = None
            p3: list[dict] = []
            audio_refs: list[dict] = []
            for oral_part, _idx, text, cprompt, cbullets, auk, aus, ain, asec in qs:
                if oral_part == 1:
                    p1.append({"text": text})
                elif oral_part == 2:
                    p2cue = {"prompt": cprompt,
                             "bullets": json.loads(cbullets or "[]"),
                             "raw": text}
                elif oral_part == 3:
                    p3.append({"text": text})
                if auk:
                    audio_refs.append({
                        "url_uk": auk, "url_us": aus, "url_in": ain,
                        "duration_s": asec,
                        # 合规硬标记：第三方代理无授权，音频仅本地引用，绝不下发/再分发
                        "redistributable": False, "downloaded": False,
                    })

            rec_obj = {
                "id": f"sp{1 if pg == 0 else 2}-{season}-{tid}",
                "part": 1 if pg == 0 else 2,
                "season": season,
                "catalog": cat,
                "topic_name": name,
                "questions": p1,
                "cue_card": p2cue,
                "part3_questions": p3,
                "audio_refs": audio_refs,
                "stats": {"oralTopicId": tid, "oralNums": nums, "recentExamCount": rec,
                          "ifNew": bool(ifnew), "timeTag": ttag, "updateDate": upd,
                          "always_on_rotation": bool(always)},
                "source": {"kind": "third_party", "origin": "ielts-bro-proxy",
                           "license_note": "unauthorized third-party proxy; internal cache only",
                           "redistributable": False},
                "not_official": True,
                "fetched_at": ts,
                "schema_version": "1.0.0",
            }
            f.write(json.dumps(rec_obj, ensure_ascii=False) + "\n")
    return out, len(rows)


# ---- main ---------------------------------------------------------------

def main() -> int:
    today = datetime.now(CST)
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2026-05-08",
                    help="归属考季标签，如 2026-05-08 / 2026-09-12")
    ap.add_argument("--date", default=today.strftime("%Y-%m-%d"), help="快照日期")
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 个题组（冒烟用）")
    ap.add_argument("--no-cache", action="store_true", help="忽略缓存强制重抓（换季时用）")
    args = ap.parse_args()

    t0 = time.time()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] 抓 list（季节 {args.season}）...")
    topics = fetch_topic_lists()
    if not topics:
        print("! 没有抓到任何题组，中止", file=sys.stderr)
        return 1
    if args.limit:
        topics = topics[: args.limit]
    print(f"    合计 {len(topics)} 题组")

    print(f"[2/4] 抓 detail（线程 {CONCURRENCY}，缓存={'关' if args.no_cache else '开'}）...")
    keys = [(t["_part_grp"], str(t.get("oralTopicId"))) for t in topics]
    details = fetch_all_details(keys, not args.no_cache)
    ok = sum(1 for d in details.values() if d and d.get("status") == 0)
    print(f"    detail 成功 {ok}/{len(keys)}")

    print("[3/4] 写 SQLite ...")
    con = sqlite3.connect(DB_PATH)
    con.executescript(DDL)
    n_q = upsert(con, topics, details, args.season)
    n_t = con.execute("SELECT COUNT(*) FROM oral_topics WHERE season=?",
                      (args.season,)).fetchone()[0]
    print(f"    oral_topics={n_t}  oral_questions={n_q}")

    print("[4/4] 写快照 ...")
    out, n = write_snapshot(con, args.season, args.date)
    print(f"    {out.relative_to(ROOT)}  ({n} 条)")

    print("\n--- 盘点 ---")
    for pg, label in ((0, "Part1"), (1, "Part2&3")):
        c = con.execute(
            "SELECT COUNT(*) FROM oral_topics WHERE season=? AND part_grp=?",
            (args.season, pg)).fetchone()[0]
        print(f"  {label}: {c} 题组")
    print("  timeTag 分布:")
    for tag, c in con.execute(
            "SELECT COALESCE(time_tag,'(空)'), COUNT(*) FROM oral_topics WHERE season=? "
            "GROUP BY time_tag ORDER BY 2 DESC", (args.season,)).fetchall():
        print(f"    {tag}: {c}")
    print("  三剑客标记（换季 diff 忽略）:")
    for (nm,) in con.execute(
            "SELECT topic_name FROM oral_topics WHERE season=? AND always_on_rotation=1",
            (args.season,)).fetchall():
        print(f"    {nm}")
    con.close()

    print(f"\n耗时 {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
