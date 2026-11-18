import hashlib, json, subprocess, sys, tempfile, unittest
from pathlib import Path
from league_table.cli import run

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'fixtures/input-tiebreak.json'
PROFILE = ROOT / 'fixtures/tie-break-profile.json'
LEGACY_SOURCE = ROOT / 'fixtures/input.json'
PROFILE_DICT = {'head_to_head': ['points', 'goal_difference', 'goals_for'],
                'overall': ['goal_difference', 'goals_for']}


def invoke(source, output, profile=None, observations=None):
    arguments = [sys.executable, '-m', 'league_table.cli',
                 '--source', str(source), '--output', str(output)]
    if profile is not None:
        arguments.extend(['--tie-break-profile', str(profile)])
    if observations is not None:
        arguments.extend(['--observations', str(observations)])
    return subprocess.run(arguments, capture_output=True, text=True)


class TieBreakCLI(unittest.TestCase):
    def write(self, directory, name, payload):
        path = Path(directory) / name
        path.write_text(json.dumps(payload))
        return path

    def test_policy_invocation_schema_v2_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'report.json'
            completed = invoke(SOURCE, out, PROFILE)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(out.read_text())
            self.assertEqual(report['schema_version'], 2)
            self.assertEqual(set(report),
                             {'schema_version', 'source_sha256', 'result', 'tie_break_audit'})
            self.assertEqual(report['source_sha256'],
                             hashlib.sha256(SOURCE.read_bytes()).hexdigest())
            self.assertNotIn('observations_sha256', report)
            self.assertNotIn('reconciliation', report)
            self.assertEqual(report['tie_break_audit']['profile'], PROFILE_DICT)
            self.assertTrue(report['tie_break_audit']['nodes'])
            self.assertEqual([r['team'] for r in report['result']['standings']],
                             ['A', 'B', 'D', 'C', 'E'])
            self.assertEqual(report['result']['pending_fixtures'], ['e1'])
            self.assertEqual(report['result']['completed'], 9)

    def test_policy_audit_matches_library_run(self):
        report = run(SOURCE, tie_break_profile=PROFILE)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'report.json'
            completed = invoke(SOURCE, out, PROFILE)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(out.read_text()), report)

    def test_legacy_invocation_remains_schema_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'report.json'
            completed = invoke(LEGACY_SOURCE, out)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(out.read_text())
            self.assertEqual(report['schema_version'], 1)
            self.assertEqual(set(report), {'schema_version', 'source_sha256', 'result'})
            self.assertNotIn('tie_break_audit', report)
            self.assertEqual(run(LEGACY_SOURCE), report)

    def test_no_tied_groups_keeps_identical_baseline_result(self):
        # A decisive result leaves every team on a distinct points total:
        # audit nodes are empty and result equals the legacy baseline result.
        with tempfile.TemporaryDirectory() as tmp:
            source = self.write(tmp, 'source.json', {
                'teams': ['A', 'B'],
                'fixtures': [{'id': 'm1', 'home': 'A', 'away': 'B'}],
                'results': [{'fixture': 'm1', 'home_goals': 1, 'away_goals': 0}],
                'deductions': []})
            out = Path(tmp) / 'report.json'
            completed = invoke(source, out, PROFILE)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(out.read_text())
            self.assertEqual(report['tie_break_audit']['nodes'], [])
            self.assertEqual(report['result'], run(source)['result'])

    def test_profile_and_observations_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'report.json'
            obs = self.write(tmp, 'obs.json', {'observations': []})
            completed = invoke(SOURCE, out, profile=PROFILE, observations=obs)
            self.assertEqual(completed.returncode, 2)
            self.assertIn('not allowed with', completed.stderr)
            self.assertFalse(out.exists())

    def test_unknown_profile_field_exits_2_and_keeps_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = self.write(tmp, 'profile.json',
                             {'head_to_head': ['play_off'], 'overall': ['goal_difference']})
            out = Path(tmp) / 'report.json'
            out.write_text('old')
            completed = invoke(SOURCE, out, bad)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(out.read_text(), 'old')

    def test_duplicate_profile_field_exits_2_and_keeps_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = self.write(tmp, 'profile.json',
                             {'head_to_head': ['points', 'points'],
                              'overall': ['goal_difference']})
            out = Path(tmp) / 'report.json'
            out.write_text('old')
            completed = invoke(SOURCE, out, bad)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(out.read_text(), 'old')

    def test_empty_profile_criteria_exits_2(self):
        for payload in ({'head_to_head': [], 'overall': ['goal_difference']},
                        {'head_to_head': ['points'], 'overall': []}):
            with tempfile.TemporaryDirectory() as tmp:
                bad = self.write(tmp, 'profile.json', payload)
                out = Path(tmp) / 'report.json'
                completed = invoke(SOURCE, out, bad)
                self.assertEqual(completed.returncode, 2)
                self.assertFalse(out.exists())

    def test_profile_unknown_top_level_field_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = self.write(tmp, 'profile.json',
                             {'head_to_head': ['points'], 'overall': ['goal_difference'],
                              'extra': 1})
            out = Path(tmp) / 'report.json'
            out.write_text('old')
            completed = invoke(SOURCE, out, bad)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(out.read_text(), 'old')

    def test_profile_duplicate_json_key_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'profile.json'
            bad.write_text('{"head_to_head":["points"],"head_to_head":["goals_for"],'
                           '"overall":["goal_difference"]}')
            out = Path(tmp) / 'report.json'
            out.write_text('old')
            completed = invoke(SOURCE, out, bad)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(out.read_text(), 'old')

    def test_missing_profile_exits_2_and_keeps_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'report.json'
            out.write_text('old')
            completed = invoke(SOURCE, out, Path(tmp) / 'absent.json')
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(out.read_text(), 'old')

    def test_output_cannot_equal_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'profile.json'
            out.write_text(json.dumps(PROFILE_DICT))
            raw = out.read_bytes()
            completed = invoke(SOURCE, out, out)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(out.read_bytes(), raw)

    def test_output_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / 'link'
            link.symlink_to(SOURCE)
            completed = invoke(SOURCE, link, PROFILE)
            self.assertEqual(completed.returncode, 2)

    def test_strict_profile_bool_and_type_checks(self):
        bad_payloads = [
            {'head_to_head': ['points'], 'overall': ['goal_difference', 1]},
            {'head_to_head': None, 'overall': ['goal_difference']},
            {'head_to_head': ['points'], 'overall': {}},
        ]
        for payload in bad_payloads:
            with tempfile.TemporaryDirectory() as tmp:
                bad = self.write(tmp, 'profile.json', payload)
                out = Path(tmp) / 'report.json'
                completed = invoke(SOURCE, out, bad)
                self.assertEqual(completed.returncode, 2)
                self.assertFalse(out.exists())

    def test_permutation_stable_report(self):
        # The same profile content delivered via files with different key order
        # and whitespace produces the exact same report.
        with tempfile.TemporaryDirectory() as tmp:
            reordered = Path(tmp) / 'reordered.json'
            reordered.write_text('{\n  "overall": ["goal_difference", "goals_for"],\n'
                                 '  "head_to_head": ["points", "goal_difference", "goals_for"]\n}\n')
            first = Path(tmp) / 'r1.json'
            second = Path(tmp) / 'r2.json'
            self.assertEqual(invoke(SOURCE, first, PROFILE).returncode, 0)
            completed = invoke(SOURCE, second, reordered)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(first.read_text()), json.loads(second.read_text()))


if __name__ == '__main__':
    unittest.main()
