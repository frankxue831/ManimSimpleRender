from manim import *

class TestDebuggingWorkflow(Scene):
    def construct(self):
        # Title - keep persistent
        self.title = Text("Test Debugging Workflow", font_size=36, color=WHITE)
        self.title.to_edge(UP, buff=0.5)
        self.play(Write(self.title))
        self.wait(1)
        
        # Phase 1: Initial Assessment
        self.show_initial_assessment()
        self.wait(1.5)
        
        # Phase 2: Root Cause Analysis
        self.show_root_cause_analysis()
        self.wait(1.5)
        
        # Phase 3: Diagnosis
        self.show_diagnosis()
        self.wait(1.5)
        
        # Phase 4: Fix Implementation
        self.show_fix_implementation()
        self.wait(1.5)
        
        # Phase 5: Validation by Component
        self.show_validation()
        self.wait(1.5)
        
        # Phase 6: Handle Edge Cases
        self.show_edge_cases()
        self.wait(1.5)
        
        # Phase 7: Measure & Iterate
        self.show_measure_iterate()
        self.wait(1.5)
        
        # Phase 8: Deployment Readiness
        self.show_deployment_readiness()
        self.wait(2)

    def show_initial_assessment(self):
        assessment_box = RoundedRectangle(
            width=10, height=2, corner_radius=0.3,
            fill_color=RED_E, fill_opacity=0.8,
            stroke_color=RED, stroke_width=3
        ).move_to([0, 1, 0])
        
        assessment_text = VGroup(
            Text("Identify Failed Tests", font_size=24, color=WHITE, weight=BOLD),
            Text("(Count, Services Affected)", font_size=18, color=WHITE)
        ).arrange(DOWN, buff=0.2).move_to(assessment_box)
        
        assessment_group = VGroup(assessment_box, assessment_text)
        
        self.play(
            FadeIn(assessment_box),
            Write(assessment_text),
            run_time=1.5
        )
        
        # Store for later reference
        self.assessment_group = assessment_group

    def show_root_cause_analysis(self):
        # Clear previous and set up new layout
        self.play(FadeOut(self.assessment_group), run_time=0.8)
        
        # Analysis diamond
        analysis_diamond = Polygon(
            [0, 1, 0], [1.2, 0, 0], [0, -1, 0], [-1.2, 0, 0],
            fill_color=BLUE_E, fill_opacity=0.7,
            stroke_color=BLUE, stroke_width=2
        ).move_to([0, 1, 0])
        
        analysis_text = Text("Debug &\nCategorize", font_size=18, color=WHITE).move_to(analysis_diamond)
        
        # Two branches - positioned lower to avoid overlap
        code_box = RoundedRectangle(
            width=3.5, height=1.5, corner_radius=0.2,
            fill_color=BLUE_E, fill_opacity=0.6,
            stroke_color=BLUE, stroke_width=2
        ).move_to([-3, -1, 0])
        
        code_text = VGroup(
            Text("Check Code Logic?", font_size=14, color=WHITE),
            Text("(Bugs in Source)", font_size=12, color=WHITE)
        ).arrange(DOWN, buff=0.1).move_to(code_box)
        
        infra_box = RoundedRectangle(
            width=3.5, height=1.5, corner_radius=0.2,
            fill_color=BLUE_E, fill_opacity=0.6,
            stroke_color=BLUE, stroke_width=2
        ).move_to([3, -1, 0])
        
        infra_text = VGroup(
            Text("Check Test Infra?", font_size=14, color=WHITE),
            Text("(Mocks, Timing)", font_size=12, color=WHITE)
        ).arrange(DOWN, buff=0.1).move_to(infra_box)
        
        # Arrows
        arrow_left = Arrow(analysis_diamond.get_left(), code_box.get_top(), buff=0.1, stroke_width=2)
        arrow_right = Arrow(analysis_diamond.get_right(), infra_box.get_top(), buff=0.1, stroke_width=2)
        
        self.play(
            FadeIn(analysis_diamond),
            Write(analysis_text),
            run_time=1
        )
        
        self.play(
            GrowArrow(arrow_left),
            GrowArrow(arrow_right),
            FadeIn(code_box),
            FadeIn(infra_box),
            Write(code_text),
            Write(infra_text),
            run_time=1.5
        )
        
        # Store references
        self.analysis_group = VGroup(
            analysis_diamond, analysis_text,
            code_box, code_text, infra_box, infra_text,
            arrow_left, arrow_right
        )

    def show_diagnosis(self):
        # Clear previous and set up new layout
        self.play(FadeOut(self.analysis_group), run_time=0.8)
        
        # Diagnosis boxes
        code_issues_box = RoundedRectangle(
            width=4, height=1.8, corner_radius=0.2,
            fill_color=GRAY_E, fill_opacity=0.6,
            stroke_color=GRAY, stroke_width=2
        ).move_to([-3, 0.5, 0])
        
        code_issues_text = VGroup(
            Text("Code Issues", font_size=16, color=WHITE, weight=BOLD),
            Text("(Rare)", font_size=14, color=WHITE)
        ).arrange(DOWN, buff=0.1).move_to(code_issues_box)
        
        infra_mismatch_box = RoundedRectangle(
            width=4, height=1.8, corner_radius=0.2,
            fill_color=GREEN_E, fill_opacity=0.6,
            stroke_color=GREEN, stroke_width=2
        ).move_to([3, 0.5, 0])
        
        infra_mismatch_text = VGroup(
            Text("Infra/Env Issues", font_size=16, color=WHITE, weight=BOLD),
            Text("(Common)", font_size=14, color=WHITE)
        ).arrange(DOWN, buff=0.1).move_to(infra_mismatch_box)
        
        self.play(
            FadeIn(code_issues_box),
            FadeIn(infra_mismatch_box),
            Write(code_issues_text),
            Write(infra_mismatch_text),
            run_time=1.5
        )
        
        self.diagnosis_group = VGroup(code_issues_box, code_issues_text, infra_mismatch_box, infra_mismatch_text)

    def show_fix_implementation(self):
        # Clear previous
        self.play(FadeOut(self.diagnosis_group), run_time=0.8)
        
        # Fix implementation box
        fixes_box = RoundedRectangle(
            width=11, height=3.5, corner_radius=0.3,
            fill_color=GREEN_E, fill_opacity=0.7,
            stroke_color=GREEN, stroke_width=3
        ).move_to([0, 0, 0])
        
        fixes_title = Text("Apply Targeted Fixes", font_size=22, color=WHITE, weight=BOLD)
        
        fixes_text = VGroup(
            Text("• Mock External Dependencies", font_size=16, color=WHITE),
            Text("• Handle Async Operations", font_size=16, color=WHITE),
            Text("• Align Assertions to Reality", font_size=16, color=WHITE),
            Text("• Use Fake Timers", font_size=16, color=WHITE),
            Text("• Deduplicate Requests", font_size=16, color=WHITE)
        ).arrange(DOWN, buff=0.25, aligned_edge=LEFT)
        
        # Arrange title and content
        content = VGroup(fixes_title, fixes_text).arrange(DOWN, buff=0.4).move_to(fixes_box)
        
        self.play(
            FadeIn(fixes_box),
            Write(content),
            run_time=2
        )
        
        self.fixes_group = VGroup(fixes_box, content)

    def show_validation(self):
        # Clear previous
        self.play(FadeOut(self.fixes_group), run_time=0.8)
        
        # Validation box
        validation_box = RoundedRectangle(
            width=10, height=2.2, corner_radius=0.3,
            fill_color=GREEN_E, fill_opacity=0.8,
            stroke_color=GREEN, stroke_width=3
        ).move_to([0, 0, 0])
        
        validation_text = VGroup(
            Text("Test Components Individually", font_size=20, color=WHITE, weight=BOLD),
            Text("(Unit Tests for UI, Services, Hooks)", font_size=16, color=WHITE)
        ).arrange(DOWN, buff=0.3).move_to(validation_box)
        
        self.play(
            FadeIn(validation_box),
            Write(validation_text),
            run_time=1.5
        )
        
        self.validation_group = VGroup(validation_box, validation_text)

    def show_edge_cases(self):
        # Clear previous
        self.play(FadeOut(self.validation_group), run_time=0.8)
        
        # Edge cases box
        edge_cases_box = RoundedRectangle(
            width=10, height=2.2, corner_radius=0.3,
            fill_color=YELLOW_E, fill_opacity=0.7,
            stroke_color=YELLOW, stroke_width=3
        ).move_to([0, 0, 0])
        
        edge_cases_text = VGroup(
            Text("Address Integrations", font_size=20, color=BLACK, weight=BOLD),
            Text("(Timeouts, State Mocks, Config)", font_size=16, color=BLACK)
        ).arrange(DOWN, buff=0.3).move_to(edge_cases_box)
        
        self.play(
            FadeIn(edge_cases_box),
            Write(edge_cases_text),
            run_time=1.5
        )
        
        self.edge_cases_group = VGroup(edge_cases_box, edge_cases_text)

    def show_measure_iterate(self):
        # Clear previous
        self.play(FadeOut(self.edge_cases_group), run_time=0.8)
        
        # Metrics box
        metrics_box = RoundedRectangle(
            width=10, height=2.5, corner_radius=0.3,
            fill_color=BLUE_E, fill_opacity=0.8,
            stroke_color=BLUE, stroke_width=3
        ).move_to([0, 0, 0])
        
        metrics_text = VGroup(
            Text("Track Metrics", font_size=22, color=WHITE, weight=BOLD),
            Text("(Reduction %, Passing Rate)", font_size=18, color=WHITE),
            Text("Validate Core Logic", font_size=18, color=WHITE)
        ).arrange(DOWN, buff=0.25).move_to(metrics_box)
        
        self.play(
            FadeIn(metrics_box),
            Write(metrics_text),
            run_time=1.5
        )
        
        self.metrics_group = VGroup(metrics_box, metrics_text)

    def show_deployment_readiness(self):
        # Clear previous
        self.play(FadeOut(self.metrics_group), run_time=0.8)
        
        # Deployment ready box
        ready_box = RoundedRectangle(
            width=10, height=2.5, corner_radius=0.3,
            fill_color=GREEN_E, fill_opacity=0.8,
            stroke_color=GREEN, stroke_width=3
        ).move_to([0, 0, 0])
        
        ready_text = VGroup(
            Text("Production-Ready", font_size=24, color=WHITE, weight=BOLD),
            Text("(All Critical Paths Passing)", font_size=18, color=WHITE)
        ).arrange(DOWN, buff=0.3).move_to(ready_box)
        
        self.play(
            FadeIn(ready_box),
            Write(ready_text),
            run_time=1.5
        )
        
        # Success indicator
        success_text = Text("SUCCESS!", font_size=32, color=GREEN, weight=BOLD)
        success_text.next_to(ready_box, DOWN, buff=0.5)
        
        self.play(Write(success_text), run_time=1)
        self.wait(1)
        
        # Simple celebration
        self.play(
            ready_box.animate.set_fill(GREEN, opacity=0.9),
            success_text.animate.scale(1.2),
            run_time=1
        )


