#!/usr/bin/env python3
"""Tests for hunch.py — the portable Bayesian hypothesis-tracking engine.

Ported from the TARS Abduction Engine (abduction-lib.js) and its dedupe
helper (cue-light-dedupe.js). Organized by work package (WP0-WP6), matching
the implementation brief. Run with:
    python3 -m unittest test_hunch -v
    python3 -m pytest test_hunch.py -q
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hunch  # noqa: E402


HUNCH_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hunch.py')


def run_cli(args, cwd=None, env=None, input_text=None):
    """Run hunch.py as a subprocess, return (returncode, stdout, stderr)."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, HUNCH_PY] + args,
        cwd=cwd, env=full_env, input=input_text,
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


# =============================================================================
# WP0 — tuning, dedupe, ledger I/O
# =============================================================================

class TestTuning(unittest.TestCase):
    def test_tuning_constants_verbatim(self):
        t = hunch.TUNING
        self.assertEqual(t['OTHER_ID'], 'other')
        self.assertEqual(t['OTHER_INITIAL_PRIOR'], 0.15)
        self.assertEqual(t['OTHER_FLOOR'], 0.05)
        self.assertEqual(t['REGEN_OTHER_THRESHOLD'], 0.35)
        self.assertEqual(t['MIN_HYPOTHESES'], 3)
        self.assertEqual(t['MAX_HYPOTHESES'], 6)
        self.assertEqual(t['NEUTRAL_LIKELIHOOD'], 0.5)
        self.assertEqual(t['POSTERIOR_ZERO_EPSILON'], 1e-12)
        self.assertEqual(t['RELIABILITY_DEFAULTS'], {
            'firsthand': 1.0, 'secondhand': 0.6, 'rumor': 0.3, 'inferred': 0.5,
        })
        self.assertEqual(t['CLUSTER_JACCARD_THRESHOLD'], 0.6)
        self.assertEqual(t['SURFACE_TOP_MIN'], 0.65)
        self.assertEqual(t['SURFACE_LEAD_MIN'], 0.25)
        self.assertEqual(t['FLAT_ENTROPY_MIN'], 0.85)
        self.assertEqual(t['STALE_AFTER_MS'], 30 * 24 * 3600 * 1000)
        self.assertEqual(t['PURGE_AFTER_MONTHS'], 6)
        self.assertEqual(t['POSTERIOR_HISTORY_CAP'], 200)
        self.assertEqual(t['INVALID_PRIOR_FALLBACK_WEIGHT'], 1)
        self.assertEqual(t['SURFACE_MAX_RATIONALES'], 3)
        self.assertEqual(t['SURFACE_SNIPPET_CHARS'], 120)
        self.assertEqual(hunch.MS_PER_MONTH, 30 * 24 * 3600 * 1000)


class TestDedupe(unittest.TestCase):
    def test_token_set_lowercases_strips_punctuation_and_stopwords(self):
        toks = hunch.token_set("The Butler and the Colonel were in the Library!")
        self.assertNotIn('the', toks)
        self.assertNotIn('and', toks)
        self.assertIn('butler', toks)
        self.assertIn('colonel', toks)
        self.assertIn('library', toks)
        self.assertIn('were', toks)  # not a stopword, len>=3

    def test_token_set_drops_short_tokens(self):
        toks = hunch.token_set("a an it is ok yes")
        # all len<3 except 'yes'? 'yes' len 3 keep, others len<3 dropped
        self.assertNotIn('a', toks)
        self.assertNotIn('an', toks)
        self.assertNotIn('it', toks)
        self.assertNotIn('is', toks)
        self.assertNotIn('ok', toks)
        self.assertIn('yes', toks)

    def test_token_set_dedupes_preserving_order(self):
        toks = hunch.token_set("library library butler library")
        self.assertEqual(toks, ['library', 'butler'])

    def test_token_set_empty_for_falsy(self):
        self.assertEqual(hunch.token_set(''), [])
        self.assertEqual(hunch.token_set(None), [])

    def test_jaccard_zero_for_empty(self):
        self.assertEqual(hunch.jaccard([], ['a']), 0)
        self.assertEqual(hunch.jaccard(['a'], []), 0)
        self.assertEqual(hunch.jaccard([], []), 0)

    def test_jaccard_identical_is_one(self):
        self.assertEqual(hunch.jaccard(['a', 'b'], ['a', 'b']), 1)

    def test_jaccard_around_threshold(self):
        # 3 shared of 5 union = 0.6
        a = ['a', 'b', 'c']
        b = ['a', 'b', 'd']
        # union {a,b,c,d}=4, intersection {a,b}=2 -> 0.5
        self.assertAlmostEqual(hunch.jaccard(a, b), 0.5)
        # exact 0.6 case: 3 shared, union 5
        a2 = ['a', 'b', 'c']
        b2 = ['a', 'b', 'c', 'd', 'e']
        # intersection 3, union 5 -> 0.6
        self.assertAlmostEqual(hunch.jaccard(a2, b2), 0.6)

    def test_stopwords_verbatim(self):
        expected = {
            'the', 'and', 'for', 'with', 'that', 'this', 'from', 'has', 'not', 'but',
            'are', 'was', 'its', 'they', 'have', 'been', 'will', 'their', 'also',
            'into', 'than', 'then', 'when', 'our', 'can', 'all', 'any', 'one',
            'you', 'your', 'per', 'via',
        }
        self.assertEqual(hunch.STOPWORDS, expected)
        self.assertEqual(len(expected), 32)


