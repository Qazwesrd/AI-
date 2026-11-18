import unittest

from app.routers.story import AIPlanInput, PROMPT_STYLE_GUIDANCE, prompt_style_guidance


class StoryPromptStyleTests(unittest.TestCase):
    def test_all_prompt_styles_have_guidance(self):
        self.assertEqual(
            set(PROMPT_STYLE_GUIDANCE),
            {"professional", "cinematic", "concise"},
        )
        for style, guidance in PROMPT_STYLE_GUIDANCE.items():
            with self.subTest(style=style):
                self.assertTrue(guidance.strip())
                self.assertEqual(prompt_style_guidance(style), guidance)

    def test_unknown_style_falls_back_to_professional(self):
        self.assertEqual(
            prompt_style_guidance("unknown"),
            PROMPT_STYLE_GUIDANCE["professional"],
        )

    def test_old_clients_keep_compatible_defaults(self):
        payload = AIPlanInput(confirm_paid=True)
        self.assertEqual(payload.prompt_style, "professional")
        self.assertEqual(payload.additional_instructions, "")

    def test_additional_instructions_are_bounded(self):
        with self.assertRaises(Exception):
            AIPlanInput(additional_instructions="x" * 4001)


if __name__ == "__main__":
    unittest.main()
