import unittest

from app.routers.ai_collaboration import classify_ai_text_result


class AITextResultClassificationTests(unittest.TestCase):
    def test_classifies_proposal_payload(self):
        self.assertEqual(classify_ai_text_result('{"proposals": []}'), "剧情变化提议")

    def test_classifies_storyboard_payload(self):
        self.assertEqual(classify_ai_text_result('{"shots": [{"shot_number": 1}]}'), "分镜设计")

    def test_classifies_screenplay_payload(self):
        self.assertEqual(classify_ai_text_result('{"screenplay": "场景一"}'), "剧本")

    def test_classifies_character_payload(self):
        self.assertEqual(classify_ai_text_result('{"characters": [{"name": "主角"}]}'), "角色设定")

    def test_classifies_scene_payload(self):
        self.assertEqual(classify_ai_text_result('{"scene_design": []}'), "场景设定")

    def test_classifies_dialogue_payload(self):
        self.assertEqual(classify_ai_text_result('{"dialogues": []}'), "对白")

    def test_classifies_plain_text(self):
        self.assertEqual(classify_ai_text_result("# 分镜设计\n镜头号：1，景别：远景"), "分镜设计")

    def test_falls_back_to_general_analysis(self):
        self.assertEqual(classify_ai_text_result("没有可识别的结构"), "综合分析")


if __name__ == "__main__":
    unittest.main()
