from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from eval_recall import FIXTURE, evaluate, load_fixture, materialize, summarize


class RetrievalEvaluationTests(unittest.TestCase):
    def test_metrics_use_separate_denominators(self):
        def row(expected, forbidden, hit=False, leak=False, false=False, preferred=None, first=False):
            return dict(expected=expected, forbidden=forbidden, evidence_hit=hit,
                        forbidden_leak=leak, false_recall=false,
                        preferred_first=preferred, preferred_first_hit=first, elapsed_ms=1.0)
        metrics = summarize([
            row(['a'], [], hit=True, preferred='a', first=True),
            row(['b'], ['old'], leak=True),
            row([], ['gone'], false=True),
            row([], []),
        ])
        self.assertEqual(metrics['top3_evidence_hit'], dict(count=1, total=2, rate=0.5))
        self.assertEqual(metrics['top3_forbidden_evidence_leak'], dict(count=1, total=2, rate=0.5))
        self.assertEqual(metrics['no_answer_false_recall'], dict(count=1, total=2, rate=0.5))
        self.assertEqual(metrics['preferred_first_hit'], dict(count=1, total=1, rate=1.0))
        self.assertIsNone(summarize([])['top3_evidence_hit']['rate'])

    def test_fixture_raw_hashes_match_writer_format(self):
        fixture = load_fixture(FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            materialize(fixture, vault)
            for doc in fixture['documents']:
                if 'raw' not in doc:
                    continue
                text = (vault / doc['path']).read_text(encoding='utf-8')
                line = next(line for line in text.splitlines()
                            if '<!-- dsh-entry:' in line and f'"id": "{doc["id"]}"' in line)
                metadata = json.loads(line.removeprefix('<!-- dsh-entry: ').removesuffix(' -->'))
                self.assertEqual(metadata['body_sha256'], hashlib.sha256(doc['body'].strip().encode()).hexdigest())
                self.assertEqual(metadata['type'], doc['raw']['type'])

    def test_results_repeat_except_informational_timing(self):
        fixture = load_fixture(FIXTURE)
        first, second = evaluate(fixture), evaluate(fixture)
        for report in (first, second):
            report['metrics'].pop('latency_ms_informational')
            for row in report['cases']:
                row.pop('elapsed_ms')
        self.assertEqual(first, second)
        self.assertTrue(first['synthetic'])
        self.assertEqual(len(first['cases']), 10)
        # This verifies the harness contract, not a perfect retrieval score.
        self.assertTrue(all(len(row['retrieved']) <= 3 for row in first['cases']))
        self.assertFalse(any(item.startswith('unmapped:')
                             for row in first['cases'] for item in row['retrieved']))

    def test_bad_fixture_is_rejected_before_materialization(self):
        valid = load_fixture(FIXTURE)
        mutations = [
            lambda f: f.update(synthetic=False),
            lambda f: f['documents'][0].update(path='../outside.md'),
            lambda f: f['cases'][0].update(expected=['unknown']),
            lambda f: f['cases'][0].update(forbidden=f['cases'][0]['expected']),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fixture.json'
            for mutate in mutations:
                fixture = copy.deepcopy(valid)
                mutate(fixture)
                path.write_text(json.dumps(fixture), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_fixture(path)


if __name__ == '__main__':
    unittest.main()
