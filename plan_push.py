#!/usr/bin/env python3
"""冷推前规划：读一盘切片（.gcode.3mf 或 .gcode），开推之前算清每件能不能推、怎么推。

为什么要有它：2026-10-16 我们推一盘 8 件的支架，拖成了三轮。第一轮只按「整件最高 42.2mm」评估，不敢在零件后面下喷头；
其实那件后 14mm 只有 20.2mm 高。推的时候它又顶着前面 4 件一起走，边走边转，最后卡在板边。这些开推前都能从 G-code 算出来。

推的方向固定是 -Y（往门口）。做法：喷头先在「下喷头空地」降到 Z3，再沿接近线 Y245 贴板横移到车道，往门口推。
下面几个数是我们一台 X2D 上的实测或切片参数原文，不是头壳图纸：
- 接近线 Y245：推件脚本允许的 Y 上限。
- 喷头在 Y245、Z3 横移，没碰到前方 27mm 处 20.2mm 高、41mm 处 42.2mm 高的零件
  → 外壳在这两个高度往前伸分别小于 27 / 41mm。CLEAR 就是这两对数；更高的件没数据，一律判「不知道」。
- 车道半宽 35：推 22mm 间距的一排挂钩，一次会顶到 2~3 个 → 能顶到的宽度约 44~66mm（推断）。
- 擦嘴条：X2D 开打时在前沿 Y≈0、X100 起画一条约 40mm 长、0.6mm 高的线，挡在出口上。车道压到它就提示。

用法: python3 plan_push.py 某盘.gcode.3mf [--json 输出.json] [--exclude 已取走的对象id,...]
退出码：0 = 一轮能推完；3 = 不能（看提示）。
"""
import argparse, json, math, re, sys, zipfile
from pathlib import Path

Y_APPROACH, Y_MIN, X_MIN, X_MAX = 245.0, 2.0, 10.0, 246.0
BIN = 4.0
CLEAR = [(20.2, 27.0), (42.2, 41.0)]   # (件在这个高度以下, 喷头前方至少要空这么多 mm)
HALF_WIDTH = 35.0
DESCENT_CLEAR = 60.0                    # 下喷头空地：60mm 内没有件（切片器 extruder_clearance_max_radius）
WIPE_X = (100.0, 140.0)


def read_gcode(path):
    path = Path(path)
    if path.suffix == '.gcode':
        return path.read_text(errors='replace').splitlines()
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if re.fullmatch(r'Metadata/plate_\d+\.gcode', n))
        return z.read(name).decode(errors='replace').splitlines()


def object_names(path):
    """slice_info.config 里的 identify_id → 名字；纯 .gcode 没有就用 id。"""
    try:
        with zipfile.ZipFile(path) as z:
            text = z.read('Metadata/slice_info.config').decode()
    except (zipfile.BadZipFile, KeyError, FileNotFoundError, IsADirectoryError):
        return {}
    return {m.group(1): m.group(2) for m in re.finditer(r'<object identify_id="(\d+)" name="([^"]+)"', text)}


def scan(lines):
    """每个对象：挤出点的 XY 范围、首层范围、按 Y 每 BIN 毫米一段的最高 Z。"""
    cur, z, x, y, parts = None, 0.0, None, None, {}
    for line in lines:
        if line.startswith('; Z_HEIGHT:'):
            z = float(line.split(':')[1]); continue
        if line.startswith('; start printing object'):
            cur = line.split(':')[-1].strip(); continue
        if line.startswith('; stop printing object'):
            cur = None; continue
        if line[:3] not in ('G1 ', 'G2 ', 'G3 '):
            continue
        m = dict(re.findall(r'([XYE])(-?[\d.]+)', line.split(';')[0]))
        if 'X' in m: x = float(m['X'])
        if 'Y' in m: y = float(m['Y'])
        if cur is None or x is None or y is None or float(m.get('E', 0)) <= 0:
            continue
        p = parts.setdefault(cur, dict(box=[x, y, x, y], first=None, bands={}, height=0.0))
        b = p['box']; b[0], b[1], b[2], b[3] = min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y)
        p['height'] = max(p['height'], z)
        k = math.floor(y / BIN)
        p['bands'][k] = max(p['bands'].get(k, 0.0), z)
        if z <= 0.21:
            f = p['first'] or [x, y, x, y]
            p['first'] = [min(f[0], x), min(f[1], y), max(f[2], x), max(f[3], y)]
    return parts


def clearance_needed(h):
    """件某段高 h，喷头在它后面多远才算有实测凭据不碰；没数据返回 None。"""
    for limit, need in CLEAR:
        if h <= limit + 1e-6:
            return need
    return None


