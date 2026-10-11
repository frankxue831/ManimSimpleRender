"""
An improved test debugging workflow animation using Manim.
This module creates professional animations demonstrating debugging processes.
"""

from manim import (
    Scene, Text, VGroup, RoundedRectangle, Polygon, Arrow,
    FadeIn, FadeOut, Write, GrowArrow,
    RED, GREEN, BLUE, YELLOW, WHITE, BLACK, ORANGE, GRAY,
    RED_E, GREEN_E, BLUE_E, YELLOW_E, GRAY_D,
    UP, DOWN, LEFT, RIGHT,
    Indicate, Flash, interpolate_color
)


class TestDebuggingWorkflowImproved(Scene):
    """
    An improved and polished version of the test debugging workflow animation.
    This version fixes layout overlaps, improves typography for readability,
    and refines animation choreography for a smoother, more professional presentation.
    """

    def __init__(self, **kwargs):
        """Initialize the scene with required attributes."""
        super().__init__(**kwargs)
        self.all_phases = VGroup()
        self.connections = VGroup()

    def construct(self):
        """Construct the main animation sequence."""
        # Set a dark, modern background color
        self.camera.background_color = "#1E1E1E"

        # --- Title ---
        title = Text(
            "A Modern Test Debugging Workflow",
            font_size=36,
            color=WHITE,
            weight="BOLD"
        )
        title.to_edge(UP, buff=0.4)

        self.play(Write(title), run_time=1.5)
        self.play(Indicate(title, color=YELLOW, scale_factor=1.05))
        self.wait(0.5)

        # --- Build Flowchart ---
        self.build_workflow_progressively()

        # --- Final Overview ---
        self.final_overview()

    def build_workflow_progressively(self):
        """Build the flowchart step-by-step with clear connections."""

        # Define vertical positions for each phase to ensure no overlap
        positions = [
            [0, 2.5, 0],    # Initial Assessment
            [0, 1.0, 0],    # Root Cause Analysis
            [0, -0.5, 0],   # Diagnosis
            [0, -2.0, 0],   # Fix Implementation
            [0, -3.5, 0],   # Validation & Deployment
        ]

        # List of creator functions for each phase
        phase_creators = [
            self.create_assessment_phase,
            self.create_analysis_phase,
            self.create_diagnosis_phase,
            self.create_fixes_phase,
            self.create_validation_deployment_phase,
        ]

        # Animate each phase and its connecting arrow
        for i, (position, creator) in enumerate(zip(positions, phase_creators)):
            phase_group = creator(position)
            self.all_phases.add(phase_group)

            if i > 0:
                # Get the bottom of the previous box and top of the current one
                prev_box = self.all_phases[i-1].get_critical_point(DOWN)
                curr_box = phase_group.get_critical_point(UP)
                arrow = self.create_connection_arrow(prev_box, curr_box)
                self.connections.add(arrow)

                self.play(
                    GrowArrow(arrow),
                    FadeIn(phase_group, shift=DOWN * 0.5),
                    run_time=1.2
                )
            else:
                self.play(FadeIn(phase_group, shift=DOWN * 0.5), run_time=1)

            self.wait(0.4)

    def create_phase_box(self, position, width, height, text_content,
                        sub_text_content, box_color, text_color=WHITE,
                        icon=None):
        """Helper function to create a standard phase box."""
        box = RoundedRectangle(
            width=width, height=height, corner_radius=0.15,
            fill_color=box_color, fill_opacity=0.9,
            stroke_color=interpolate_color(box_color, BLACK, 0.3), stroke_width=2
        ).move_to(position)

        main_text = Text(text_content, font_size=20, color=text_color,
                        weight="BOLD")
        sub_text = Text(sub_text_content, font_size=15, color=WHITE)
        text_group = VGroup(main_text, sub_text).arrange(
            DOWN, buff=0.15).move_to(box)

        phase_group = VGroup(box, text_group)
        if icon:
            icon_mobject = Text(icon, font_size=24).next_to(
                box, LEFT, buff=0.3)
            phase_group.add(icon_mobject)

        return phase_group

    def create_assessment_phase(self, position):
        """Create the initial 'Identify Failed Tests' phase."""
        return self.create_phase_box(
            position, width=7, height=1.0,
            text_content="1. Initial Assessment: Identify Failed Tests",
            sub_text_content="Triage the impact: count, services affected, "
                            "and severity.",
            box_color=RED_E,
            icon="!"  # Simple exclamation mark instead of emoji
        )

    def create_analysis_phase(self, position):
        """Create the 'Root Cause Analysis' phase with a decision diamond."""
        diamond = Polygon(
            [0, 0.5, 0], [1, 0, 0], [0, -0.5, 0], [-1, 0, 0],
            fill_color=BLUE_E, fill_opacity=0.9,
            stroke_color=BLUE, stroke_width=2
        ).move_to(position)

        diamond_text = Text("2. Root Cause Analysis", font_size=16,
                           color=WHITE, weight="BOLD").move_to(diamond)

        # Decision branches
        left_box = self.create_sub_box([-3, position[1], 0],
                                      "Code Logic Issue?")
        right_box = self.create_sub_box([3, position[1], 0],
                                       "Test Environment/Infra Issue?")

        # Connecting arrows from diamond
        left_arrow = Arrow(diamond.get_left(), left_box.get_right(),
                          buff=0.1, stroke_width=3, color=GRAY)
        right_arrow = Arrow(diamond.get_right(), right_box.get_left(),
                           buff=0.1, stroke_width=3, color=GRAY)

        return VGroup(diamond, diamond_text, left_box, right_box,
                     left_arrow, right_arrow)

    def create_sub_box(self, position, text):
        """Helper for creating smaller boxes within a phase."""
        box = RoundedRectangle(width=3.5, height=0.8, corner_radius=0.1,
                              fill_color=BLUE_E, fill_opacity=0.5,
                              stroke_color=BLUE)
        box_text = Text(text, font_size=14, color=WHITE).move_to(box)
        return VGroup(box, box_text).move_to(position)

    def create_diagnosis_phase(self, position):
        """Create the 'Diagnosis' phase showing common findings."""
        title = Text("3. Diagnosis: Pinpoint the Cause", font_size=20,
                    weight="BOLD", color=WHITE).move_to(position + UP * 0.8)

        code_box = self.create_diagnosis_box(
            [-2.5, position[1] - 0.2, 0], "Code Issues (Less Common)",
            "C", GRAY_D)  # Simple "C" for Code
        infra_box = self.create_diagnosis_box(
            [2.5, position[1] - 0.2, 0], "Infra Issues (More Common)",
            "I", YELLOW_E, text_color=BLACK)  # Simple "I" for Infrastructure

        return VGroup(title, code_box, infra_box)

    def create_diagnosis_box(self, position, text, icon, color,
                           text_color=WHITE):
        """Helper for diagnosis result boxes."""
        box = RoundedRectangle(width=4, height=1, corner_radius=0.15,
                              fill_color=color, fill_opacity=0.9)
        icon_mobj = Text(icon, font_size=24).move_to(
            box.get_center() + UP * 0.2)
        text_mobj = Text(text, font_size=14, color=text_color).move_to(
            box.get_center() + DOWN * 0.25)
        return VGroup(box, icon_mobj, text_mobj).move_to(position)

    def create_fixes_phase(self, position):
        """Create the 'Apply Fixes' phase."""
        return self.create_phase_box(
            position, width=8, height=1.2,
            text_content="4. Fix Implementation",
            sub_text_content="Apply targeted fixes: Mock dependencies, "
                            "handle async, align assertions.",
            box_color=GREEN_E,
            icon="F"  # Simple "F" for Fix instead of emoji
        )

    def create_validation_deployment_phase(self, position):
        """Create the final 'Validation and Deployment' phase."""
        return self.create_phase_box(
            position, width=8, height=1.2,
            text_content="5. Validate and Deploy",
            sub_text_content="Verify fixes with isolated tests and confirm "
                            "critical paths are green.",
            box_color=GREEN_E,
            icon="D"  # Simple "D" for Deploy instead of emoji
        )

    def create_connection_arrow(self, start_mobject, end_mobject):
        """Create a clean, vertical arrow between two points."""
        return Arrow(
            start_mobject, end_mobject,
            buff=0.2, stroke_width=4, color=GRAY,
            max_tip_length_to_length_ratio=0.1
        )

    def final_overview(self):
        """Zoom out and provide a final summary and celebration."""
        self.wait(0.5)

        # Group all elements to scale them down for the overview
        full_workflow = VGroup(self.all_phases, self.connections)

        # Success metrics text
        metrics_text = VGroup(
            Text("Key Outcomes:", font_size=24, color=YELLOW, weight="BOLD"),
            Text("• 77% Reduction in Test Failures", font_size=18,
                color=WHITE),
            Text("• 100% of Test Suite Now Passing", font_size=18,
                color=WHITE),
            Text("• Core Business Logic Fully Validated", font_size=18,
                color=WHITE)
        ).arrange(DOWN, buff=0.25, aligned_edge=LEFT)
        metrics_text.to_corner(RIGHT, buff=0.7)

        # Animate the transition to the final overview layout
        self.play(
            full_workflow.animate.scale(0.7).to_corner(LEFT, buff=0.7),
            Write(metrics_text),
            run_time=2
        )

        # Highlight the most common path: Infra -> Fix -> Deploy
        infra_box = self.all_phases[2][2]  # The infra diagnosis box
        fix_box = self.all_phases[3]
        deploy_box = self.all_phases[4]

        highlight_path = VGroup(infra_box, fix_box, deploy_box)

        self.play(
            Indicate(highlight_path, color=GREEN, scale_factor=1.1,
                    repetitions=2),
            run_time=3
        )

        # Final celebration banner
        success_banner = Text("Workflow Successfully Implemented!",
                             font_size=28, color=GREEN, weight="BOLD")
        success_banner.to_edge(DOWN, buff=0.4)

        self.play(
            FadeIn(success_banner, shift=UP),
            Flash(success_banner, color=GREEN, flash_radius=2, num_lines=12),
            run_time=2
        )
        self.wait(3)


