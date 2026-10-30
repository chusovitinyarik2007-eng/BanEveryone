"""Regression tests for Telegram's split long messages (no aiogram required)."""
import ast
import pathlib
import unittest

from auto_rule_input import (add_part, assembled_text, draft_statistics, parse_rules,
                             RuleInputError, MAX_RULES)

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TelegramRuleInputTests(unittest.TestCase):
    def test_many_messages_are_all_saved_in_chat_order(self):
        rules = [f'domain:site-{i}.example.org' for i in range(850)]
        # Telegram text length cap: break the large paste into separate chunks.
        source = '\n'.join(rules)
        chunks = [source[i:i + 4000] for i in range(0, len(source), 4000)]
        pieces = []
        # Simulate out-of-order worker completion and ensure message_id sorting.
        for idx in reversed(range(len(chunks))):
            pieces = add_part(pieces, 1000 + idx, chunks[idx])
        self.assertEqual(parse_rules(pieces), rules)
        self.assertGreater(len(pieces), 3)

    def test_separate_whole_rule_messages_get_newlines(self):
        draft = add_part([], 10, 'domain:one.com\nfull:hello.one')
        draft = add_part(draft, 11, 'geosite:ru\nregexp:(^|\\.)ru$')
        self.assertEqual(parse_rules(draft), [
            'domain:one.com', 'full:hello.one', 'geosite:ru', 'regexp:(^|\\.)ru$'])

    def test_partial_prefix_and_partial_domain_rejoined(self):
        parts = add_part([], 100, 'domain:one.com\ndom')
        parts = add_part(parts, 101, 'ain:two.e')
        parts = add_part(parts, 102, 'xample.org\nfull:complete.example')
        self.assertEqual(parse_rules(parts), [
            'domain:one.com', 'domain:two.example.org', 'full:complete.example'])

    def test_duplicate_delivery_same_message_id_is_idempotent(self):
        first = add_part([], 20, 'domain:example.org')
        second = add_part(first, 20, 'domain:example.org')
        self.assertEqual(first, second)
        self.assertEqual(parse_rules(second), ['domain:example.org'])

    def test_clear_requires_explicit_dash_and_final_confirmation(self):
        draft = add_part([], 30, '-')
        self.assertEqual(parse_rules(draft), [])
        draft = add_part(draft, 31, 'domain:sample.org')
        with self.assertRaises(RuleInputError):
            parse_rules(draft)

    def test_invalid_one_part_rejects_entire_draft(self):
        draft = add_part([], 40, 'domain:ok.com')
        draft = add_part(draft, 41, 'not-valid-rule')
        draft = add_part(draft, 42, 'geosite:ru')
        with self.assertRaisesRegex(RuleInputError, '2'):
            parse_rules(draft)

    def test_no_content_rejected_and_progress_count(self):
        with self.assertRaises(RuleInputError):
            parse_rules([])
        parts = add_part([], 50, 'domain:a.org\n')
        parts = add_part(parts, 51, 'domain:b.org')
        self.assertEqual(draft_statistics(parts), (2, 2))
        self.assertEqual(assembled_text(parts), 'domain:a.org\ndomain:b.org')

    def test_large_rule_list_is_not_silently_limited_to_300(self):
        draft = add_part([], 60, '\n'.join(f'full:site{i}.org' for i in range(700)))
        self.assertEqual(len(parse_rules(draft)), 700)
        overflow = add_part([], 61, '\n'.join('domain:x.org' for _ in range(MAX_RULES + 1)))
        with self.assertRaises(RuleInputError):
            parse_rules(overflow)

    def test_oversize_rules_rejected_not_truncated(self):
        parts = add_part([], 70, 'domain:' + 'a' * 520)
        with self.assertRaises(RuleInputError):
            parse_rules(parts)

    def test_bot_has_explicit_confirm_and_never_saves_per_message(self):
        tree = ast.parse((ROOT / 'tgbot.py').read_text())
        funcs = {func.name:func for func in tree.body if isinstance(func, ast.AsyncFunctionDef)}
        self.assertIn('auto_collect_rules', funcs)
        self.assertIn('auto_finish_rules_command', funcs)
        self.assertIn('_auto_finish_rules', funcs)
        self.assertIn('auto_cancel_rules', funcs)
        self.assertIn('auto_reset_rules', funcs)
        def calls_to(name):
            return [n for n in ast.walk(funcs[name]) if isinstance(n, ast.Call)]
        self.assertFalse(any(isinstance(n.func, ast.Attribute) and n.func.attr == 'set_option'
                             for n in calls_to('auto_collect_rules')))
        self.assertTrue(any(isinstance(n.func, ast.Attribute) and n.func.attr == 'set_option'
                            for n in calls_to('_auto_finish_rules')))
        source = (ROOT / 'tgbot.py').read_text()
        self.assertIn('auto:rulefinish:', source)
        self.assertIn('auto_rule_parts', source)
        self.assertIn('await state.clear()', source)


if __name__ == '__main__':
    unittest.main()