def overlaps(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


def plan(parts, names):
    rows, issues = [], []
    for oid, p in parts.items():
        x0, y0, x1, y1 = p['box']
        bands = sorted(p['bands'].items(), reverse=True)
        # 接近线：Y245 前方每一段都要满足 CLEAR。
        approach_ok, worst = True, None
        for k, h in bands:
            d = Y_APPROACH - min((k + 1) * BIN, y1)   # 分段上沿不超过件的真实后沿
            need = clearance_needed(h)
            if need is None or d < need:
                approach_ok = False
                worst = worst or f'Y{k*BIN:.0f}-{(k+1)*BIN:.0f} 高 {h:.1f}，离接近线 {d:.0f}mm，' + \
                    ('高于 42.2 没有实测数据' if need is None else f'要 ≥{need:.0f}mm')
        back = [h for k, h in bands if k * BIN >= y1 - 14 - BIN / 2]
        rows.append(dict(id=oid, name=names.get(oid, oid), x=[round(x0, 1), round(x1, 1)], y=[round(y0, 1), round(y1, 1)],
                         height=round(p['height'], 1), back14_height=round(max(back), 1) if back else None,
                         lane_x=round((p['first'][0] + p['first'][2]) / 2 if p['first'] else (x0 + x1) / 2, 1),
                         approach_ok=approach_ok, approach_issue=worst,
                         bands={f'{k*BIN:.0f}': round(h, 1) for k, h in bands}))
    by_id = {r['id']: r for r in rows}
    # 一串：推某件时，它自己扫过的 X 范围 + 外壳半宽内、在它前方的件都会被顶上。
    for r in rows:
        sweep = (min(r['x'][0], r['lane_x'] - HALF_WIDTH), max(r['x'][1], r['lane_x'] + HALF_WIDTH))
        r['chain'] = sorted((o['id'] for o in rows if o is not r and o['y'][0] < r['y'][0]
                             and overlaps(sweep[0], sweep[1], o['x'][0], o['x'][1])), key=lambda i: -by_id[i]['y'][1])
        r['wipe_line'] = overlaps(sweep[0], sweep[1], *WIPE_X)
    # 下喷头空地：接近线上、DESCENT_CLEAR 内没有任何件的 X。
    spots = [x for x in range(int(X_MIN), int(X_MAX) + 1, 2)
             if all(math.dist((x, Y_APPROACH), (min(max(x, o['x'][0]), o['x'][1]), min(max(Y_APPROACH, o['y'][0]), o['y'][1]))) >= DESCENT_CLEAR
                    for o in rows)]
    descent = None
    if spots:
        mid = sum(r['lane_x'] for r in rows) / len(rows) if rows else 128
        descent = min(spots, key=lambda x: abs(x - mid))
    else:
        issues.append(f'接近线上找不到 {DESCENT_CLEAR:.0f}mm 内没件的空地下喷头')
    # 顺序：先推最靠后、链上件最少的车道；被前面一串带走的件不再单独推。
    order, gone = [], set()
    for r in sorted(rows, key=lambda r: (-r['y'][1], len(r['chain']))):
        if r['id'] in gone:
            continue
        order.append(dict(lead=r['id'], lane_x=r['lane_x'], chain=[c for c in r['chain'] if c not in gone]))
        gone |= {r['id'], *r['chain']}
    for r in rows:
        if not r['approach_ok']:
            issues.append(f"{r['name']}：接近线挡死（{r['approach_issue']}），手取或开打前改摆位")
    for step in order:
        if len(step['chain']) >= 2:
            names_ = '、'.join(by_id[c]['name'] for c in step['chain'])
            issues.append(f"推 {by_id[step['lead']]['name']} 会一次顶上 {len(step['chain'])} 件（{names_}）：2026-10-11 一推顶 2~3 件把整块钢板推出去过；件会转、会散")
        if by_id[step['lead']]['wipe_line']:
            issues.append(f"{by_id[step['lead']]['name']} 的车道压到前沿擦嘴条（X100~140），可能推不出板边")
    return dict(approach_y=Y_APPROACH, descent_x=descent, half_width=HALF_WIDTH, parts=rows, order=order, issues=issues,
                pushable_in_one_round=not issues)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plate'); ap.add_argument('--json')
    ap.add_argument('--exclude', default='', help='已经取走的对象 id，逗号分隔（id 见 --json 或 slice_info）')
    a = ap.parse_args()
    parts = scan(read_gcode(a.plate))
    for oid in filter(None, a.exclude.split(',')):
        parts.pop(oid.strip(), None)
    result = plan(parts, object_names(a.plate))
    if a.json:
        Path(a.json).write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print('件 | X | Y（门口=0） | 最高 | 后14mm高 | 车道X | 接近线')
    for r in sorted(result['parts'], key=lambda r: -r['y'][1]):
        print(f"{r['name']} | {r['x'][0]:.0f}-{r['x'][1]:.0f} | {r['y'][0]:.0f}-{r['y'][1]:.0f} | {r['height']} | {r['back14_height']} | {r['lane_x']} | {'可以' if r['approach_ok'] else '挡死'}")
    names = {r['id']: r['name'] for r in result['parts']}
    print(f"\n下喷头空地：X{result['descent_x']} Y{result['approach_y']:.0f}" if result['descent_x'] is not None else '\n下喷头空地：没有')
    print('推的顺序：')
    for i, s in enumerate(result['order'], 1):
        print(f"  {i}. 车道 X{s['lane_x']}：{names[s['lead']]}" + (f" → 顶着 {'、'.join(names[c] for c in s['chain'])}" if s['chain'] else ''))
    print('\n一轮能推完：' + ('能' if result['pushable_in_one_round'] else '不能'))
    for msg in result['issues']:
        print('  - ' + msg)
    return 0 if result['pushable_in_one_round'] else 3


if __name__ == '__main__':
    sys.exit(main())
