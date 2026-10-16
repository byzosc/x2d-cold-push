#!/usr/bin/env python3
"""X2D cold push — 打完 PETG 件、热床凉到 35℃ 以下，用喷头把件推下打印板。

实验性。我们一台拓竹 Bambu Lab X2D（PETG Basic、纹理 PEI、首层热床 70℃、无外边）上：
  成功：2026-10-11 一个 15mm 空心方块（push 命令）。
  失败：2026-10-07、10-11 两次把整块钢板推走；10-16 一盘 8 件支架三轮没推下（见 README）。
  2026-10-16 加 lane / current-test：只在空板上测过，多件真件没测过。

前提：
  - 打印机开「局域网模式」+「开发者模式」（gcode_line 需要），访问码在打印机屏幕上看。
  - Python 3.9+，pip install "paho-mqtt>=2"
  - 环境变量：X2D_HOST（打印机 IP）、X2D_SERIAL（序列号）、X2D_ACCESS_CODE（访问码），可选 X2D_PORT（默认 8883）。
用法：
  python3 x2d_cold_push.py status                       # 看状态和热床温度
  python3 x2d_cold_push.py wait-cool [--max-temp 35]    # 等打完、热床凉到阈值
  python3 x2d_cold_push.py push --x 128 --part-back-y 67.5 [--dry-run] [--yes]        # 单个小件，一口气推（10-11 的做法）
  python3 x2d_cold_push.py lane --plan plan.json --lane 1 [--current 0.3] [--dry-run]  # 一条车道分段推（先跑 plan_push.py）
  python3 x2d_cold_push.py current-test --current 0.3 [--dry-run]                       # 空板验证降电流 / 恢复
"""
import argparse, json, os, ssl, sys, time


Z_SAFE, Z_LOOK, Z_PUSH, Y_END = 40, 13, 3, 2
HOME_Z_AT = (50, 220)          # push：归 Z 时喷头在这里碰板，必须是空板（默认左后方）
BACK_CLEARANCE = 70            # push：降落点 = 零件后沿 + 70，给头壳前伸留余量（保守值）
Y_APPROACH, Z_TRAVEL = 245, 60  # lane：接近线、抬高高度


def env(k):
    v = os.environ.get(k)
    if not v: sys.exit(f'缺环境变量 {k}')
    return v


