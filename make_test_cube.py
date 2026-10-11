#!/usr/bin/env python3
"""写一个推件测试方块 STL（默认 15×15×15 mm）。切片时设成空心（2 圈墙、0% 填充、无顶、底 3 层）≈ 1 克。

用法: python3 make_test_cube.py test_cube_15.stl [边长 X Y Z]
为什么 15mm 高：推的时候喷嘴离板 3mm，真正推件的是头壳前脸（比喷嘴高几毫米），件要明显高过它，头才不会从件上面滑过去。
"""
import struct, sys


def cube(path, dx=15.0, dy=15.0, dz=15.0):
    x0, y0, x1, y1 = -dx / 2, -dy / 2, dx / 2, dy / 2
    v = [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0), (x0, y0, dz), (x1, y0, dz), (x1, y1, dz), (x0, y1, dz)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(path, 'wb') as f:
        f.write(b'x2d cold push test cube'.ljust(80, b' ')); f.write(struct.pack('<I', len(faces)))
        for a, b, c in faces:
            f.write(struct.pack('<3f', 0, 0, 0))
            for i in (a, b, c): f.write(struct.pack('<3f', *v[i]))
            f.write(b'\0\0')
    print(f'写出 {path}：{dx:g} × {dy:g} × {dz:g} mm')


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else 'test_cube_15.stl'
    dims = [float(x) for x in sys.argv[2:5]] if len(sys.argv) >= 5 else []
    cube(out, *dims)