class TestDebuggingWorkflowDetailed(Scene):
    """Detailed version with step-by-step progression"""
    
    def construct(self):
        title = Text("Detailed Test Debugging Journey", font_size=40, color=WHITE)
        title.to_edge(UP, buff=0.5)
        self.play(Write(title))
        
        # Show the progression with metrics
        self.show_progression_with_metrics()
        
    def show_progression_with_metrics(self):
        # Progress bar background
        progress_bg = Rectangle(width=10, height=0.8, fill_color=GRAY, fill_opacity=0.3)
        progress_bg.shift(UP * 2)
        
        # Initial state
        total_tests = 156  # Approximate total
        
        # Create progress sections
        stages = [
            ("Initial Crisis", 39, RED),
            ("After UserService", 23, ORANGE),
            ("After CacheService", 14, YELLOW),
            ("After WikiService", 12, LIGHT_BROWN),
            ("After Components", 9, GREEN),
            ("Final State", 9, BLUE)
        ]
        
        self.play(FadeIn(progress_bg))
        
        for i, (stage_name, failures, color) in enumerate(stages):
            # Calculate progress
            success_rate = ((total_tests - failures) / total_tests) * 100
            progress_width = (success_rate / 100) * 10
            
            # Progress bar
            progress_bar = Rectangle(
                width=progress_width, height=0.8,
                fill_color=color, fill_opacity=0.7
            ).align_to(progress_bg, LEFT).move_to(progress_bg)
            
            # Metrics text
            metrics_text = VGroup(
                Text(stage_name, font_size=24, color=WHITE, weight=BOLD),
                Text(f"{failures} Failed Tests", font_size=20, color=RED if failures > 9 else GREEN),
                Text(f"{success_rate:.1f}% Passing", font_size=18, color=WHITE)
            ).arrange(DOWN, buff=0.2).next_to(progress_bg, DOWN, buff=1)
            
            if i == 0:
                self.play(
                    FadeIn(progress_bar),
                    Write(metrics_text),
                    run_time=1.5
                )
            else:
                self.play(
                    ReplacementTransform(prev_progress, progress_bar),
                    ReplacementTransform(prev_metrics, metrics_text),
                    run_time=1.5
                )
            
            prev_progress = progress_bar
            prev_metrics = metrics_text
            self.wait(1)