class TestDebuggingWorkflowDetailed(Scene):
    """
    A detailed, animated progress bar showing the reduction of test failures
    over time. This version fixes layout issues and uses smoother transformations
    for a better visual experience.
    """

    def construct(self):
        """Construct the detailed progress tracking animation."""
        self.camera.background_color = "#1E1E1E"

        # --- Title ---
        title = Text("Tracking Test Failure Reduction", font_size=32,
                    color=WHITE, weight="BOLD")
        title.to_edge(UP, buff=0.5)
        self.play(Write(title))
        self.wait(0.5)

        # --- Progress Tracking Animation ---
        self.show_progress_tracking()

    def show_progress_tracking(self):
        """Animate a progress bar and metrics to show debugging progress."""

        # --- Enhanced Progress Bar Setup ---
        bar_width = 10
        bar_height = 0.8  # Made taller for better visibility
        
        # Create precisely styled background progress bar
        progress_bg = RoundedRectangle(
            width=bar_width, 
            height=bar_height, 
            corner_radius=0.15,
            fill_color="#1A1A1A", 
            fill_opacity=0.95,  # Slightly more opaque for better contrast
            stroke_color=WHITE, 
            stroke_width=2, 
            stroke_opacity=0.8  # More visible border
        )
        
        # Position progress bar with proper spacing from main title
        progress_bg.shift(UP * 0.5)
        
        # Create shadow positioned relative to progress bar
        progress_shadow = RoundedRectangle(
            width=bar_width + 0.1, height=bar_height + 0.1, corner_radius=0.15,
            fill_color=BLACK, fill_opacity=0.3, stroke_width=0
        ).move_to(progress_bg.get_center() + RIGHT * 0.05 + DOWN * 0.05)
        
        self.play(
            FadeIn(progress_shadow),
            FadeIn(progress_bg),
            run_time=1.0
        )

        # --- Stages Data ---
        stages = [
            {"name": "Initial Crisis", "fails": 39, "total": 156,
             "color": RED},
            {"name": "After Analysis", "fails": 32, "total": 156,
             "color": ORANGE},
            {"name": "Fixes Applied", "fails": 18, "total": 156,
             "color": YELLOW},
            {"name": "Components Fixed", "fails": 12, "total": 156,
             "color": GREEN_E},
            {"name": "Integration Done", "fails": 9, "total": 156,
             "color": GREEN},
            {"name": "Production Ready", "fails": 0, "total": 156,
             "color": BLUE}
        ]

        # --- Animation Loop ---
        current_progress_bar = None
        current_metrics_display = None

        for i, stage in enumerate(stages):
            # Calculate metrics with precision
            passing = stage["total"] - stage["fails"]
            success_rate = passing / stage["total"]
            
            # Create a container that exactly fits inside the background rectangle
            # This ensures the progress bar will never exceed the visual boundaries
            inner_container_width = bar_width - 0.08  # Leave small margin from stroke
            inner_container_height = bar_height - 0.08  # Leave small margin from stroke
            progress_width = success_rate * inner_container_width

            # Create progress bar that fits precisely within visual boundaries
            if success_rate > 0.01:  # Only create bar if there's meaningful progress
                new_bar = RoundedRectangle(
                    width=max(progress_width, 0.1),  # Ensure minimum visible width
                    height=inner_container_height,  # Contained height
                    corner_radius=0.1,  # Slightly smaller radius to fit inside
                    fill_color=stage["color"], 
                    fill_opacity=0.9,
                    stroke_width=0  # No stroke to avoid any boundary issues
                )
                
                # Position progress bar with precise containment
                # Center vertically with background, align left edge with small offset
                new_bar.move_to(progress_bg.get_center())
                new_bar.align_to(progress_bg.get_left(), LEFT)
                new_bar.shift(RIGHT * 0.04)  # Small inward shift to clear the stroke
                
            else:
                # For very small progress, create minimal visible indicator
                new_bar = RoundedRectangle(
                    width=0.06,  # Very small indicator
                    height=inner_container_height, 
                    corner_radius=0.1,
                    fill_color=stage["color"], 
                    fill_opacity=0.9, 
                    stroke_width=0
                )
                new_bar.move_to(progress_bg.get_center())
                new_bar.align_to(progress_bg.get_left(), LEFT)
                new_bar.shift(RIGHT * 0.04)

            # Create precisely positioned percentage label
            label_color = BLACK if stage["name"] == "Fixes Applied" else WHITE
            percent_label = Text(f"{success_rate:.0%}", font_size=18,
                               color=label_color, weight="BOLD")
            
            # Optimized label positioning for contained progress bars
            if progress_width >= 1.8:  # Enough space inside progress bar
                percent_label.move_to(new_bar.get_center())
            elif progress_width >= 0.9:  # Partial space, position at right edge of bar
                percent_label.next_to(new_bar.get_right(), RIGHT, buff=0.08)
                percent_label.align_to(new_bar, UP + DOWN)
                percent_label.set_color(WHITE)
            else:  # Small bar, position outside the background container
                percent_label.next_to(progress_bg.get_right(), RIGHT, buff=0.2)
                percent_label.align_to(progress_bg, UP + DOWN)
                percent_label.set_color(WHITE)

            new_bar_group = VGroup(new_bar, percent_label)

            # Create dashboard with stage title positioned correctly
            new_metrics = self.create_metrics_dashboard(
                stage, success_rate, passing, stage["fails"], progress_bg
            )

            # Animate the transition
            if i == 0:
                # First stage - simple fade in
                self.play(
                    FadeIn(new_bar_group, shift=RIGHT * 0.5),
                    Write(new_metrics),
                    run_time=1.5
                )
                current_progress_bar = new_bar_group
                current_metrics_display = new_metrics
            else:
                # Subsequent stages - fade out old, fade in new
                animations = []

                # Fade out the old elements with more visible transitions
                if current_progress_bar:
                    animations.append(FadeOut(current_progress_bar,
                                            shift=LEFT * 0.5, scale=0.8))
                if current_metrics_display:
                    animations.append(FadeOut(current_metrics_display,
                                            shift=DOWN * 0.5, scale=0.8))

                # Fade in the new elements with more visible transitions
                animations.extend([
                    FadeIn(new_bar_group, shift=RIGHT * 0.5, scale=1.2),
                    FadeIn(new_metrics, shift=UP * 0.5, scale=1.2)
                ])

                self.play(*animations, run_time=2.5)

                # Update current references
                current_progress_bar = new_bar_group
                current_metrics_display = new_metrics

            # Add emphasis on key milestones
            if stage["name"] in ["Fixes Applied", "Production Ready"]:
                self.play(Indicate(current_progress_bar, color=WHITE,
                                 scale_factor=1.1), run_time=1)

            self.wait(1.0)

        # --- Enhanced Final Celebration ---
        # Create a celebration banner with background
        banner_bg = RoundedRectangle(
            width=12, height=1.5, corner_radius=0.2,
            fill_color=GREEN, fill_opacity=0.2,
            stroke_color=GREEN, stroke_width=3, stroke_opacity=0.8
        )
        
        celebration_text = Text("*** Mission Accomplished: Test Suite Optimized! ***",
                               font_size=24, color=GREEN, weight="BOLD")
        celebration_text.move_to(banner_bg.get_center())
        
        celebration_banner = VGroup(banner_bg, celebration_text)
        celebration_banner.next_to(current_metrics_display, DOWN, buff=1.2)

        # Add some final statistics with better visibility
        final_stats = VGroup(
            Text("✓ 100% Reduction in Failures", font_size=16, color=GREEN, weight="BOLD"),
            Text("✓ 100% Success Rate Achieved", font_size=16, color=GREEN, weight="BOLD"),
            Text("✓ Production Ready", font_size=16, color=GREEN, weight="BOLD")
        ).arrange(RIGHT, buff=1.2)
        final_stats.next_to(celebration_banner, DOWN, buff=0.5)

        self.play(
            FadeIn(celebration_banner, scale=0.8),
            run_time=1.5
        )
        self.play(
            Write(final_stats),
            Flash(celebration_text, color=GREEN, flash_radius=3, num_lines=16),
            run_time=2.5
        )
        self.wait(3)

    def create_metrics_dashboard(self, stage, success_rate, passing, failed, progress_bg):
        """Create a complete dashboard with dynamic, responsive layout."""
        
        # Stage title with dynamic sizing based on content
        stage_header = Text(stage["name"], font_size=20,
                           color=WHITE, weight="BOLD")
        stage_underline = RoundedRectangle(
            width=stage_header.width + 0.5, height=0.05,
            fill_color=stage["color"], fill_opacity=1.0, stroke_width=0
        ).next_to(stage_header, DOWN, buff=0.1)
        stage_title = VGroup(stage_header, stage_underline)
        
        # Position stage title dynamically above the progress bar
        stage_title.next_to(progress_bg, UP, buff=0.5)
        
        # Create metric cards with consistent design
        failed_color = "#FF4444" if failed > 20 else "#FF8800" if failed > 10 else "#44FF44"
        
        failed_card = self.create_metric_card(
            title="Failed Tests",
            value=str(failed),
            icon="✗" if failed > 20 else "!" if failed > 10 else "✓",
            color=failed_color
        )
        
        passing_card = self.create_metric_card(
            title="Passing Tests",
            value=str(passing),
            icon="✓",
            color="#44FF44"
        )
        
        success_card = self.create_metric_card(
            title="Success Rate",
            value=f"{success_rate:.1%}",
            icon="%",
            color="#4488FF"
        )
        
        # Use dynamic arrangement with proper spacing
        cards_group = VGroup(failed_card, passing_card, success_card).arrange(
            RIGHT, 
            buff=1.2,  # Responsive spacing
            aligned_edge=DOWN  # Align all cards to bottom edge
        )
        
        # Position cards dynamically below progress bar
        cards_group.next_to(progress_bg, DOWN, buff=1.0)
        
        # Return stage title and cards as a cohesive group
        # Note: We don't use arrange() here to preserve the relative positioning
        dashboard = VGroup(stage_title, cards_group)
        
        return dashboard
    
    def create_metric_card(self, title, value, icon, color):
        """Create an individual metric card with enhanced visibility."""
        
        # Card background with shadow
        card_shadow = RoundedRectangle(
            width=2.8, height=1.8, corner_radius=0.15,
            fill_color=BLACK, fill_opacity=0.4, stroke_width=0
        ).shift(RIGHT * 0.05 + DOWN * 0.05)
        
        # Use lighter background for better contrast
        card_bg = RoundedRectangle(
            width=2.8, height=1.8, corner_radius=0.15,
            fill_color="#2A2A2A", fill_opacity=0.95,  # Lighter gray for better contrast
            stroke_color=color, stroke_width=3, stroke_opacity=1.0  # Thicker, more visible border
        )
        
        # Card content with improved visibility
        icon_text = Text(icon, font_size=26, color=WHITE).move_to(card_bg.get_center() + UP * 0.4)
        
        # Make value text brighter and more prominent
        value_text = Text(value, font_size=24, color=color, weight="BOLD").move_to(
            card_bg.get_center() + UP * 0.05)
        
        # Make title text much more visible with larger font and brighter color
        title_text = Text(title, font_size=14, color=WHITE, weight="NORMAL").move_to(
            card_bg.get_center() + DOWN * 0.4)
        
        card = VGroup(card_shadow, card_bg, icon_text, value_text, title_text)
        
        return card