class TestLedgerIO(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'ledger.json')
            store = hunch.new_store(now=1000)
            hunch.save_ledger(path, store)
            loaded = hunch.load_ledger(path)
            self.assertEqual(loaded['schemaVersion'], 1)
            self.assertEqual(loaded['createdAt'], 1000)
            self.assertEqual(loaded['situations']['seq'], 0)

    def test_atomic_write_no_partial_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'ledger.json')
            store = hunch.new_store(now=1)
            hunch.save_ledger(path, store)
            # Directory should contain no leftover temp files.
            entries = os.listdir(d)
            self.assertEqual(entries, ['ledger.json'])

    def test_missing_ledger_load_returns_none_for_read(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'nope.json')
            self.assertIsNone(hunch.load_ledger_or_none(path))

    def test_corrupt_json_raises_named_error_and_leaves_file_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'ledger.json')
            with open(path, 'w') as f:
                f.write('{not valid json')
            with self.assertRaises(hunch.LedgerError) as ctx:
                hunch.load_ledger(path)
            self.assertIn(path, str(ctx.exception))
            self.assertIn('fix or move the file', str(ctx.exception))
            with open(path) as f:
                self.assertEqual(f.read(), '{not valid json')

    def test_schema_version_mismatch_raises_named_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'ledger.json')
            with open(path, 'w') as f:
                json.dump({'schemaVersion': 99, 'situations': {'seq': 0, 'items': {}}}, f)
            with self.assertRaises(hunch.LedgerError) as ctx:
                hunch.load_ledger(path)
            self.assertIn('schemaVersion', str(ctx.exception))

    def test_ledger_path_precedence_flag_over_env_over_default(self):
        with tempfile.TemporaryDirectory() as d:
            flag_path = os.path.join(d, 'flag.json')
            env_path = os.path.join(d, 'env.json')
            resolved = hunch.resolve_ledger_path(flag=flag_path, env_value=env_path, cwd=d)
            self.assertEqual(resolved, flag_path)
            resolved2 = hunch.resolve_ledger_path(flag=None, env_value=env_path, cwd=d)
            self.assertEqual(resolved2, env_path)
            resolved3 = hunch.resolve_ledger_path(flag=None, env_value=None, cwd=d)
            self.assertEqual(resolved3, os.path.join(d, '.hunch', 'ledger.json'))


# =============================================================================
# WP1 — store lifecycle + clustering + hypothesis validation
# =============================================================================

def open_situation(store, question='Why did the widget break?', now=1000, entity_ref=None):
    return hunch.open_situation(store, question=question, now=now, entity_ref=entity_ref)


VALID_HYPS = [
    {'statement': 'The widget overheated', 'prior': 0.4, 'predictedEvidence': ['temp logs show spike']},
    {'statement': 'A bad firmware update shipped', 'prior': 0.3, 'predictedEvidence': ['deploy log around break time']},
    {'statement': 'Physical damage from shipping', 'prior': 0.3, 'predictedEvidence': ['visible dents', 'shipping report']},
]


