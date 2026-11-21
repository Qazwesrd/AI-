import unittest

from app.routers.media import ass_color_from_hex, build_subtitle_force_style


class SubtitleStyleTests(unittest.TestCase):
    def test_hex_color_converts_to_ass_bgr_order(self):
        self.assertEqual(ass_color_from_hex("#FF8040"), "&H004080FF")

    def test_force_style_includes_font_size_outline_position_and_background(self):
        style = build_subtitle_force_style({
            "subtitle_font_name": "Microsoft YaHei",
            "subtitle_font_size": 42,
            "subtitle_primary_color": "#FFFFFF",
            "subtitle_outline_color": "#000000",
            "subtitle_outline_width": 2,
            "subtitle_background": True,
            "subtitle_alignment": 8,
            "subtitle_margin_v": 90,
        })
        self.assertIn("FontSize=42", style)
        self.assertIn("BorderStyle=3", style)
        self.assertIn("Alignment=8", style)
        self.assertIn("MarginV=90", style)

    def test_rejects_unsafe_font_names_and_invalid_colors(self):
        with self.assertRaises(ValueError):
            build_subtitle_force_style({"subtitle_font_name": "font';movie=evil"})
        with self.assertRaises(ValueError):
            ass_color_from_hex("red")


if __name__ == "__main__":
    unittest.main()
