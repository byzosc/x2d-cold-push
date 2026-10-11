#!/usr/bin/env python3
"""X2D cold push — 打完 PETG 件、热床凉到 35℃ 以下，用喷头把件推下打印板。

实验性：2026-10-11 在一台拓竹 Bambu Lab X2D 上成功 1 次（PETG Basic、纹理 PEI 板、首层热床 70℃、无外边）。
不保证你的机器、你的料、你的件也能推下来；件粘得牢时会把整块钢板推走（2026-10-07 发生过）。

前提：
  - 打印机开「局域网模式」+「开发者模式」（gcode_line 需要），访问码在打印机屏幕上看。
  - Python 3.9+，pip install "paho-mqtt>=2"
  - 环境变量：X2D_HOST（打印机 IP）、X2D_SERIAL（序列号）、X2D_ACCESS_CODE（访问码），可选 X2D_PORT（默认 8883）。
用法：
  python3 x2d_cold_push.py status                       # 看状态和热床温度
  python3 x2d_cold_push.py wait-cool [--max-temp 35]    # 等打完、热床凉到阈值
  python3 x2d_cold_push.py push --x 128 --part-back-y 67.5 [--dry-run] [--yes]
推件动作（每步之间等它走完）：XY 归零 → 在空板处碰板归 Z → 抬到 Z40 → 移到零件后方（后沿 + 70）降到 Z13 停住 →
  （默认等你按回车确认）→ 降到 Z3、20 mm/s 往前推到 Y2 → 抬起 → 停到后方。
"""
import argparse, json, os, ssl, sys, time


Z_SAFE, Z_LOOK, Z_PUSH, Y_END = 40, 13, 3, 2
HOME_Z_AT = (50, 220)          # 归 Z 时喷头在这里碰板：必须是空板（默认左后方）
BACK_CLEARANCE = 70            # 降落点 = 零件后沿 + 70：给头壳前伸留余量（保守值，没实测头壳尺寸）


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
        self.state, self.replies = {}, []
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
        if p.get('command') == 'gcode_line': self.replies.append(p)
        for k in ('gcode_state', 'bed_temper', 'nozzle_temper', 'layer_num', 'total_layer_num', 'print_error'):
            if k in p: self.state[k] = p[k]

    def refresh(self, wait=4):
        self.c.publish(f'device/{self.serial}/request', json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
        time.sleep(wait); return dict(self.state)

    def gcode(self, g):
        self.c.publish(f'device/{self.serial}/request', json.dumps({"print": {"sequence_id": "0", "command": "gcode_line", "param": g}}))

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
    a = ap.parse_args()

    if a.cmd == 'push':
        yd = a.part_back_y + BACK_CLEARANCE
        assert 10 <= a.x <= 246, 'X 超出安全范围 10~246'
        assert 20 <= yd <= 245, f'降落点 Y={yd:g} 超出安全范围 20~245（零件太靠后，推不了）'
        if a.dry_run:
            for name, g, t in steps(a.x, yd): print(f'== {name}（之后等 {t}s）\n{g}')
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
        # push
        st, bed = s.get('gcode_state'), s.get('bed_temper')
        if st not in ('FINISH', 'IDLE'): sys.exit(f'打印机状态是 {st}，只在打完 / 空闲时推')
        if bed is None or bed > a.max_temp: sys.exit(f'热床 {bed}℃ 高于 {a.max_temp}℃：先 wait-cool（官方：凉到 35℃ 以下再取件）')
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
