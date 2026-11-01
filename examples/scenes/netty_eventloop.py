"""Own ordinary Manim Scene; model time is explicitly separate from teaching pauses."""

from manim import (
    Dot,
    FadeIn,
    FadeOut,
    Indicate,
    LEFT,
    Line,
    Rectangle,
    RoundedRectangle,
    Scene,
    Succession,
    Text,
    Transform,
    VGroup,
    ValueTracker,
    linear,
)
from scene_font import scene_font

BG = "#101923"
WHITE_INK = "#EDF3F7"
MUTED = "#A6BAC9"
A_BLUE = "#65BFFF"
B_GREEN = "#77D6A2"
YELLOW_LOOP = "#FFD166"
PURPLE_WORK = "#C397FF"
RED_BUSY = "#FF8585"
FONT = scene_font(
    "·→≠、一不与业两个中为么也了二于互交享什仍任会但位住作停先入共"
    "关再写到刻功务动化单占却发变口另只可同后器回固在型处外始完定实客"
    "容察就工已序应度开异式径很得快意慢成户执把投接提收改放时是显暂有"
    "服未束样格模次步求没测然理由的直相短示移程空端第等管线终经结给络"
    "绪网置能自行观试请调走起超路轮轻返还这进送途释量销间需非面顺，："
    "；？"
)
# Shared illustrative event table, identical in both rounds.
A_START, B_READY, A_DONE, B_WORK = 0, 2, 8, 1
WALL_SECONDS_PER_MODEL_UNIT = 2