class Printer:
    def __init__(self):
        try:
            import paho.mqtt.client as mqtt   # 真要连打印机才需要；--dry-run 不需要
        except ImportError:
            sys.exit('需要 paho-mqtt：pip install "paho-mqtt>=2"')
        self.host, self.serial, self.code = env('X2D_HOST'), env('X2D_SERIAL'), env('X2D_ACCESS_CODE')
        self.port = int(os.environ.get('X2D_PORT', '8883'))
        self.state, self.replies = {}, {}
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'cold-push-{int(time.time())}')
        c.username_pw_set('bblp', self.code)
        c.tls_set(cert_reqs=ssl.CERT_NONE); c.tls_insecure_set(True)   # 打印机局域网用自签证书
        c.on_connect = lambda cl, u, f, rc, p=None: cl.subscribe(f'device/{self.serial}/report')
        c.on_message = self._on_message
        c.connect(self.host, self.port, 30); c.loop_start(); self.c = c
        time.sleep(1.5)

    def _on_message(self, c, u, msg):
        try: d = json.loads(msg.payload)
        except Exception: return
        p = d.get('print', {})
        if p.get('command') == 'gcode_line': self.replies[str(p.get('sequence_id'))] = p
        for k in ('gcode_state', 'bed_temper', 'nozzle_temper', 'layer_num', 'total_layer_num', 'print_error', 'hms'):
            if k in p: self.state[k] = p[k]

    def refresh(self, wait=4):
        self.c.publish(f'device/{self.serial}/request', json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
        time.sleep(wait); return dict(self.state)

    def gcode(self, g):
        self.c.publish(f'device/{self.serial}/request', json.dumps({"print": {"sequence_id": "0", "command": "gcode_line", "param": g}}))

    def send_checked(self, g, wait):
        """发一段并等打印机回 SUCCESS；回复不明或被拒就抛错（不重发）。之后按估计时间等动作走完。"""
        seq = str(time.time_ns()); t0 = time.monotonic()
        self.c.publish(f'device/{self.serial}/request', json.dumps({"print": {"sequence_id": seq, "command": "gcode_line", "param": g}}))
        while seq not in self.replies and time.monotonic() - t0 < 6: time.sleep(.05)
        r = self.replies.get(seq)
        if r is None: raise RuntimeError('打印机没回复，这一步不重发；先看一眼机器')
        if str(r.get('result', '')).lower() != 'success' or r.get('err_code', 0) not in (0, '0'):
            raise RuntimeError(f'打印机拒绝：{r}')
        time.sleep(max(0, wait - (time.monotonic() - t0)))

    def close(self):
        self.c.loop_stop(); self.c.disconnect()


def steps(x, yd):
    return [
        ('XY 归零', 'G29.2 S0\nG28 X T300 R\nM400\n', 25),
        (f'在 {HOME_Z_AT} 空板处碰板归 Z', f'G90\nG1 Y{HOME_Z_AT[1]} F6000\nG1 X{HOME_Z_AT[0]} F6000\nM400\nG28 Z P0 T400\nM400\n', 35),
        (f'抬到 Z{Z_SAFE}', f'G90\nG1 Z{Z_SAFE} F600\nM400\n', 12),
        (f'移到零件后方 ({x:g},{yd:g}) 降到 Z{Z_LOOK}', f'G90\nG1 Z{Z_SAFE} F600\nG1 X{x:g} Y{yd:g} F6000\nM400\nG1 Z{Z_LOOK} F600\nM400\n', 22),
        ('PUSH', f'G90\nM204 S2000\nG1 Z{Z_PUSH} F300\nM400\nG1 Y{Y_END} F1200\nM400\nG1 Z{Z_SAFE} F600\nM400\nG1 Y{yd:g} F6000\nM400\n', 25),
        ('停到后方', 'G90\nG1 Z60 F600\nG1 X128 Y200 F6000\nM400\n', 12),
    ]


# ---------- lane：一条车道分段推（2026-10-16） ----------
# 分段：前推按 --segments（默认 10,10,20,30，最后一个数重复）一段段发，每段等打印机回 SUCCESS 再发下一段。
# 降电流：--current 把 XY 电机电流降到这个值（安培）。官方结束 G-code 对 Z 轴就是这么用的：
#   `M17 S` 存 → `M17 Z0.4 ; lower z motor current to reduce impact` → `M17 R` 恢复。卡住时电机打滑丢步，不硬顶。
#   我们空板实测：0.6A 加速度 10000 来回不丢步（当不了力度上限）；0.3A 丢步（证明 XY 生效），M17 R 后不再丢步。
#   0.3A 推不推得动真件没测过。X2D 开打 G-code 里有 `M17 D ; reset motor current to default`，下一盘开打会重置电流。
# 丢步后坐标不准，所以降过电流的车道收尾一律重新归零 XY。中途出错也会补发：恢复电流、抬高、归零、停车。

def segments(y_from, y_end, schedule):
    out, y, i = [], y_from, 0
    while y > y_end + 1e-6:
        y = max(y_end, y - schedule[min(i, len(schedule) - 1)]); out.append(round(y, 2)); i += 1
    return out


def finish(current_was_low):
    f = [('恢复电流', 'M17 R\n', 1)] if current_was_low else []
    f.append(('抬高', f'G90\nG1 Z{Z_TRAVEL} F600\nM400\n', 20))
    if current_was_low: f.append(('重新归零 XY', 'G28 X T300 R\nM400\n', 20))
    f.append(('停车', 'G90\nG1 X128 Y200 F3000\nM400\n', 6))
    return f


def lane_steps(lane_x, descent_x, current=None, schedule=(10, 10, 20, 30), y_end=2, home=True):
    for v, lo, hi, n in ((lane_x, 10, 246, '车道 X'), (descent_x, 10, 246, '下喷头 X'), (y_end, 2, 245, '推到 Y')):
        if not lo <= v <= hi: raise ValueError(f'{n} 要在 {lo}~{hi}')
    if current is not None and not 0.3 <= current <= 1.2: raise ValueError('电流要在 0.3~1.2 A')
    s = []
    if home:
        s += [('XY 归零', 'G29.2 S0\nG28 X T300 R\nM400\n', 20),
              ('在下喷头空地碰板归 Z', f'G90\nG1 X{descent_x:g} Y{Y_APPROACH} F3000\nM400\nG28 Z P0 T400\nM400\n', 40)]
    s += [('抬高移到下喷头空地', f'G90\nG1 Z{Z_TRAVEL} F600\nM400\nG1 X{descent_x:g} Y{Y_APPROACH} F3000\nM400\n', 12),
          (f'降到 Z{Z_PUSH}', f'G90\nG1 Z{Z_PUSH} F600\nM400\n', 8)]
    if current is not None: s.append((f'XY 电流降到 {current:g}A', f'M17 S\nM17 X{current:g} Y{current:g}\n', 1))
    s.append(('贴板横移到车道', f'G90\nM204 S300\nG1 X{lane_x:g} F600\nM400\n', abs(lane_x - descent_x) / 10 + 2))
    y = Y_APPROACH
    for ny in segments(Y_APPROACH, y_end, list(schedule)):
        s.append((f'推到 Y{ny:g}', f'G90\nM204 S300\nG1 Y{ny:g} F300\nM400\n', (y - ny) / 5 + 1)); y = ny
    return s, finish(current is not None)


def current_test_steps(current):
    """空板：参照点 → 降电流 → 慢走 → 狠来回 → 回参照点（降流丢步就会偏）→ 恢复 → 归零 → 狠来回 → 回参照点。
    对比三次参照点的摄像头画面：第二次偏了 = 降电流生效；第三次不偏 = 恢复有效。"""
    ref = 'G90\nG1 X128 Y128 F3000\nM400\n'
    shake = 'G90\nM204 S10000\n' + 'G1 X80 Y100 F18000\nG1 X176 Y160 F18000\n' * 4 + 'M400\n'
    s = [('XY 归零', 'G29.2 S0\nG28 X T300 R\nM400\n', 20), ('抬高', f'G90\nG1 Z{Z_TRAVEL} F600\nM400\n', 10),
         ('参照点 1（看画面）', ref, 6), (f'XY 电流降到 {current:g}A', f'M17 S\nM17 X{current:g} Y{current:g}\n', 1),
         ('低电流慢走一圈', 'G90\nM204 S300\nG1 X108 F300\nG1 Y108 F300\nG1 X148 F300\nG1 Y148 F300\nG1 X128 Y128 F300\nM400\n', 22),
         ('低电流狠来回', shake, 6), ('参照点 2（看画面：偏了 = 降电流生效）', ref, 6),
         ('恢复电流', 'M17 R\n', 1), ('重新归零', 'G28 X T300 R\nM400\n', 20),
         ('恢复后狠来回', shake, 6), ('参照点 3（看画面：不偏 = 恢复有效）', ref, 6)]
    return s, [('恢复电流', 'M17 R\n', 1), ('抬高', f'G90\nG1 Z{Z_TRAVEL} F600\nM400\n', 3), ('重新归零 XY', 'G28 X T300 R\nM400\n', 20),
               ('停车', 'G90\nG1 X128 Y200 F3000\nM400\n', 6)]


def run_steps(pr, body, tail, pause_names=()):
    """按顺序发 body；不管正常走完还是中途停，都发 tail（恢复电流只在降过、还没恢复时发）。"""
    sent, err = set(), None
    try:
        for name, g, t in body:
            if name in pause_names: input(f'下一步「{name}」。看一眼摄像头，按回车继续（Ctrl+C 停）…')
            print(f'{time.strftime("%H:%M:%S")} {name}', flush=True)
            pr.send_checked(g, t); sent.add(name)
    except (Exception, KeyboardInterrupt) as e:
        err = e; print(f'中途停：{e}', file=sys.stderr)
    finally:
        lowered = any(n.startswith('XY 电流降到') for n in sent)
        for name, g, t in tail:
            if name == '恢复电流' and (not lowered or name in sent): continue
            print(f'{time.strftime("%H:%M:%S")} 收尾：{name}', flush=True)
            try: pr.send_checked(g, t)
            except Exception as e: print(f'收尾失败：{e}；先看机器，必要时断电重启（会重置电机电流）', file=sys.stderr); break
    return err


def gate(s, max_temp):
    st, bed = s.get('gcode_state'), s.get('bed_temper')
    if st not in ('FINISH', 'IDLE'): sys.exit(f'打印机状态是 {st}，只在打完 / 空闲时动')
    if bed is None or bed > max_temp: sys.exit(f'热床 {bed}℃ 高于 {max_temp}℃：先 wait-cool（官方：凉到 35℃ 以下再取件）')
    if s.get('print_error') not in (0, None) or s.get('hms'): sys.exit(f'有报错 / HMS：{s.get("print_error")} {s.get("hms")}，先处理')


def main():
    ap = argparse.ArgumentParser(description='X2D cold push（实验性）')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    w = sub.add_parser('wait-cool'); w.add_argument('--max-temp', type=float, default=35); w.add_argument('--max-minutes', type=float, default=180)
    p = sub.add_parser('push')
    p.add_argument('--x', type=float, required=True, help='零件中心 X（mm）')
    p.add_argument('--part-back-y', type=float, required=True, help='零件后沿 Y（mm，离门最远的那条边）')
    p.add_argument('--max-temp', type=float, default=35, help='热床高于这个温度就不推')
    p.add_argument('--dry-run', action='store_true', help='只打印要发的 G-code，不发')
    p.add_argument('--yes', action='store_true', help='降到 Z13 后不停下等回车，直接推')
    l = sub.add_parser('lane', help='一条车道分段推（先用 plan_push.py 出计划）')
    l.add_argument('--plan', help='plan_push.py --json 的输出'); l.add_argument('--lane', type=int, default=1, help='计划里第几条车道')
    l.add_argument('--lane-x', type=float); l.add_argument('--descent-x', type=float)
    l.add_argument('--current', type=float, help='XY 电流（安培，0.3~1.2）；不给就不降')
    l.add_argument('--segments', default='10,10,20,30', help='每段推多远（mm），最后一个数重复')
    l.add_argument('--y-end', type=float, default=2); l.add_argument('--max-temp', type=float, default=35)
    l.add_argument('--no-home', action='store_true'); l.add_argument('--dry-run', action='store_true')
    l.add_argument('--yes', action='store_true', help='第一段推之前不停下等回车')
    c = sub.add_parser('current-test', help='空板验证降电流 / 恢复（板上不能有东西）')
    c.add_argument('--current', type=float, default=0.3); c.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    if a.cmd == 'push':
        yd = a.part_back_y + BACK_CLEARANCE
        assert 10 <= a.x <= 246, 'X 超出安全范围 10~246'
        assert 20 <= yd <= 245, f'降落点 Y={yd:g} 超出安全范围 20~245（零件太靠后，推不了）'
        if a.dry_run:
            for name, g, t in steps(a.x, yd): print(f'== {name}（之后等 {t}s）\n{g}')
            return
    if a.cmd in ('lane', 'current-test'):
        try:
            if a.cmd == 'current-test':
                if not 0.3 <= a.current <= 1.2: raise ValueError('电流要在 0.3~1.2 A')
                body, tail = current_test_steps(a.current)
            else:
                lane_x, descent_x = a.lane_x, a.descent_x
                if a.plan:
                    plan = json.load(open(a.plan, encoding='utf-8'))
                    if lane_x is None: lane_x = plan['order'][a.lane - 1]['lane_x']
                    if descent_x is None: descent_x = plan['descent_x']
                if lane_x is None or descent_x is None: sys.exit('给 --plan，或同时给 --lane-x 和 --descent-x')
                body, tail = lane_steps(lane_x, descent_x, a.current, [float(v) for v in a.segments.split(',')], a.y_end, not a.no_home)
        except ValueError as e:
            sys.exit(str(e))
        if a.dry_run:
            for name, g, t in body + [(f'收尾：{n}', g, t) for n, g, t in tail]: print(f'== {name}（约 {t:.0f}s）\n{g}', end='')
            return

    pr = Printer()
    try:
        s = pr.refresh()
        if a.cmd == 'status':
            print(json.dumps(s, ensure_ascii=False)); return
        if a.cmd == 'wait-cool':
            t0 = time.time()
            while True:
                st, bed = s.get('gcode_state'), s.get('bed_temper')
                print(f'{time.strftime("%H:%M:%S")} 状态 {st} 热床 {bed}℃', flush=True)
                if st in ('FINISH', 'IDLE') and bed is not None and bed <= a.max_temp: print('可以推了'); return
                if st in ('FAILED', 'PAUSE'): sys.exit(f'状态 {st}，不推')
                if time.time() - t0 > a.max_minutes * 60: sys.exit('等太久了')
                time.sleep(60); s = pr.refresh()
        if a.cmd == 'current-test':
            gate(s, 50)
            input('板上必须是空的（这个测试会狠狠来回）。确认后按回车…')
            err = run_steps(pr, body, tail)
            print('完成。对比三次「参照点」的摄像头画面。' if err is None else '中途停了，已收尾。'); return
        if a.cmd == 'lane':
            gate(s, a.max_temp)
            print('确认：板上只有计划里的零件；下喷头空地是空的；出口（门那边）有接件的地方；人在电源旁边。')
            first_push = next(n for n, _, _ in body if n.startswith('推到 Y'))
            err = run_steps(pr, body, tail, () if a.yes else (first_push,))
            print('推完。看一眼：零件下板了吗？钢板动了吗？' if err is None else '中途停了，已收尾（恢复电流、抬高、归零、停车）。'); return
        # push
        gate(s, a.max_temp)
        print('确认：板上只有要推的零件；归 Z 点和零件后方 70mm 都是空的；出口（门那边）放了接件盒；人在电源旁边。')
        for name, g, t in steps(a.x, yd):
            if name == 'PUSH' and not a.yes:
                input('喷头已停在零件后方 Z13。看一眼没碰到零件，按回车推（Ctrl+C 取消）…')
            print(f'{time.strftime("%H:%M:%S")} {name}', flush=True)
            pr.gcode(g); time.sleep(t)
        print('推完。看一眼：零件下板了吗？钢板动了吗？')
    finally:
        pr.close()


if __name__ == '__main__':
    main()