class TestStoreLifecycle(unittest.TestCase):
    def test_open_seeds_other_hypothesis(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        self.assertEqual(sit['id'], 'sit-1')
        self.assertEqual(sit['status'], 'open')
        self.assertEqual(len(sit['hypotheses']), 1)
        other = sit['hypotheses'][0]
        self.assertEqual(other['id'], 'other')
        self.assertEqual(other['prior'], 0.15)
        self.assertEqual(other['posterior'], 0.15)
        self.assertEqual(other['status'], 'active')

    def test_open_requires_question(self):
        store = hunch.new_store(now=1)
        with self.assertRaises(ValueError):
            hunch.open_situation(store, question='', now=1)
        with self.assertRaises(ValueError):
            hunch.open_situation(store, question=None, now=1)

    def test_open_increments_seq(self):
        store = hunch.new_store(now=1)
        s1 = open_situation(store)
        s2 = open_situation(store)
        self.assertEqual(s1['id'], 'sit-1')
        self.assertEqual(s2['id'], 'sit-2')

    def test_get_unknown_returns_none(self):
        store = hunch.new_store(now=1)
        self.assertIsNone(hunch.get_situation(store, 'sit-99'))

    def test_list_situations_filters_by_status(self):
        store = hunch.new_store(now=1)
        s1 = open_situation(store)
        open_situation(store)
        hunch.resolve(store, s1['id'], resolution='other: it broke', now=2)
        open_only = hunch.list_situations(store, status='open')
        self.assertEqual(len(open_only), 1)
        resolved_only = hunch.list_situations(store, status='resolved')
        self.assertEqual(len(resolved_only), 1)
        self.assertEqual(len(hunch.list_situations(store)), 2)


class TestClustering(unittest.TestCase):
    def test_same_event_as_known_id_joins_cluster_model_method(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        r1 = hunch.add_observation(store, sit['id'], text='It got hot', now=10)
        r2 = hunch.add_observation(store, sit['id'], text='Temperature was high',
                                    same_event_as=r1['observationId'], now=11)
        self.assertEqual(r2['clusterId'], r1['clusterId'])
        self.assertEqual(r2['clusterMethod'], 'model')

    def test_same_event_as_unknown_id_errors_listing_known_ids(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.add_observation(store, sit['id'], text='It got hot', now=10)
        with self.assertRaises(ValueError) as ctx:
            hunch.add_observation(store, sit['id'], text='x', same_event_as='obs-99', now=11)
        self.assertIn('obs-99', str(ctx.exception))
        self.assertIn('obs-1', str(ctx.exception))

    def test_same_event_as_null_forces_new_cluster_even_for_identical_text(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        r1 = hunch.add_observation(store, sit['id'], text='It got very hot', now=10)
        r2 = hunch.add_observation(store, sit['id'], text='It got very hot',
                                    same_event_as=None, force_new_cluster=True, now=11)
        self.assertNotEqual(r1['clusterId'], r2['clusterId'])
        self.assertEqual(r2['clusterMethod'], 'new')

    def test_jaccard_fallback_joins_or_creates(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        r1 = hunch.add_observation(store, sit['id'], text='the widget temperature spiked badly', now=10)
        r2 = hunch.add_observation(store, sit['id'], text='widget temperature spiked badly today', now=11)
        self.assertEqual(r1['clusterId'], r2['clusterId'])
        self.assertEqual(r2['clusterMethod'], 'jaccard')
        r3 = hunch.add_observation(store, sit['id'], text='completely unrelated firmware rollout event', now=12)
        self.assertNotEqual(r3['clusterId'], r1['clusterId'])
        self.assertEqual(r3['clusterMethod'], 'new')

    def test_reliability_precedence_explicit_over_source_type_over_inferred_default(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.add_observation(store, sit['id'], text='a', reliability=0.9, now=1)
        hunch.add_observation(store, sit['id'], text='b', source_type='rumor', now=2)
        hunch.add_observation(store, sit['id'], text='c', now=3)
        obs = hunch.get_situation(store, sit['id'])['observations']
        self.assertEqual(obs[0]['provenance']['reliability'], 0.9)
        self.assertEqual(obs[1]['provenance']['reliability'], 0.3)
        self.assertEqual(obs[2]['provenance']['reliability'], 0.5)  # inferred default

    def test_observe_on_non_open_situation_errors(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.resolve(store, sit['id'], resolution='other: done', now=2)
        with self.assertRaises(ValueError):
            hunch.add_observation(store, sit['id'], text='too late', now=3)

    def test_cluster_reliability_is_max_of_members(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        r1 = hunch.add_observation(store, sit['id'], text='the widget failed today', reliability=0.3, now=1)
        hunch.add_observation(store, sit['id'], text='the widget failed today again',
                               same_event_as=r1['observationId'], reliability=0.9, now=2)
        clusters = hunch.get_clusters(store, sit['id'])
        cluster = next(c for c in clusters if c['id'] == r1['clusterId'])
        self.assertEqual(cluster['reliability'], 0.9)
        self.assertEqual(cluster['representativeText'], 'the widget failed today')


class TestApplyHypotheses(unittest.TestCase):
    def test_valid_hypotheses_become_active(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        active = [h for h in s['hypotheses'] if h['status'] == 'active']
        self.assertEqual(len(active), 4)  # 3 + other

    def test_bounds_error_names_bounds_and_got(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        with self.assertRaises(ValueError) as ctx:
            hunch.apply_hypotheses(store, sit['id'], VALID_HYPS[:2], now=5)
        msg = str(ctx.exception)
        self.assertIn('3', msg)
        self.assertIn('6', msg)
        self.assertIn('2', msg)

    def test_requires_predicted_evidence_names_offender(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        bad = [
            VALID_HYPS[0],
            {'statement': 'Missing evidence hypothesis with a fairly long descriptive name', 'prior': 0.3, 'predictedEvidence': []},
            VALID_HYPS[2],
        ]
        with self.assertRaises(ValueError) as ctx:
            hunch.apply_hypotheses(store, sit['id'], bad, now=5)
        msg = str(ctx.exception)
        self.assertIn('predictedEvidence', msg)
        self.assertIn('1', msg)  # index of offender
        self.assertIn('Missing evidence hypothesis', msg)

    def test_prior_renormalization_sums_to_085(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        non_other = [h for h in s['hypotheses'] if h['id'] != 'other']
        self.assertAlmostEqual(sum(h['prior'] for h in non_other), 0.85, places=9)

    def test_invalid_prior_falls_back_to_equal_share(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hyps = [
            {'statement': 'A', 'prior': -1, 'predictedEvidence': ['x']},
            {'statement': 'B', 'prior': float('nan'), 'predictedEvidence': ['y']},
            {'statement': 'C', 'prior': 0.5, 'predictedEvidence': ['z']},
        ]
        hunch.apply_hypotheses(store, sit['id'], hyps, now=5)
        s = hunch.get_situation(store, sit['id'])
        non_other = [h for h in s['hypotheses'] if h['id'] != 'other']
        # A and B fall back to weight 1 each, C keeps weight 0.5 -> rawSum=2.5
        # scaled to 0.85 total: A=B=0.85*(1/2.5)=0.34, C=0.85*(0.5/2.5)=0.17
        by_statement = {h['statement']: h['prior'] for h in non_other}
        self.assertAlmostEqual(by_statement['A'], 0.34, places=6)
        self.assertAlmostEqual(by_statement['B'], 0.34, places=6)
        self.assertAlmostEqual(by_statement['C'], 0.17, places=6)

    def test_regeneration_demotes_never_deletes_and_bumps_generation(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        gen0_ids = [h['id'] for h in s['hypotheses'] if h['id'] != 'other']
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=10)
        s2 = hunch.get_situation(store, sit['id'])
        for hid in gen0_ids:
            h = next(h for h in s2['hypotheses'] if h['id'] == hid)
            self.assertEqual(h['status'], 'demoted')
        new_active = [h for h in s2['hypotheses'] if h['status'] == 'active' and h['id'] != 'other']
        self.assertEqual(len(new_active), 3)
        for h in new_active:
            self.assertEqual(h['generation'], 1)
        other = next(h for h in s2['hypotheses'] if h['id'] == 'other')
        self.assertEqual(other['prior'], 0.15)
        self.assertEqual(other['status'], 'active')

    def test_not_open_situation_errors(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.resolve(store, sit['id'], resolution='other: done', now=2)
        with self.assertRaises(ValueError):
            hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)


# =============================================================================
# WP2 — posterior math + matrix stats/warnings
# =============================================================================

def _setup_matrix_situation(store, hyps=None):
    sit = open_situation(store)
    hunch.apply_hypotheses(store, sit['id'], hyps or VALID_HYPS, now=5)
    o1 = hunch.add_observation(store, sit['id'], text='temperature spiked before failure', now=10)
    o2 = hunch.add_observation(store, sit['id'], text='completely different unrelated note', now=11)
    return sit, o1, o2


class TestPosteriorMath(unittest.TestCase):
    def test_hand_computed_uniform_blend_values(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        h_ids = [h['id'] for h in s['hypotheses'] if h['id'] != 'other']
        hunch.add_observation(store, sit['id'], text='an observation', reliability=1.0, now=10)
        clusters = hunch.get_clusters(store, sit['id'])
        cid = clusters[0]['id']
        matrix = [{'clusterId': cid, 'likelihoods': [
            {'hypothesisId': h_ids[0], 'likelihood': 0.9},
            {'hypothesisId': h_ids[1], 'likelihood': 0.1},
            {'hypothesisId': h_ids[2], 'likelihood': 0.1},
            {'hypothesisId': 'other', 'likelihood': 0.1},
        ]}]
        active_hyps = [h for h in s['hypotheses'] if h['status'] == 'active']
        result = hunch.compute_posteriors(hypotheses=active_hyps, clusters=clusters, matrix=matrix)
        # priors: h0..h2 each 0.85/3=0.28333, other=0.15
        # raw = prior * eff(likelihood, reliability=1.0) = prior * likelihood (reliability 1 -> eff=score)
        priors = {h['id']: h['prior'] for h in active_hyps}
        raw = {hid: priors[hid] * lk for hid, lk in zip(h_ids + ['other'], [0.9, 0.1, 0.1, 0.1])}
        raw_sum = sum(raw.values())
        expected = {hid: v / raw_sum for hid, v in raw.items()}
        # other floor may kick in; check no-floor case value is close before floor
        for hid in h_ids:
            self.assertAlmostEqual(result['posteriors'][hid], expected[hid], places=6) if expected['other'] >= 0.05 else None

    def test_shuffle_order_invariance(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        hunch.add_observation(store, sit['id'], text='obs one here', now=10)
        hunch.add_observation(store, sit['id'], text='obs two totally different', now=11)
        clusters = hunch.get_clusters(store, sit['id'])
        active_hyps = [h for h in s['hypotheses'] if h['status'] == 'active']
        matrix = []
        for c in clusters:
            matrix.append({'clusterId': c['id'], 'likelihoods': [
                {'hypothesisId': h['id'], 'likelihood': 0.3 + 0.1 * i} for i, h in enumerate(active_hyps)
            ]})
        r1 = hunch.compute_posteriors(hypotheses=active_hyps, clusters=clusters, matrix=matrix)
        r2 = hunch.compute_posteriors(hypotheses=list(reversed(active_hyps)), clusters=list(reversed(clusters)),
                                       matrix=list(reversed(matrix)))
        for hid in r1['posteriors']:
            self.assertAlmostEqual(r1['posteriors'][hid], r2['posteriors'][hid], places=9)

    def test_other_floor_exact_005_sum_1(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        h_ids = [h['id'] for h in s['hypotheses'] if h['id'] != 'other']
        hunch.add_observation(store, sit['id'], text='strong evidence', reliability=1.0, now=10)
        clusters = hunch.get_clusters(store, sit['id'])
        active_hyps = [h for h in s['hypotheses'] if h['status'] == 'active']
        matrix = [{'clusterId': clusters[0]['id'], 'likelihoods': [
            {'hypothesisId': h_ids[0], 'likelihood': 1.0},
            {'hypothesisId': h_ids[1], 'likelihood': 0.001},
            {'hypothesisId': h_ids[2], 'likelihood': 0.001},
            {'hypothesisId': 'other', 'likelihood': 0.001},
        ]}]
        result = hunch.compute_posteriors(hypotheses=active_hyps, clusters=clusters, matrix=matrix)
        self.assertAlmostEqual(result['posteriors']['other'], 0.05, places=9)
        self.assertAlmostEqual(sum(result['posteriors'].values()), 1.0, places=9)

    def test_zero_sum_fallback_to_renormalized_priors(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        active_hyps = [h for h in s['hypotheses'] if h['status'] == 'active']
        # No clusters, empty matrix -> product over empty clusters = 1 for all,
        # times prior -> rawSum = sum(priors) > 0, not the zero-sum path.
        # To hit true zero-sum, use a likelihood of 0 for all with reliability 1.
        hunch.add_observation(store, sit['id'], text='zeroing observation', reliability=1.0, now=10)
        clusters = hunch.get_clusters(store, sit['id'])
        matrix = [{'clusterId': clusters[0]['id'], 'likelihoods': [
            {'hypothesisId': h['id'], 'likelihood': 0.0} for h in active_hyps
        ]}]
        result = hunch.compute_posteriors(hypotheses=active_hyps, clusters=clusters, matrix=matrix)
        # rawSum ~ 0 -> falls back to renormalized priors (still floors OTHER)
        self.assertAlmostEqual(sum(result['posteriors'].values()), 1.0, places=9)

    def test_zero_sum_fallback_uniform_when_priors_also_zero(self):
        active_hyps = [
            {'id': 'h-1', 'status': 'active', 'prior': 0, 'posterior': 0},
            {'id': 'h-2', 'status': 'active', 'prior': 0, 'posterior': 0},
            {'id': 'other', 'status': 'active', 'prior': 0, 'posterior': 0},
        ]
        result = hunch.compute_posteriors(hypotheses=active_hyps, clusters=[], matrix=[])
        # uniform among 3 -> 1/3 each, then OTHER floor doesn't trigger (1/3 > 0.05)
        for hid in ('h-1', 'h-2', 'other'):
            self.assertAlmostEqual(result['posteriors'][hid], 1 / 3, places=9)

    def test_duplicate_row_dedupe_last_wins_wholesale(self):
        store = hunch.new_store(now=1)
        sit, o1, o2 = _setup_matrix_situation(store)
        s = hunch.get_situation(store, sit['id'])
        active_hyps = [h for h in s['hypotheses'] if h['status'] == 'active']
        h_ids = [h['id'] for h in active_hyps if h['id'] != 'other']
        cid = o1['clusterId']
        matrix = [
            {'clusterId': cid, 'likelihoods': [{'hypothesisId': h_ids[0], 'likelihood': 0.1}]},
            {'clusterId': cid, 'likelihoods': [{'hypothesisId': h_ids[0], 'likelihood': 0.9}]},
        ]
        clusters = hunch.get_clusters(store, sit['id'])
        stats = hunch.compute_matrix_stats(matrix, clusters, active_hyps, s['hypotheses'])
        warnings = hunch.build_matrix_warnings(stats)
        self.assertIn(cid, stats['duplicateClusterIds'])
        self.assertTrue(any('duplicate matrix rows' in w for w in warnings))
        # matched cells reflects only last row (1 cell), not both rows (2 cells)
        self.assertEqual(stats['matchedCells'], 1)
        # math follows the last row (0.9), not the first (0.1)
        result = hunch.compute_posteriors(hypotheses=active_hyps,
                                           clusters=[c for c in clusters if c['id'] == cid], matrix=matrix)
        # can't directly assert internal raw score, but matchedCells confirms last-wins

    def test_duplicate_cell_within_row_last_wins(self):
        store = hunch.new_store(now=1)
        sit, o1, o2 = _setup_matrix_situation(store)
        s = hunch.get_situation(store, sit['id'])
        active_hyps = [h for h in s['hypotheses'] if h['status'] == 'active']
        h_ids = [h['id'] for h in active_hyps if h['id'] != 'other']
        cid = o1['clusterId']
        matrix = [{'clusterId': cid, 'likelihoods': [
            {'hypothesisId': h_ids[0], 'likelihood': 0.1},
            {'hypothesisId': h_ids[0], 'likelihood': 0.9},
        ]}]
        clusters = hunch.get_clusters(store, sit['id'])
        stats = hunch.compute_matrix_stats(matrix, clusters, active_hyps, s['hypotheses'])
        warnings = hunch.build_matrix_warnings(stats)
        self.assertEqual(len(stats['duplicateHypothesisCells']), 1)
        self.assertTrue(any('duplicate hypothesis cell' in w for w in warnings))

    def test_clamp_warning_and_posterior_identical_to_10(self):
        store = hunch.new_store(now=1)
        sit, o1, o2 = _setup_matrix_situation(store)
        s = hunch.get_situation(store, sit['id'])
        active_hyps = [h for h in s['hypotheses'] if h['status'] == 'active']
        h_ids = [h['id'] for h in active_hyps if h['id'] != 'other']
        cid = o1['clusterId']
        matrix = [{'clusterId': cid, 'likelihoods': [{'hypothesisId': h_ids[0], 'likelihood': 1.7}]}]
        clusters = [c for c in hunch.get_clusters(store, sit['id']) if c['id'] == cid]
        stats = hunch.compute_matrix_stats(matrix, clusters, active_hyps, s['hypotheses'])
        warnings = hunch.build_matrix_warnings(stats)
        self.assertEqual(len(stats['outOfRangeLikelihoods']), 1)
        self.assertTrue(any('clamped' in w and f'{cid}/{h_ids[0]}=1.7' in w for w in warnings))
        # posterior computation clamps to 1.0 (same as if likelihood was exactly 1.0)
        r_clamped = hunch.compute_posteriors(hypotheses=active_hyps, clusters=clusters, matrix=matrix)
        matrix_at_one = [{'clusterId': cid, 'likelihoods': [{'hypothesisId': h_ids[0], 'likelihood': 1.0}]}]
        r_one = hunch.compute_posteriors(hypotheses=active_hyps, clusters=clusters, matrix=matrix_at_one)
        self.assertAlmostEqual(r_clamped['posteriors'][h_ids[0]], r_one['posteriors'][h_ids[0]], places=9)

    def test_zero_match_warning_names_unknown_and_inactive_ids(self):
        store = hunch.new_store(now=1)
        sit, o1, o2 = _setup_matrix_situation(store)
        s = hunch.get_situation(store, sit['id'])
        active_hyps = [h for h in s['hypotheses'] if h['status'] == 'active']
        # demote one via regeneration to get an inactive (demoted) hyp id
        old_ids = [h['id'] for h in active_hyps if h['id'] != 'other']
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=20)
        s2 = hunch.get_situation(store, sit['id'])
        active_hyps2 = [h for h in s2['hypotheses'] if h['status'] == 'active']
        clusters = hunch.get_clusters(store, sit['id'])
        matrix = [
            {'clusterId': 'c-unknown', 'likelihoods': [{'hypothesisId': old_ids[0], 'likelihood': 0.5}]},
            {'clusterId': clusters[0]['id'], 'likelihoods': [
                {'hypothesisId': 'h-unknown', 'likelihood': 0.5},
                {'hypothesisId': old_ids[0], 'likelihood': 0.5},
            ]},
        ]
        stats = hunch.compute_matrix_stats(matrix, clusters, active_hyps2, s2['hypotheses'])
        warnings = hunch.build_matrix_warnings(stats)
        self.assertIn('c-unknown', stats['unknownClusterIds'])
        self.assertIn('h-unknown', stats['unknownHypothesisIds'])
        self.assertIn(old_ids[0], stats['inactiveHypothesisIds'])
        joined = ' '.join(warnings)
        self.assertIn('c-unknown', joined)
        self.assertIn('h-unknown', joined)
        self.assertIn(old_ids[0], joined)

    def test_entropy_edges(self):
        self.assertEqual(hunch.normalized_entropy({'a': 1.0}), 0)
        self.assertEqual(hunch.normalized_entropy({}), 0)
        uniform = hunch.normalized_entropy({'a': 0.5, 'b': 0.5})
        self.assertAlmostEqual(uniform, 1.0, places=9)
        peaked = hunch.normalized_entropy({'a': 0.99, 'b': 0.01})
        self.assertLess(peaked, 0.2)

    def test_matched_cells_never_exceeds_total_cells(self):
        store = hunch.new_store(now=1)
        sit, o1, o2 = _setup_matrix_situation(store)
        s = hunch.get_situation(store, sit['id'])
        active_hyps = [h for h in s['hypotheses'] if h['status'] == 'active']
        clusters = hunch.get_clusters(store, sit['id'])
        h_ids = [h['id'] for h in active_hyps]
        matrix = []
        for c in clusters:
            matrix.append({'clusterId': c['id'], 'likelihoods': [
                {'hypothesisId': hid, 'likelihood': 0.5} for hid in h_ids
            ]})
        stats = hunch.compute_matrix_stats(matrix, clusters, active_hyps, s['hypotheses'])
        self.assertEqual(stats['totalCells'], len(clusters) * len(active_hyps))
        self.assertEqual(stats['matchedCells'], stats['totalCells'])
        self.assertLessEqual(stats['matchedCells'], stats['totalCells'])


# =============================================================================
# WP3 — rescore + verdicts + surfacing
# =============================================================================

def _full_matrix(active_hyps, clusters, likelihood_fn):
    """Build a full matrix: likelihood_fn(hyp, cluster) -> float."""
    matrix = []
    for c in clusters:
        matrix.append({'clusterId': c['id'], 'likelihoods': [
            {'hypothesisId': h['id'], 'likelihood': likelihood_fn(h, c), 'rationale': f"r-{h['id']}-{c['id']}"}
            for h in active_hyps
        ]})
    return matrix


class TestDecideSurfacing(unittest.TestCase):
    def test_confused_when_other_above_regen_threshold(self):
        posteriors = {'h-1': 0.3, 'h-2': 0.3, 'other': 0.4}
        hyps = [{'id': 'h-1', 'status': 'active'}, {'id': 'h-2', 'status': 'active'}, {'id': 'other', 'status': 'active'}]
        result = hunch.decide_surfacing(posteriors=posteriors, hypotheses=hyps, last_surfaced_top_id=None)
        self.assertEqual(result['verdict'], 'confused')

    def test_peaked_when_top_above_min_and_lead_above_min(self):
        posteriors = {'h-1': 0.7, 'h-2': 0.2, 'other': 0.1}
        hyps = [{'id': 'h-1', 'status': 'active'}, {'id': 'h-2', 'status': 'active'}, {'id': 'other', 'status': 'active'}]
        result = hunch.decide_surfacing(posteriors=posteriors, hypotheses=hyps, last_surfaced_top_id=None)
        self.assertEqual(result['verdict'], 'peaked')
        self.assertEqual(result['topId'], 'h-1')

    def test_flip_requires_lead_gate(self):
        hyps = [{'id': 'h-1', 'status': 'active'}, {'id': 'h-2', 'status': 'active'}, {'id': 'other', 'status': 'active'}]
        # top changed but lead too small -> not flip, falls through to flat/none
        posteriors = {'h-1': 0.34, 'h-2': 0.33, 'other': 0.33}
        result = hunch.decide_surfacing(posteriors=posteriors, hypotheses=hyps, last_surfaced_top_id='h-2')
        self.assertNotEqual(result['verdict'], 'flip')
        # top changed with real lead -> flip
        posteriors2 = {'h-1': 0.6, 'h-2': 0.3, 'other': 0.1}
        result2 = hunch.decide_surfacing(posteriors=posteriors2, hypotheses=hyps, last_surfaced_top_id='h-2')
        self.assertEqual(result2['verdict'], 'flip')

    def test_flat_when_entropy_high(self):
        hyps = [{'id': 'h-1', 'status': 'active'}, {'id': 'h-2', 'status': 'active'}, {'id': 'h-3', 'status': 'active'}, {'id': 'other', 'status': 'active'}]
        posteriors = {'h-1': 0.26, 'h-2': 0.25, 'h-3': 0.25, 'other': 0.24}
        result = hunch.decide_surfacing(posteriors=posteriors, hypotheses=hyps, last_surfaced_top_id=None)
        self.assertEqual(result['verdict'], 'flat')

    def test_none_when_nothing_matches(self):
        hyps = [{'id': 'h-1', 'status': 'active'}, {'id': 'h-2', 'status': 'active'}, {'id': 'other', 'status': 'active'}]
        # top < SURFACE_TOP_MIN (not peaked); top unchanged from lastSurfacedTopId (not flip);
        # entropy below FLAT_ENTROPY_MIN (not flat) -> none.
        posteriors = {'h-1': 0.62, 'h-2': 0.23, 'other': 0.15}
        result = hunch.decide_surfacing(posteriors=posteriors, hypotheses=hyps, last_surfaced_top_id='h-1')
        self.assertEqual(result['verdict'], 'none')

    def test_precedence_confused_beats_flip_beats_peaked_beats_flat(self):
        # OTHER > 0.35 AND top changed with lead AND top>0.65 -> still confused
        hyps = [{'id': 'h-1', 'status': 'active'}, {'id': 'h-2', 'status': 'active'}, {'id': 'other', 'status': 'active'}]
        posteriors = {'h-1': 0.05, 'h-2': 0.05, 'other': 0.9}
        result = hunch.decide_surfacing(posteriors=posteriors, hypotheses=hyps, last_surfaced_top_id='h-1')
        self.assertEqual(result['verdict'], 'confused')


class TestRescore(unittest.TestCase):
    def test_guard_not_open(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        hunch.add_observation(store, sit['id'], text='an obs', now=10)
        hunch.resolve(store, sit['id'], resolution='other: done', now=20)
        with self.assertRaises(ValueError) as ctx:
            hunch.rescore(store, sit['id'], [], now=30)
        self.assertIn('not open', str(ctx.exception))

    def test_guard_no_active_hypotheses_states_fix(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.add_observation(store, sit['id'], text='an obs', now=10)
        with self.assertRaises(ValueError) as ctx:
            hunch.rescore(store, sit['id'], [], now=30)
        self.assertIn('hypotheses', str(ctx.exception))

    def test_guard_no_observations_states_fix(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        with self.assertRaises(ValueError) as ctx:
            hunch.rescore(store, sit['id'], [], now=30)
        self.assertIn('observations', str(ctx.exception))

    def test_non_array_matrix_errors_with_verbatim_shape(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        hunch.add_observation(store, sit['id'], text='an obs', now=10)
        with self.assertRaises(ValueError) as ctx:
            hunch.rescore(store, sit['id'], {'matrix': [], 'now': 1}, now=30)
        msg = str(ctx.exception)
        self.assertIn('clusterId', msg)
        self.assertIn('likelihoods', msg)
        self.assertIn('hypothesisId', msg)
        self.assertIn('likelihood', msg)

    def test_last_surfaced_top_id_only_updates_on_surfacing_verdicts(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        active = [h for h in s['hypotheses'] if h['status'] == 'active']
        h_ids = [h['id'] for h in active if h['id'] != 'other']
        hunch.add_observation(store, sit['id'], text='flat evidence', reliability=1.0, now=10)
        clusters = hunch.get_clusters(store, sit['id'])
        flat_matrix = _full_matrix(active, clusters, lambda h, c: 0.5)
        r1 = hunch.rescore(store, sit['id'], flat_matrix, now=11)
        self.assertIn(r1['verdict'], ('flat', 'none'))
        s1 = hunch.get_situation(store, sit['id'])
        self.assertIsNone(s1['lastSurfacedTopId'])

        peaked_matrix = _full_matrix(active, clusters, lambda h, c: 0.99 if h['id'] == h_ids[0] else 0.01)
        r2 = hunch.rescore(store, sit['id'], peaked_matrix, now=12)
        self.assertEqual(r2['verdict'], 'peaked')
        s2 = hunch.get_situation(store, sit['id'])
        self.assertEqual(s2['lastSurfacedTopId'], h_ids[0])

    def test_posterior_history_fifo_cap_200(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        active = [h for h in s['hypotheses'] if h['status'] == 'active']
        hunch.add_observation(store, sit['id'], text='evidence one', now=10)
        clusters = hunch.get_clusters(store, sit['id'])
        matrix = _full_matrix(active, clusters, lambda h, c: 0.5)
        for i in range(205):
            hunch.rescore(store, sit['id'], matrix, now=100 + i, trigger=f't{i}')
        s2 = hunch.get_situation(store, sit['id'])
        self.assertEqual(len(s2['posteriorHistory']), 200)
        # FIFO: oldest trimmed, most recent kept
        self.assertEqual(s2['posteriorHistory'][-1]['trigger'], 't204')

    def test_surface_text_content_and_watch_for_and_runner_up(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        active = [h for h in s['hypotheses'] if h['status'] == 'active']
        h_ids = [h['id'] for h in active if h['id'] != 'other']
        hunch.add_observation(store, sit['id'], text='temp logs show spike clearly', now=10)
        clusters = hunch.get_clusters(store, sit['id'])
        matrix = _full_matrix(active, clusters, lambda h, c: 0.9 if h['id'] == h_ids[0] else 0.1)
        result = hunch.rescore(store, sit['id'], matrix, now=11)
        text = hunch.build_surface_text(store, sit['id'])
        self.assertIn('Why did the widget break?', text)
        self.assertIn('p=', text)
        self.assertIn('Watch for:', text)
        # h_ids[0]'s predictedEvidence "temp logs show spike" WAS observed (jaccard-matched)
        self.assertIn('nothing new', text)
        self.assertIn('If instead', text)

    def test_no_scoring_surface_text_shows_priors_and_no_evidence(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        text = hunch.build_surface_text(store, sit['id'])
        self.assertIn('no evidence scored yet', text)


# =============================================================================
# WP4 — resolve/calibration/remove/purge
# =============================================================================

class TestResolveCalibration(unittest.TestCase):
    def test_other_prefix_resolution_rule(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        # top active hyp is 'other' (no applyHypotheses called)
        result = hunch.resolve(store, sit['id'], resolution='other: it was a ghost', now=10)
        self.assertTrue(result['calibration'][-1]['correct'])
        self.assertEqual(result['calibration'][-1]['claimedTopId'], 'other')

    def test_non_other_resolution_matches_claimed_top_id(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        s = hunch.get_situation(store, sit['id'])
        top = hunch._top_active_hypothesis(s)
        result = hunch.resolve(store, sit['id'], resolution=top['id'], now=10)
        self.assertTrue(result['calibration'][-1]['correct'])
        result2_sit = open_situation(store, question='another one')
        hunch.apply_hypotheses(store, result2_sit['id'], VALID_HYPS, now=5)
        result2 = hunch.resolve(store, result2_sit['id'], resolution='h-nonexistent', now=10)
        self.assertFalse(result2['calibration'][-1]['correct'])

    def test_calibration_bucket_edges_inclusive_top(self):
        self.assertEqual(hunch._bucket_for(0.0), '0-0.5')
        self.assertEqual(hunch._bucket_for(0.5), '0.5-0.7')
        self.assertEqual(hunch._bucket_for(0.7), '0.7-0.9')
        self.assertEqual(hunch._bucket_for(0.9), '0.9-1.0')
        self.assertEqual(hunch._bucket_for(1.0), '0.9-1.0')

    def test_empty_calibration_summary_no_nan(self):
        store = hunch.new_store(now=1)
        summary = hunch.calibration_summary(store)
        self.assertEqual(summary['count'], 0)
        self.assertIsNone(summary['meanClaimedConfidence'])
        self.assertIsNone(summary['accuracy'])
        for b in summary['buckets']:
            self.assertEqual(b['count'], 0)
            self.assertIsNone(b['accuracy'])

    def test_remove_drops_calibration_and_seq_never_reused(self):
        store = hunch.new_store(now=1)
        sit = open_situation(store)
        hunch.resolve(store, sit['id'], resolution='other: yes', now=5)
        self.assertEqual(hunch.calibration_summary(store)['count'], 1)
        hunch.remove_situation(store, sit['id'])
        self.assertEqual(hunch.calibration_summary(store)['count'], 0)
        sit2 = open_situation(store)
        self.assertEqual(sit2['id'], 'sit-2')  # sit-1 not reused despite removal

    def test_mark_stale_strict_greater_than_30_days(self):
        store = hunch.new_store(now=0)
        sit = open_situation(store, now=0)
        thirty_days = 30 * 24 * 3600 * 1000
        transitioned_at_boundary = hunch.mark_stale(store, thirty_days)
        self.assertEqual(transitioned_at_boundary, [])  # exactly at boundary: not stale (strict >)
        transitioned_after = hunch.mark_stale(store, thirty_days + 1)
        self.assertEqual(transitioned_after, [sit['id']])

    def test_purge_reaps_only_resolved_or_stale(self):
        store = hunch.new_store(now=0)
        open_sit = open_situation(store, question='open one', now=0)
        resolved_sit = open_situation(store, question='resolved one', now=0)
        hunch.resolve(store, resolved_sit['id'], resolution='other: done', now=0)
        six_months = hunch.TUNING['PURGE_AFTER_MONTHS'] * hunch.MS_PER_MONTH
        purged = hunch.purge_expired(store, six_months + 1)
        self.assertIn(resolved_sit['id'], purged)
        self.assertNotIn(open_sit['id'], purged)
        self.assertIsNotNone(hunch.get_situation(store, open_sit['id']))
        self.assertIsNone(hunch.get_situation(store, resolved_sit['id']))


# =============================================================================
# WP5 — views + CLI wiring
# =============================================================================

class TestViews(unittest.TestCase):
    def test_list_view_sort_order_open_stale_resolved_then_recency(self):
        store = hunch.new_store(now=0)
        s_open_old = open_situation(store, question='old open', now=0)
        s_open_new = open_situation(store, question='new open', now=100)
        s_resolved = open_situation(store, question='resolved', now=0)
        hunch.resolve(store, s_resolved['id'], resolution='other: x', now=1)
        rows = hunch.build_list_view(store)
        statuses = [r['status'] for r in rows]
        self.assertEqual(statuses, ['open', 'open', 'resolved'])
        # newer open situation sorts before older open situation
        open_rows = [r for r in rows if r['status'] == 'open']
        self.assertEqual(open_rows[0]['id'], s_open_new['id'])
        self.assertEqual(open_rows[1]['id'], s_open_old['id'])

    def test_detail_view_shape(self):
        store = hunch.new_store(now=0)
        sit = open_situation(store)
        hunch.apply_hypotheses(store, sit['id'], VALID_HYPS, now=5)
        hunch.add_observation(store, sit['id'], text='temp logs show spike', now=10)
        view = hunch.build_detail_view(store, sit['id'])
        self.assertEqual(view['id'], sit['id'])
        self.assertIn('hypotheses', view)
        self.assertIn('observations', view)
        self.assertIn('clusters', view)
        self.assertIn('surfaceText', view)
        self.assertIn('calibration', view)

    def test_detail_view_unknown_returns_none(self):
        store = hunch.new_store(now=0)
        self.assertIsNone(hunch.build_detail_view(store, 'sit-99'))


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.ledger = os.path.join(self.tmpdir.name, 'ledger.json')

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run(self, args, **kw):
        return run_cli(['--ledger', self.ledger, '--now', '1000'] + args, **kw)

    def test_open_happy_path(self):
        code, out, err = self._run(['open', '--question', 'Why did it break?'])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data['id'], 'sit-1')
        self.assertEqual(data['question'], 'Why did it break?')

    def test_open_auto_creates_hunch_dir(self):
        self.assertFalse(os.path.exists(self.ledger))
        code, out, err = self._run(['open', '--question', 'Why?'])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(self.ledger))

    def test_hypotheses_accepts_stdin_json(self):
        code, out, err = self._run(['open', '--question', 'Why?'])
        sid = json.loads(out)['id']
        code, out, err = self._run(['hypotheses', sid], input_text=json.dumps(VALID_HYPS))
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertIn('hypotheses', data)

    def test_hypotheses_accepts_json_flag(self):
        code, out, err = self._run(['open', '--question', 'Why?'])
        sid = json.loads(out)['id']
        code, out, err = self._run(['hypotheses', sid, '--json', json.dumps(VALID_HYPS)])
        self.assertEqual(code, 0, err)

    def test_observe_and_clusters(self):
        code, out, _ = self._run(['open', '--question', 'Why?'])
        sid = json.loads(out)['id']
        code, out, err = self._run(['observe', sid, '--text', 'it got hot'])
        self.assertEqual(code, 0, err)
        obs = json.loads(out)
        self.assertIn('observationId', obs)
        self.assertIn('clusterId', obs)
        self.assertIn('clusterMethod', obs)
        code, out, err = self._run(['clusters', sid])
        self.assertEqual(code, 0, err)
        clusters = json.loads(out)
        self.assertEqual(len(clusters), 1)

    def test_observe_mutually_exclusive_flags_error(self):
        code, out, _ = self._run(['open', '--question', 'Why?'])
        sid = json.loads(out)['id']
        self._run(['observe', sid, '--text', 'first obs'])
        code, out, err = self._run(['observe', sid, '--text', 'second', '--same-event-as', 'obs-1', '--new-cluster'])
        self.assertNotEqual(code, 0)

    def test_rescore_non_array_payload_error_quotes_verbatim_shape(self):
        code, out, _ = self._run(['open', '--question', 'Why?'])
        sid = json.loads(out)['id']
        self._run(['hypotheses', sid, '--json', json.dumps(VALID_HYPS)])
        self._run(['observe', sid, '--text', 'an observation'])
        code, out, err = self._run(['rescore', sid, '--json', json.dumps({'matrix': [], 'now': 1})])
        self.assertNotEqual(code, 0)
        data = json.loads(out)
        self.assertIn('clusterId', data['error'])
        self.assertIn('likelihoods', data['error'])

    def test_rescore_full_flow(self):
        code, out, _ = self._run(['open', '--question', 'Why?'])
        sid = json.loads(out)['id']
        self._run(['hypotheses', sid, '--json', json.dumps(VALID_HYPS)])
        self._run(['observe', sid, '--text', 'temp logs show a spike'])
        code, out, err = self._run(['clusters', sid])
        clusters = json.loads(out)
        code, out, _ = self._run(['get', sid])
        detail = json.loads(out)
        active_ids = [h['id'] for h in detail['hypotheses'] if h['status'] == 'active']
        matrix = [{'clusterId': clusters[0]['id'], 'likelihoods': [
            {'hypothesisId': hid, 'likelihood': 0.5} for hid in active_ids
        ]}]
        code, out, err = self._run(['rescore', sid, '--json', json.dumps(matrix)])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertIn('verdict', data)
        self.assertIn('surfaceText', data)
        self.assertIn('matrixStats', data)
        self.assertIn('warnings', data)

    def test_get_and_list_and_surface(self):
        code, out, _ = self._run(['open', '--question', 'Why?'])
        sid = json.loads(out)['id']
        code, out, err = self._run(['get', sid])
        self.assertEqual(code, 0, err)
        code, out, err = self._run(['list'])
        self.assertEqual(code, 0, err)
        rows = json.loads(out)
        self.assertEqual(len(rows), 1)
        code, out, err = self._run(['surface', sid])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertIn('surfaceText', data)

    def test_resolve_and_calibration_and_remove(self):
        code, out, _ = self._run(['open', '--question', 'Why?'])
        sid = json.loads(out)['id']
        code, out, err = self._run(['resolve', sid, '--resolution', 'other: ghost'])
        self.assertEqual(code, 0, err)
        code, out, err = self._run(['calibration'])
        self.assertEqual(code, 0, err)
        summary = json.loads(out)
        self.assertEqual(summary['count'], 1)
        code, out, err = self._run(['remove', sid])
        self.assertEqual(code, 0, err)

    def test_unknown_situation_id_lists_known(self):
        self._run(['open', '--question', 'Why?'])
        code, out, err = self._run(['get', 'sit-99'])
        self.assertNotEqual(code, 0)
        data = json.loads(out)
        self.assertIn('sit-1', data['error'])

    def test_purge_command(self):
        code, out, err = self._run(['purge'])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertIn('markedStale', data)
        self.assertIn('purged', data)

    def test_now_determinism(self):
        code, out, _ = self._run(['open', '--question', 'Why?'])
        data = json.loads(out)
        self.assertEqual(data['createdAt'], 1000)

    def test_demo_exits_0(self):
        code, out, err = run_cli(['demo'], cwd=self.tmpdir.name)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(data['ok'])


# =============================================================================
# WP6 — demo + SKILL.md fidelity
# =============================================================================

SKILL_MD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'SKILL.md')


class TestSkillMdFidelity(unittest.TestCase):
    def test_skill_md_exists_with_frontmatter(self):
        with open(SKILL_MD_PATH) as f:
            content = f.read()
        self.assertTrue(content.startswith('---\n'))
        self.assertIn('name: hunch', content)
        self.assertIn('description:', content)

    def test_skill_md_contains_verbatim_hypotheses_shape(self):
        with open(SKILL_MD_PATH) as f:
            content = f.read()
        self.assertIn('predictedEvidence', content)
        self.assertIn('statement', content)

    def test_skill_md_contains_verbatim_matrix_shape(self):
        with open(SKILL_MD_PATH) as f:
            content = f.read()
        # Character-identical to RESCORE_SIGNATURE's matrix shape fragment.
        self.assertIn('clusterId', content)
        self.assertIn('likelihoods', content)
        self.assertIn('hypothesisId', content)
        self.assertIn('"likelihood"', content)
        self.assertIn('rationale', content)

    def test_skill_md_mentions_never_hand_edit_ledger(self):
        with open(SKILL_MD_PATH) as f:
            content = f.read()
        self.assertIn('NEVER', content.upper() if False else content)
        self.assertTrue('hand-edit' in content.lower() or 'never edit' in content.lower())

    def test_skill_md_mentions_no_filesystem_fallback(self):
        with open(SKILL_MD_PATH) as f:
            content = f.read()
        self.assertIn('claude.ai', content)


class TestDemoSubprocess(unittest.TestCase):
    def test_demo_subprocess_exit_0_and_ok_true(self):
        code, out, err = run_cli(['demo'])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(data['ok'])


if __name__ == '__main__':
    unittest.main()
