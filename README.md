# X2D 冷推件 · x2d-cold-push

**拓竹 Bambu Lab X2D 打完 PETG 件，等热床凉下来，用喷头把件推下打印板。不加任何配件。**

> ⚠️ 实验性：2026-10-11 在一台 X2D 上**成功 1 次**（PETG Basic、纹理 PEI 板）。之前一次（10-07）失败，把整块钢板推走了。
> 下面的条件就是两次对比出来的。你的机器、你的料、你的件不一定一样，先用 1 克的测试方块试。

![推之前 / 推完（床在同一高度）：方块没了，钢板位置没变](media/before_after.jpg)

视频：待补。

## 必要条件（缺一条都可能推不下来，或者把钢板推走）

| # | 条件 | 为什么 |
|---|---|---|
| 1 | **首层热床 70℃**（拓竹 PETG Basic 官方默认），**不要 80℃** | 10-07 用首层 80℃，件没下来、钢板被推走；10-11 用 70℃ 推下来了 |
| 2 | **不加外边（brim）** | 外边让贴板面积变大，粘得更牢 |
| 3 | **热床凉到 35℃ 以下再推** | 拓竹纹理 PEI 板官方原文："wait until it reaches 35℃ or lower"。实测关着门从 70℃ 凉到 35℃ 约 37 分钟 |
| 4 | **板上只有要推的件，底面小、排成一排** | 这次只测了一个 15×15 mm 底面的方块；多件、大底面没测过 |
| 5 | **出口放接件盒** | 件会被直接弹出板子前沿，没接住会掉地上 |
| 6 | **局域网模式 + 开发者模式** | 推件动作是空闲时通过局域网 MQTT 的 `gcode_line` 一步步发的 |

## 已知问题

- **擦嘴条挡在出口**：X2D 每次开打都会在板子最前沿（y≈0、从 x100 起约 40 mm、0.6 mm 高）画一条出料线（开打 G-code 里的 `nozzle load line`，`G130 … L40 E12`），它留在板上，正好在往前推的出口。连续推件时应该只有第一盘画（还没做）。
- **钢板往前没有东西挡**：X2D 的钢板靠磁吸 + 后定位销，往前推没有限位。件粘得牢时，推的是整块板。
- **没有「板空了」检测**：别无人值守连打——推件失败时下一盘会直接打在没推走的件上。

## 怎么用

```bash
pip install "paho-mqtt>=2"
export X2D_HOST=192.168.x.x        # 打印机 IP
export X2D_SERIAL=xxxxxxxx         # 序列号
export X2D_ACCESS_CODE=xxxxxxxx    # 局域网访问码（打印机屏幕上看）

python3 x2d_cold_push.py status                                  # 看状态和热床温度
python3 x2d_cold_push.py wait-cool                               # 等打完、热床凉到 35℃
python3 x2d_cold_push.py push --x 128 --part-back-y 67.5 --dry-run   # 先看要发的 G-code
python3 x2d_cold_push.py push --x 128 --part-back-y 67.5         # 真推（降到零件后方会停下等你按回车）
```

`--x` 是零件中心 X，`--part-back-y` 是零件后沿（离门最远那条边）的 Y，单位 mm，切片软件里看。

推件动作（每步之间等它走完）：

1. XY 归零（`G28 X T300 R`）
2. 在左后方空板处 (50, 220) 碰板归 Z（`G28 Z P0 T400`）——**这个位置必须是空的**
3. 抬到 Z40
4. 移到零件后方（后沿 + 70 mm）降到 Z13，停住，看一眼没碰到件
5. 降到 Z3，以 20 mm/s 往门的方向推到 Y2
6. 抬起，停到后方

## 测试方块（约 1 克）

```bash
python3 make_test_cube.py test_cube_15.stl
```

切片设置：2 圈墙、0% 填充、无顶面、底 3 层、无外边、首层热床 70℃、首层速度 20 mm/s。放在板子前部中间（我们放在 (128, 60)，后沿 y=67.5）。
我们这次：0.92 g、约 20 分钟。

## 实测记录

| 日期 | 件 | 首层热床 | 外边 | 冷却 | 结果 |
|---|---|---|---|---|---|
| 2026-10-07 | 两个 PETG 短钩 | 80℃ | 无 | 约 45 分钟（没测温） | ❌ 件没下来，整块钢板被推得往前、往右走 |
| 2026-10-11 | 15 mm 空心方块（0.92 g） | 70℃ | 无 | 到 35.0℃（打完后约 37 分钟，关门） | ✅ 件被推下板并弹出，钢板没动（推前推后同一高度两帧对比） |

## 接下来要做

- [ ] 连续推件时只在第一盘画擦嘴条
- [ ] 把推件写进打印结束段，打完自动等凉、自动推
- [ ] 多件一盘、底面更大的件
- [ ] 前沿限位，防止钢板被推走
- [ ] 出口接件盒的打印件

## English summary

Push PETG parts off a Bambu Lab X2D plate with the toolhead, no add-ons. **Experimental: worked once (2026-10-11).**
Conditions that made the difference: first-layer bed **70 °C (not 80 °C)**, **no brim**, wait until the bed is **≤ 35 °C** (Bambu's own textured PEI guidance), small single part, a catch box at the front.
Known issues: the nozzle load line at the front edge stays on the plate; nothing stops the steel plate from sliding forward if a part sticks.
`x2d_cold_push.py` talks to the printer over LAN MQTT (developer mode, `gcode_line`). Use at your own risk.

## 免责声明

开发者模式下手动发运动指令，可能撞头、刮板、把钢板推走。推的时候人在旁边、手边就是电源开关。风险自负。

## License

MIT