class NettyPuzzleComparison(Scene):
    def construct(self):
        self.camera.background_color = BG
        model = ValueTracker(0)
        round_number = 1

        def text(value, point, size=27, color=WHITE_INK, width=12):
            obj = Text(value, font=FONT, font_size=size, color=color, line_spacing=1.1)
            if obj.width > width:
                obj.scale_to_fit_width(width)
            return obj.move_to(point)

        def box(point, width, height, color=MUTED):
            return RoundedRectangle(
                width=width,
                height=height,
                corner_radius=0.12,
                stroke_color=color,
                stroke_width=2,
                fill_color=color,
                fill_opacity=0.045,
            ).move_to(point)

        def card(value, point, color, width=1.35):
            return VGroup(
                box(point, width, 0.62, color).set_fill(BG, opacity=1),
                text(value, point, 25, color, width - 0.12),
            ).set_z_index(5)

        def swap(obj, value, point, size=27, color=WHITE_INK, width=12):
            target = text(value, point, size, color, width)
            self.play(Succession(FadeOut(obj), FadeIn(target)), run_time=0.3)
            self.remove(target)
            obj.become(target).set_opacity(1)
            self.add(obj)

        def audit(event):
            print(
                f"MODEL_EVENT round={round_number} event={event} t={model.get_value():.6f} scene={self.time:.6f}"  # noqa: E501
            )

        def until(end):
            if end - self.time > 0.02:
                self.wait(end - self.time)
            print(
                f"SCENE_BOUNDARY requested={end:.3f} actual={self.time:.6f} model={model.get_value():.3f}"  # noqa: E501
            )

        A_CONN, B_CONN = [-5.55, 0.95, 0], [-5.55, -0.7, 0]
        B_WAIT = [-3.3, -0.7, 0]
        A_INPUT, B_INPUT = [-3.85, 0.95, 0], [-3.85, -0.7, 0]
        ACTIVE, POINTER = [0.15, 0.5, 0], [0.15, 1.3, 0]
        IDLE = [-1.9, 0.5, 0]
        WORK = [5.0, 0.5, 0]
        RETURN = [0, -0.63, 0]
        OUT_A, OUT_B = [1.25, -1.35, 0], [-1.25, -1.35, 0]
        title = text("B 很快，为什么也得等 A？", [0, 3.45, 0], 35)
        caption = text("A、B 是互不相关的两个请求", [0, 2.65, 0], 28)
        round_badge = text("第一轮：直接执行", [-5.65, 3.5, 0], 20, MUTED, 2.65)
        self.add(
            title,
            caption,
            round_badge,
            box(A_CONN, 1.9, 1.05, A_BLUE),
            text("A\n慢工作", A_CONN, 26, A_BLUE, 1.7),
            box(B_CONN, 1.9, 1.05, B_GREEN),
            text("B\n轻工作", B_CONN, 26, B_GREEN, 1.7),
        )
        facade = box([1.25, 0.25, 0], 7.0, 3.1, MUTED)
        facade_name = text("同一服务入口", [1.25, 1.45, 0], 28)
        self.add(facade, facade_name)
        a = card("A", A_INPUT, A_BLUE)
        b = card("B", B_INPUT, B_GREEN)
        occupied = box(ACTIVE, 3.5, 1.15, RED_BUSY)
        busy_label = text("A 的慢工作进行中", [0.15, -0.27, 0], 23, A_BLUE, 3.6)

        # One physical clock object and one A progress bar for both trials.
        clock_line = Line([-4.5, -2.65, 0], [4.5, -2.65, 0], color=MUTED)
        clock_ticks = VGroup(
            *[
                VGroup(
                    Line([-4.5 + t, -2.73, 0], [-4.5 + t, -2.57, 0], color=MUTED),
                    text(str(t), [-4.5 + t, -3.0, 0], 20, MUTED),
                )
                for t in (0, 2, 4, 6, 8, 9)
            ]
        )
        progress_bg = Rectangle(
            width=8, height=0.12, stroke_width=0, fill_color=A_BLUE, fill_opacity=0.16
        ).move_to([-0.5, -2.25, 0])
        progress_fill = (
            Rectangle(width=0.02, height=0.12, stroke_width=0, fill_color=A_BLUE, fill_opacity=1)
            .align_to(progress_bg, LEFT)
            .set_y(-2.25)
        )
        progress_fill.add_updater(
            lambda m: m.stretch_to_fit_width(max(0.02, min(model.get_value(), A_DONE))).align_to(
                progress_bg, LEFT
            )
        )
        clock_pointer = Dot([-4.5, -2.65, 0], radius=0.07, color=WHITE_INK)
        clock_pointer.add_updater(lambda m: m.move_to([-4.5 + model.get_value(), -2.65, 0]))
        clock_caption = text("A 的慢工作：固定 8 格", [-2.8, -1.92, 0], 23, A_BLUE, 4.1)
        clock_scope = text("t 为示意刻度，非实测", [3.25, -1.92, 0], 20, MUTED, 4.0)
        pause_label = text("暂停观察 · 模型时间不走", [0, -3.55, 0], 23, YELLOW_LOOP)
        self.add(
            clock_line,
            clock_ticks,
            progress_bg,
            progress_fill,
            clock_pointer,
            clock_caption,
            clock_scope,
        )

        def pause(value=None):
            self.add(pause_label)
            audit("pause")
            if value:
                swap(caption, value, [0, 2.65, 0], 28)

        def resume():
            self.remove(pause_label)
            audit("resume")

        def advance(to, *animations):
            previous = model.get_value()
            self.play(
                model.animate.set_value(to),
                *animations,
                run_time=(to - previous) * WALL_SECONDS_PER_MODEL_UNIT,
                rate_func=linear,
            )
            audit("advance")

        def b_arrives():
            advance(1.5)
            self.add(b)
            advance(B_READY, b.animate.move_to(B_WAIT))
            audit("B_ready")

        def written(obj, name, point, color):
            self.play(Transform(obj, card(name + " 写已发起", point, color, 2.3)), run_time=0.8)

        # Puzzle: the visible busy entrance gives B a reason to wait, not a random stop.
        until(2)
        self.add(a)
        self.play(a.animate.move_to(ACTIVE), run_time=2)
        self.add(occupied, busy_label)
        audit("A_work_start")
        b_arrives()
        pause("B 的工作很短，为什么停在外面？")
        self.wait(2)
        until(14)

        # Reveal during an explicit model pause: A, B and all progress stay frozen at t=2.
        track = box([0, 0.425, 0], 4.8, 2.75, YELLOW_LOOP)
        rail = Line([-2.1, 0.5, 0], [2.1, 0.5, 0], color=YELLOW_LOOP).set_opacity(0.45)
        loop_dot = Dot(POINTER, radius=0.12, color=YELLOW_LOOP).set_z_index(8)
        loop_name = text("同一个执行位", [0, 1.98, 0], 26, YELLOW_LOOP, 4.8)
        ready_name = text("网络就绪", [-3.3, -1.22, 0], 20, B_GREEN, 1.8)
        self.play(
            Transform(facade, track),
            FadeOut(facade_name),
            FadeIn(rail),
            FadeIn(loop_dot),
            FadeIn(loop_name),
            FadeIn(ready_name),
            run_time=1.2,
        )
        pause("A 的回调没有返回，这个位置就不能处理 B")
        until(23)
        swap(loop_name, "共享 EventLoop · 单线程", [0, 1.98, 0], 25, YELLOW_LOOP, 4.8)
        swap(caption, "网络异步，不会自动移交慢业务", [0, 2.65, 0], 28)
        until(26)
        resume()
        advance(A_DONE)
        audit("A_work_done")
        pause("A 返回，执行位终于释放")
        self.play(FadeOut(occupied), FadeOut(busy_label), run_time=0.3)
        written(a, "A", OUT_A, A_BLUE)
        self.play(loop_dot.animate.move_to(IDLE), run_time=0.4)
        self.play(b.animate.move_to(ACTIVE), loop_dot.animate.move_to(POINTER), run_time=0.8)
        audit("B_work_start")
        resume()
        advance(A_DONE + B_WORK, Indicate(b, color=B_GREEN))
        audit("B_work_done")
        pause("B 只需 1 格，却等到了 A 结束")
        written(b, "B", OUT_B, B_GREEN)
        self.play(loop_dot.animate.move_to(IDLE), run_time=0.4)
        until(48)

        # Prepare the alternate executor BEFORE any new request, never move blocked A away.
        round_number = 2
        swap(round_badge, "第二轮：显式移交", [-5.65, 3.5, 0], 20, MUTED, 2.65)
        pause("同样请求，再试一次；只改变慢工作的执行位置")
        self.play(FadeOut(a), FadeOut(b), FadeOut(ready_name), run_time=0.3)
        self.remove(clock_pointer, progress_fill)
        model.set_value(A_START)
        self.add(clock_pointer, progress_fill)
        worker = box([5, 0.3, 0], 2.9, 3.0, PURPLE_WORK)
        worker_name = text("业务执行器", [5, 1.98, 0], 25, PURPLE_WORK, 2.8)
        worker_dot = Dot([5, 1.3, 0], radius=0.12, color=PURPLE_WORK)
        self.play(FadeIn(worker), FadeIn(worker_name), run_time=0.8)
        swap(caption, "同样：A 慢工作 8 格；B 在 t=2 就绪", [0, 2.65, 0], 27)
        until(56)
        a = card("A", A_INPUT, A_BLUE)
        b = card("B", B_INPUT, B_GREEN)
        self.add(a)
        self.play(a.animate.move_to(ACTIVE), loop_dot.animate.move_to(POINTER), run_time=1)
        swap(caption, "把 A 的慢工作，交给业务执行器", [0, 2.65, 0], 27, PURPLE_WORK)
        self.play(a.animate.move_to(WORK), FadeIn(worker_dot), run_time=1)
        self.play(loop_dot.animate.move_to(IDLE), run_time=0.4)
        audit("loop_callback_returned")
        resume()
        audit("A_work_start")
        b_arrives()
        pause("A 还没结束，但共享执行位已经空了")
        self.wait(1)
        self.play(b.animate.move_to(ACTIVE), loop_dot.animate.move_to(POINTER), run_time=0.8)
        audit("B_work_start")
        resume()
        # The SAME model clock advances A and B together; B takes precisely one unit.
        advance(B_READY + B_WORK, Indicate(b, color=B_GREEN))
        audit("B_work_done")
        pause("B 已得到处理，开始发送回应")
        written(b, "B", OUT_B, B_GREEN)
        self.play(loop_dot.animate.move_to(IDLE), run_time=0.4)
        until(74)
        resume()
        advance(A_DONE)
        audit("A_work_done")
        pause("慢工作没有变快：仍然是 8 格")
        until(89)

        # Only now introduce completion-posting details, with model time explicitly paused.
        completion_slot = box([0, -0.62, 0], 3.7, 0.55, PURPLE_WORK)
        completion_label = text("回投任务", [0, -0.14, 0], 21, PURPLE_WORK)
        swap(caption, "完成后，先回投 EventLoop", [0, 2.65, 0], 28)
        self.play(FadeIn(completion_slot), FadeIn(completion_label), run_time=0.5)
        completion = card("完成任务 A", WORK, PURPLE_WORK, 2.1)
        self.play(FadeOut(a), FadeIn(completion), FadeOut(worker_dot), run_time=0.3)
        self.play(completion.animate.move_to(RETURN), run_time=1.2)
        swap(caption, "eventLoop.execute：提交完成任务", [0, 2.65, 0], 27, PURPLE_WORK)
        self.wait(1.2)
        self.play(completion.animate.move_to(ACTIVE), loop_dot.animate.move_to(POINTER), run_time=1)
        swap(caption, "由这个 EventLoop 发起 writeAndFlush", [0, 2.65, 0], 27, A_BLUE)
        written(completion, "A", OUT_A, A_BLUE)
        self.play(loop_dot.animate.move_to(IDLE), run_time=0.4)
        swap(caption, "写已发起 ≠ 客户端已收到", [0, 2.65, 0], 28)
        swap(clock_scope, "提交 / 回投 / 调度 / 回写开销未量化", [2.8, -1.92, 0], 19, MUTED, 5)
        until(108)

        self.play(
            worker.animate.set_opacity(0.25),
            worker_name.animate.set_opacity(0.3),
            completion_slot.animate.set_opacity(0.2),
            completion_label.animate.set_opacity(0.2),
            run_time=0.4,
        )
        swap(title, "占住共享线程 → B 等", [0, 3.45, 0], 34)
        swap(caption, "显式移交 → 线程可处理 B", [0, 2.65, 0], 30)
        swap(pause_label, "单 A 在途成功路径；容量、超时与顺序另管", [0, -3.55, 0], 20, MUTED)
        until(120)
