from probe_support import ProbeCase

EN = ("What changed", "What checks found", "What you still need to decide")
ZH = ("这次改了什么", "检查发现了什么", "还需要你判断什么")


class HandoffHelpers:
    def overview_of(self, content):
        self.assertIn("<details>", content,
                      "technical evidence must stay folded behind <details>")
        return content.split("<details>", 1)[0]

    def assert_three_sections_in_reading_order(self, text, sections):
        pos = []
        for section in sections:
            self.assertIn(section, text, "missing plain section: %r" % section)
            pos.append(text.index(section))
        self.assertLess(pos[0], pos[1], "wrong reading order for the three sections")
        self.assertLess(pos[1], pos[2], "wrong reading order for the three sections")

    def section_body(self, text, first, second):
        return text[text.index(first) + len(first):text.index(second)].strip()


class EnglishHandoffReport(HandoffHelpers, ProbeCase):
    def test_summary_precedes_folded_evidence_in_reading_order(self):
        code, out = self.cli("init", "--goal", "Stop retrying after a timeout", "--lang", "en")
        self.assertEqual(code, 0, out)
        self.write("retry.py", "changed behavior")
        self.write("notes.md", "updated notes")
        code, out = self.cli("report")
        self.assertEqual(code, 0, out)
        content = self.read(".agentproof/report.md")
        overview = self.overview_of(content)
        self.assert_three_sections_in_reading_order(overview, EN)
        self.assertLess(overview.index(EN[2]), content.index("<details>"),
                        "the decide section must be readable before the fold")
        self.assertIn("## Verification", content.split("<details>", 1)[1])

    def test_change_section_names_request_and_changed_files(self):
        goal = "Stop retrying after a timeout"
        code, out = self.cli("init", "--goal", goal, "--lang", "en")
        self.assertEqual(code, 0, out)
        self.write("retry.py", "changed behavior")
        self.write("notes.md", "updated notes")
        code, out = self.cli("report")
        self.assertEqual(code, 0, out)
        overview = self.overview_of(self.read(".agentproof/report.md"))
        body = self.section_body(overview, EN[0], EN[1])
        self.assertIn(goal, body)
        self.assertIn("retry.py", body)
        self.assertIn("notes.md", body)

    def test_each_section_carries_content_beyond_its_heading(self):
        code, out = self.cli("init", "--goal", "Stop retrying after a timeout", "--lang", "en")
        self.assertEqual(code, 0, out)
        self.write("retry.py", "changed behavior")
        code, out = self.cli("report")
        self.assertEqual(code, 0, out)
        overview = self.overview_of(self.read(".agentproof/report.md"))
        self.assertTrue(self.section_body(overview, EN[0], EN[1]),
                        "what-changed section must say something")
        self.assertTrue(self.section_body(overview, EN[1], EN[2]),
                        "checks section must state the check situation, even 'none recorded'")
        tail = overview[overview.index(EN[2]) + len(EN[2]):].strip()
        self.assertTrue(tail, "the decide section must tell the reader something")


class ChineseHandoffReport(HandoffHelpers, ProbeCase):
    def test_zh_cn_reader_gets_same_three_parts_before_the_fold(self):
        code, out = self.cli("init", "--goal", "修复重试超时", "--lang", "zh-CN")
        self.assertEqual(code, 0, out)
        self.write("retry.py", "changed behavior")
        code, out = self.cli("report")
        self.assertEqual(code, 0, out)
        content = self.read(".agentproof/report.md")
        overview = self.overview_of(content)
        self.assert_three_sections_in_reading_order(overview, ZH)
        self.assertLess(overview.index(ZH[2]), content.index("<details>"))
        body = self.section_body(overview, ZH[0], ZH[1])
        self.assertIn("修复重试超时", body)
        self.assertIn("retry.py", body)


class BriefStatusHandoff(HandoffHelpers, ProbeCase):
    def test_brief_status_answers_all_three_questions_without_full_report(self):
        code, out = self.cli("init", "--goal", "Stop retrying after a timeout", "--lang", "en")
        self.assertEqual(code, 0, out)
        self.write("retry.py", "changed behavior")
        code, out = self.cli("status", "--brief")
        self.assertEqual(code, 0, out)
        self.assert_three_sections_in_reading_order(out, EN)
        self.assertIn("retry.py", out)