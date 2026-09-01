#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_all.py — 数据包机械层全量质检器。

只查「不用读文章就能查」的东西：配比、题号、evidence 切片与段落归属、
选项唯一性、填空词数与答案是否命中原文。语义层（T/F/NG 定性、MC 第二解）
查不了，那部分见 docs/pack-authoring.md 的人工复核清单。

用法:
  python3 check_all.py                       # 校验默认数据包下已勾选的全部篇目
  python3 check_all.py --pack <数据包目录>    # 显式指定
"""
import argparse
import json, os, sys, pathlib, re

# ---- 路径：全部基于 __file__ 推导 ----------------------------------
_HERE = pathlib.Path(__file__).resolve().parent
CAPABILITY = _HERE.parents[1]
BASE = CAPABILITY / 'packs' / 'reading-news-2026-08' / 'data' / 'reading' / 'news'
POOL = _HERE / 'topic_pool.md'

# 篇目计划从选题池自动发现：只校验磁盘上真实存在的文件，
# 代号（A-J）以选题池为准。新增篇目无需改本文件。
# 主题列可能含空格（"AI 数据中心电网压力"），故用非贪婪匹配到行尾代号；
# 代号允许带 subtype 后缀（如 "G(note)"），只取首字母。
POOL_LINE = re.compile(
    r'^-\s*\[(?P<done>[ xX])\]\s*(?P<num>\d+)\s+(?P<slug>[a-z0-9-]+)\s+'
    r'(?P<topic>.*?)\s{2,}(?P<code>[A-J](?:\([a-z]+\))?)\s*$',
    re.M,
)


def load_plan(base: pathlib.Path) -> list:
    """返回 [(篇号, 文件名, 代号)]，仅包含磁盘上已存在的文件。"""
    if not POOL.exists():
        raise SystemExit(f'✗ 选题池不存在：{POOL}')
    plan, seen = [], set()
    for line in POOL.read_text(encoding='utf-8').split('\n'):
        if not line.startswith('- ['):
            continue
        m = POOL_LINE.match(line.rstrip())
        if not m:
            print(f'⚠ 选题池行无法解析，已跳过: {line.strip()[:60]}', file=sys.stderr)
            continue
        num, slug = int(m.group('num')), m.group('slug')
        code = m.group('code')[0]
        if num in seen:
            continue
        if not (base / f'{slug}.json').exists():
            continue  # 还没写，不报致命
        seen.add(num)
        plan.append((num, f'{slug}.json', code))
    return sorted(plan)

COMBOS = {
 'A': ['identifying_information/5','summary_completion/note/4','multiple_choice/4'],
 'B': ['matching_headings/5','identifying_information/4','short_answer/4'],
 'C': ['matching_information/5','identifying_information/4','sentence_completion/4'],
 'D': ['identifying_information/5','multiple_choice/4','summary_completion/summary|note/4'],
 'E': ['matching_features/5','identifying_information/4','summary_completion/note/4'],
 'F': ['identifying_writers_views/5','matching_information/4','short_answer/4'],
 'G': ['matching_headings/5','identifying_information/4','summary_completion/summary|note/4'],
 'H': ['identifying_information/5','matching_features/4','summary_completion/summary/4'],
 'I': ['matching_information/5','identifying_writers_views/4','multiple_choice/4'],
 'J': ['identifying_information/5','sentence_completion/4','multiple_choice/4'],
}

LIMIT_WORDS = {
 'NO MORE THAN ONE WORD': 1,
 'NO MORE THAN TWO WORDS': 2,
 'NO MORE THAN THREE WORDS': 3,
 'NO MORE THAN ONE WORD AND/OR A NUMBER': 1,
}

def norm_limit(wl):
    """兼容 'NO MORE THAN THREE WORDS' / 3 两种写法"""
    if isinstance(wl, int):
        return wl
    if isinstance(wl, str):
        return LIMIT_WORDS.get(wl.upper())
    return None

def norm_sub(t, sub):
    """把 summary_completion 的 subtype 归一化：summary/note 对 G/D 都算符合；
    仅当选题池标注了精确 subtype 时严格匹配。返回 '*' 表示接受任意。"""
    if t == 'summary_completion':
        return sub or 'summary'
    return sub

def actual(spec):
    t = spec['type']
    sub = spec.get('subtype')
    if sub and t == 'summary_completion':
        return f'{t}/{sub}/{len(spec["questions"])}'
    return f'{t}/{len(spec["questions"])}'

def expand(exp):
    """把代号定义展开成 (type, set_of_subtype_or_None, count) 的列表"""
    out = []
    for item in exp:
        p = item.split('/')
        if len(p) == 3:
            out.append((p[0], set(p[1].split('|')), p[2]))
        else:
            out.append((p[0], None, p[1]))
    return out

def spec_match(got, exp):
    """got 与 exp 比较。允许题组顺序不同（题型集合一致即受理），subtype 允许变体。
    返回 (bool, reason)。"""
    exp_sigs = expand(exp)
    if len(got) != len(exp_sigs):
        return False, '组数不同'
    # 逐个 got 在 exp 中找匹配（不重复使用 exp 项）
    used = [False]*len(exp_sigs)
    for g in got:
        g_parts = g.split('/')
        g_type = g_parts[0]
        g_count = g_parts[-1]
        g_sub = g_parts[1] if len(g_parts) == 3 else None
        matched = False
        for i,(e_type, e_subs, e_count) in enumerate(exp_sigs):
            if used[i]:
                continue
            if e_type == g_type and e_count == g_count and (e_subs is None or g_sub is None or g_sub in e_subs):
                used[i] = True
                matched = True
                break
        if not matched:
            return False, f'{g} 无匹配项'
    return True, 'ok'

def wordcount(s):
    return len(s.split())

def main():
    ap = argparse.ArgumentParser(description='数据包机械层全量质检')
    ap.add_argument('--pack', default=str(BASE.parents[2]),
                    help='数据包目录（默认 capability/packs/reading-news-2026-08）')
    a = ap.parse_args()

    base = pathlib.Path(a.pack) / 'data' / 'reading' / 'news'
    if not base.is_dir():
        raise SystemExit(f'✗ 找不到数据目录：{base}')

    plan = load_plan(base)
    if not plan:
        raise SystemExit(f'✗ {base} 下没有与选题池对应的条目')

    # 仅产出机械层问题清单，不打印每题答案以便输出更紧凑
    run(base, plan)


def run(base, PLAN):
    results = []

    for num, fn, code in PLAN:
        fp = base / fn
        if not fp.exists():
            results.append((num, fn, code, [f'[致命] 文件不存在: {fp}']))
            continue
        try:
            d = json.loads(fp.read_text(encoding='utf-8'))
        except Exception as e:
            results.append((num, fn, code, [f'[致命] JSON 解析失败: {e}']))
            continue

        ps = d['passage']['paragraphs']
        full = '\n'.join(p['text'] for p in ps)
        issues = []

        allq = [q for g in d['question_groups'] for q in g['questions']]
        if len(d['question_groups']) != 3:
            issues.append(f'[严重] 题组数={len(d["question_groups"])}，应为 3')
        if len(allq) != 13:
            issues.append(f'[严重] 总题数={len(allq)}，应为 13')

        got = [actual(g) for g in d['question_groups']]
        exp = COMBOS[code]
        ok, reason = spec_match(got, exp)
        if not ok:
            issues.append(f'[严重] 配比不符: 预期 {exp} / 实际 {got}（{reason}）')
        elif got != exp:
            # 题型集合一致但顺序与代号定义不同 → 轻微设计偏好
            issues.append(f'[轻微] 组顺序与代号 {code} 定义不同（题型齐全）: {got}')

        nums = sorted(q['number'] for q in allq)
        if nums != list(range(1,14)):
            issues.append(f'[严重] 题号异常: {nums}')

        for g in d['question_groups']:
            qr = g['question_range']
            gn = sorted(q['number'] for q in g['questions'])
            expn = list(range(qr['from'], qr['to']+1))
            if gn != expn:
                issues.append(f'[严重] 组 {g["id"]} range {qr} != 实际 {gn}')

        for g in d['question_groups']:
            for q in g['questions']:
                if 'answer' not in q:
                    issues.append(f'[严重] Q{q["number"]} 缺 answer')

        # evidence 偏移
        ev_total = 0
        ev_miss = 0
        for g in d['question_groups']:
            for q in g['questions']:
                for e in q.get('evidence', []):
                    ev_total += 1
                    s, t = e.get('start'), e.get('end')
                    if s is None or t is None:
                        issues.append(f'[严重] Q{q["number"]} evidence 缺 start/end')
                        continue
                    if full[s:t] != e['quote']:
                        issues.append(f'[严重] Q{q["number"]} evidence 切片不匹配')
                        ev_miss += 1
                    lab = e.get('paragraph_label')
                    idx = [i for i,p in enumerate(ps) if p['label']==lab]
                    if idx:
                        so = sum(len(ps[j]['text'])+1 for j in range(idx[0]))
                        eo = so + len(ps[idx[0]]['text'])
                        if not (so <= s and t <= eo):
                            issues.append(f'[严重] Q{q["number"]} evidence 偏移 {s}-{t} 不在段 {lab}')

        # MC / matching
        for g in d['question_groups']:
            t = g['type']
            opts = g.get('options') or []
            keys = [q['answer'] for q in g['questions']]
            if t == 'multiple_choice' and opts:
                nopt = len(opts)
                if nopt == len(keys):
                    if sorted(keys) != sorted(o['key'] for o in opts):
                        issues.append(f'[严重] MC 选项=题数但答案未一一覆盖 答{keys} 选{[o["key"] for o in opts]}')
                else:
                    dup = [k for k in set(keys) if keys.count(k)>1]
                    if dup:
                        issues.append(f'[严重] MC 重复答案 {dup}')
            if t in ('matching_headings','matching_information','matching_features') and opts:
                nopt = len(opts)
                dup = [k for k in set(keys) if keys.count(k)>1]
                if dup:
                    issues.append(f'[严重] {t} 重复选项 {dup}')
                if t == 'matching_headings' and nopt <= len(keys):
                    issues.append(f'[严重] matching_headings 选项数({nopt}) 未多于题数({len(keys)})')

        # 判断题取值合法性
        for g in d['question_groups']:
            if g['type'] == 'identifying_information':
                bad = [(q['number'],q['answer']) for q in g['questions'] if q['answer'] not in ('TRUE','FALSE','NOT GIVEN')]
                if bad:
                    issues.append(f'[严重] T/F/NG 非法取值 {bad}')
                ks = [q['answer'] for q in g['questions']]
                if len(set(ks))==2 and len(ks)>=4:
                    issues.append(f'[轻微] T/F/NG 组仅 2 种答案 {sorted(set(ks))}')
            if g['type'] == 'identifying_writers_views':
                bad = [(q['number'],q['answer']) for q in g['questions'] if q['answer'] not in ('YES','NO','NOT GIVEN')]
                if bad:
                    issues.append(f'[严重] Y/N/NG 非法取值 {bad}')
                ks = [q['answer'] for q in g['questions']]
                if len(set(ks))==2 and len(ks)>=4:
                    issues.append(f'[轻微] Y/N/NG 组仅 2 种答案 {sorted(set(ks))}')

        # 填空词数 + 原文可找到
        for g in d['question_groups']:
            if g['type'] in ('summary_completion','sentence_completion','short_answer','note_completion','table_completion'):
                wl = g.get('word_limit')
                lim = norm_limit(wl)
                for q in g['questions']:
                    ans = str(q['answer'])
                    n = wordcount(ans)
                    if lim and n > lim:
                        issues.append(f'[严重] Q{q["number"]} 答「{ans}」{n}词 超过 {wl}')
                    if ans.lower() not in full.lower() and g['type'] != 'short_answer':
                        issues.append(f'[致命] Q{q["number"]} 答「{ans}」未在原文找到')
                    if not q.get('acceptable_answers'):
                        issues.append(f'[轻微] Q{q["number"]} 无 acceptable_answers')
                    for a in (q.get('acceptable_answers') or []):
                        for variant in a.split('|'):
                            v = variant.strip()
                            if v and lim and wordcount(v) > lim:
                                # 备选是等价指称（全称/缩写），评判系统按同义接受；仅轻微提示，不判严重
                                issues.append(f'[轻微] Q{q["number"]} 备选「{v}」超 {wl}（等价指称）')
                            if v and v.lower() not in full.lower() and g['type'] != 'short_answer':
                                issues.append(f'[轻微] Q{q["number"]} 备选「{v}」原文未原样出现')

        results.append((num, fn, code, issues, ev_total, ev_miss))

    print('='*100)
    print(f'{"#":>3} {"代号":<4} {"题组":<3} {"题数":<3} {"evidence":<14} {"硬伤数":<8} {"致命":<4} {"严重":<4} {"轻微":<4}  文件')
    fatal_total = severe_total = minor_total = 0
    for r in results:
        num, fn, code, issues, ev_total, ev_miss = r
        if not issues:
            issues = []
        f = sum(1 for i in issues if '[致命]' in i)
        s = sum(1 for i in issues if '[严重]' in i)
        m = sum(1 for i in issues if '[轻微]' in i)
        fatal_total += f
        severe_total += s
        minor_total += m
        print(f'{num:>3} {code:<4} {"3":<3} {"13":<3} {f"{ev_total-ev_miss}/{ev_total}":<14} {f+s+m:<8} {f:<4} {s:<4} {m:<4}  {fn}')
    print('-'*100)
    print(f'合计: 致命 {fatal_total} 严重 {severe_total} 轻微 {minor_total}')

    # 输出每篇问题清单（机械层）
    print()
    print('='*100)
    print('逐篇机械层问题清单')
    print('='*100)
    for r in results:
        num, fn, code, issues, ev_total, ev_miss = r
        if not issues:
            print(f'\n#{num} {fn} ({code}): 脚本层零问题')
            continue
        print(f'\n#{num} {fn} ({code}): {len(issues)} 条')
        for i in issues:
            print(f'   - {i}')


if __name__ == '__main__':
    main()
