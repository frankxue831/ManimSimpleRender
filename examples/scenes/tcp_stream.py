from manim import (
    Create,
    DOWN,
    FadeIn,
    FadeOut,
    Line,
    ORIGIN,
    RoundedRectangle,
    Scene,
    SurroundingRectangle,
    Text,
    Transform,
    VGroup,
)
from scene_font import scene_font

BG = "#101923"
INK = "#EDF3F7"
MUT = "#A6BAC9"
A = "#65BFFF"
B = "#77D6A2"
Y = "#FFD166"
FONT = scene_font(
    "§·“”→≥、。一三不两串为么交什仅付但余例依俗保值先入六再写冲"
    "出分到制剩加动包区十协原取另只可号同后回在块处够大头字完定实己已"
    "带并序应度待循怎息意找抓拆据接收数整文断无是最有条来析查标格检次"
    "正每法流消清源环理用画界留的示种称空端符等算粘约络继续缓网能自色"
    "节要解计议记读边返还这进连追都里长际限需非靠颜（），：；？"
)


class TCPStream(Scene):
    def construct(self):
        self.camera.background_color = BG

        def txt(s, p, size=28, c=INK):
            m = Text(s, font=FONT, font_size=size, color=c)
            m.scale(min(1, 12 / m.width))
            return m.move_to(p)

        def cell(s, p, c):
            return VGroup(
                RoundedRectangle(
                    width=0.76,
                    height=0.67,
                    corner_radius=0.08,
                    stroke_color=c,
                    fill_color=c,
                    fill_opacity=0.13,
                ),
                txt(s, ORIGIN, 23, c),
            ).move_to(p)

        def until(t):
            if t > self.time:
                self.wait(t - self.time)

        title = txt("两次 write，为什么不是两次 read？", [0, 3.35, 0], 36)
        cap = txt("A = CAT（3 字节）    B = HELLO（5 字节）", [0, 2.48, 0])
        self.add(title, cap)
        foot = txt("每格 1 字节 · 颜色只标记来源，不是 TCP 自带的边界", [0, -3.45, 0], 21, MUT)
        self.add(foot)
        xs = [-3.5 + i for i in range(8)]
        data = VGroup(
            *[cell(s, [xs[i], 0.7, 0], A if i < 3 else B) for i, s in enumerate("CATHELLO")]
        )
        wa = txt("write(A)", [-4.7, 1.55, 0], 24, A)
        wb = txt("write(B)", [2.6, 1.55, 0], 24, B)
        self.play(FadeIn(data[:3]), FadeIn(wa), run_time=1.5)
        until(5)
        self.play(FadeIn(data[3:]), FadeIn(wb), run_time=1.5)
        until(9)
        rail = Line([-5, 0.05, 0], [5, 0.05, 0], color=MUT)
        self.play(
            FadeIn(rail), data.animate.shift(DOWN * 0.8), FadeOut(wa), FadeOut(wb), run_time=2
        )
        note = txt("TCP：可靠、有序的字节流", [0, 1.45, 0], 30, Y)
        self.play(FadeIn(note))
        until(16)

        def caption(s):
            self.play(Transform(cap, txt(s, [0, 2.48, 0])), run_time=0.5)

        caption("一种可能：一次 read 读到 A + B")
        block = SurroundingRectangle(data, buff=0.14, color=Y)
        label = txt("read #1：CATHELLO（8 字节）", [0, -1.35, 0], 28, Y)
        self.play(Create(block), FadeIn(label), run_time=1.5)
        until(24)
        caption("另一种可能：同一串字节，分三次 read")
        self.play(FadeOut(block), FadeOut(label))
        for inds, s in [
            ([0, 1], "read #1：CA（2 字节）"),
            ([2, 3, 4, 5], "read #2：THEL（4 字节）"),
            ([6, 7], "read #3：LO（2 字节）"),
        ]:
            r = SurroundingRectangle(VGroup(*[data[i] for i in inds]), buff=0.13, color=Y)
            read_label = txt(s, [0, -1.35, 0], 28, Y)
            self.play(Create(r), FadeIn(read_label), run_time=0.7)
            self.wait(2.3)
            self.play(FadeOut(r), FadeOut(read_label), run_time=0.3)
        caption("俗称“粘包 / 拆包”：应用需要自己找消息边界")
        caution = txt("这里画的是 read 返回的数据块，不是网络包", [0, -1.5, 0], 25, MUT)
        self.play(FadeIn(caution))
        until(41)
        self.play(*[FadeOut(m) for m in [data, rail, note, caution]], run_time=0.6)
        self.play(Transform(title, txt("在字节流里，写入消息边界", [0, 3.35, 0], 36)))
        caption("约定：2 字节无符号大端长度头，数值只计算正文")
        vals = ["00", "03", "C", "A", "T", "00", "05", "H", "E", "L", "L", "O"]
        stream = VGroup(
            *[
                cell(s, [-5.5 + i, 1.05, 0], Y if i in [0, 1, 5, 6] else (A if i < 5 else B))
                for i, s in enumerate(vals)
            ]
        )
        stream.scale(0.88)
        self.play(FadeIn(stream), run_time=1)
        fmt = txt("头：十六进制 00 03 / 00 05；正文：ASCII 字节", [0, 1.95, 0], 24, MUT)
        self.play(FadeIn(fmt))
        bufbox = RoundedRectangle(
            width=12, height=1.25, corner_radius=0.12, stroke_color=MUT
        ).move_to([0, -0.65, 0])
        bn = txt("接收缓冲区：追加数据，够一条再取出", [0, -1.65, 0], 26)
        self.add(bufbox, bn)
        until(50)
        status = txt("read #1：仅到 00，连长度头都不够", [0, -2.55, 0], 27, Y)
        self.add(status)
        received = []

        def append(ids):
            moves = []
            for i in ids:
                received.append(i)
                moves.append(
                    stream[i].animate.move_to([-5.3 + (len(received) - 1) * 0.87, -0.65, 0])
                )
            self.play(*moves, run_time=1.6)

        def state(s):
            self.play(Transform(status, txt(s, [0, -2.55, 0], 27, Y)), run_time=0.4)

        append([0])
        until(57)
        state("read #2：追加 03 C；L = 3，但正文只有 1 字节")
        append([1, 2])
        until(65)
        state("还需 2 字节：保留 00 03 C，等待后续数据")
        until(69)
        state("read #3：追加 A T 00 05 H E")
        append([3, 4, 5, 6, 7, 8])
        until(74)
        state("缓冲区 9 字节 ≥ 2 + 3：取出 A，剩余 4 字节")
        self.play(
            *[stream[i].animate.move_to([-4.8 + i * 0.87, -0.65, 0]) for i in range(9)],
            run_time=0.4,
        )
        self.play(
            *[stream[i].animate.shift(DOWN * 1.4).set_opacity(0) for i in range(5)], run_time=1
        )
        received[:] = [5, 6, 7, 8]
        self.play(
            *[
                stream[i].animate.move_to([-5.3 + j * 0.87, -0.65, 0])
                for j, i in enumerate(received)
            ],
            run_time=1,
        )
        outa = txt("已交付 A：CAT", [-3.2, 1.05, 0], 25, A)
        self.add(outa)
        until(81)
        state("继续解析余数：L = 5，正文只有 HE（2 字节）")
        until(86)
        state("read #4：追加 L L O；已有 2 + 5 字节，B 完整")
        append([9, 10, 11])
        until(92)
        self.play(
            *[stream[i].animate.shift(DOWN * 1.4).set_opacity(0) for i in received], run_time=1
        )
        outb = txt("已交付 B：HELLO", [1.8, 1.05, 0], 25, B)
        self.add(outb)
        state("缓冲区清空。read 怎么分，消息 A / B 都能还原。")
        until(99)
        caption("先等够 2 字节头 → 检查长度 → 等够 2 + L → 取出并循环")
        self.play(
            Transform(
                bn, txt("实际协议还需限制最大长度，并处理断流 / 非法数据", [0, -1.9, 0], 23, MUT)
            )
        )
        self.play(
            Transform(
                foot,
                txt(
                    "示意动画，非抓包 · 依据 RFC 9293 §2.2 / §3.7 · 长度头为应用协议示例",
                    [0, -3.45, 0],
                    19,
                    MUT,
                ),
            )
        )
        until(108)
