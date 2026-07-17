#!/usr/bin/env python3
"""hunch — Bayesian hypothesis tracking for explanation questions.

A portable, zero-dependency (Python 3.9+ stdlib only) port of the TARS
Abduction Engine (abduction-lib.js) plus the tokenSet/jaccard fuzzy-dedupe
helper from cue-light-dedupe.js. The model (Claude) supplies judgment —
hypotheses, likelihood matrices, cluster assignments — this script does the
honest Bayesian bookkeeping and owns a JSON ledger the model never
hand-edits directly.

All JSON wire keys are deliberately kept camelCase (clusterId, hypothesisId,
likelihood, predictedEvidence, sameEventAs, matrixStats, posteriorHistory,
lastScoring, clusterMethod, ...) to match the CLI's own validation exactly —
this is NOT a Python-style schema, on purpose.

Usage: python3 hunch.py <command> [options]
Run `python3 hunch.py --help` for the full command list, or `demo` for an
end-to-end walkthrough.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time

# =============================================================================
# Layer 1: TUNING — all tuning knobs, ported verbatim from ABDUCTION_TUNING
# (js/abduction-lib.js:19-44). Grouped in one dict so every threshold this
# engine uses is discoverable and adjustable in one place.
# =============================================================================

TUNING = {
    'OTHER_ID': 'other',
    'OTHER_INITIAL_PRIOR': 0.15,      # reserved "explanation not in candidate set" mass
    'OTHER_FLOOR': 0.05,              # posterior floor — epistemic humility never dies
    'REGEN_OTHER_THRESHOLD': 0.35,    # OTHER above this -> hypothesis space is wrong, regenerate
    'MIN_HYPOTHESES': 3,
    'MAX_HYPOTHESES': 6,
    'NEUTRAL_LIKELIHOOD': 0.5,        # blend target for unreliable evidence
    'POSTERIOR_ZERO_EPSILON': 1e-12,  # float guard for "all raw posteriors are zero" checks
    'RELIABILITY_DEFAULTS': {'firsthand': 1.0, 'secondhand': 0.6, 'rumor': 0.3, 'inferred': 0.5},
    'CLUSTER_JACCARD_THRESHOLD': 0.6,
    'SURFACE_TOP_MIN': 0.65,          # "peaked" needs top above this...
    'SURFACE_LEAD_MIN': 0.25,         # ...and this much lead over runner-up
    'FLAT_ENTROPY_MIN': 0.85,         # normalized entropy above this -> stay silent
    'STALE_AFTER_MS': 30 * 24 * 3600 * 1000,  # no new observation in 30d -> stale
    'PURGE_AFTER_MONTHS': 6,          # default retention for resolved/stale
    'POSTERIOR_HISTORY_CAP': 200,     # FIFO cap on belief-over-time snapshots
    # A model-supplied hypothesis prior that is non-finite or <= 0 falls back
    # to this raw weight (an equal share among the malformed siblings) rather
    # than a near-zero epsilon -- an epsilon would let one well-formed sibling
    # prior dominate the renormalized set entirely, silently discarding a
    # hypothesis the model DID propose just because it forgot a prior.
    'INVALID_PRIOR_FALLBACK_WEIGHT': 1,
    'SURFACE_MAX_RATIONALES': 3,      # cap on evidence rationales shown per surfaced hypothesis
    'SURFACE_SNIPPET_CHARS': 120,     # cap on cluster representative-text snippet length
}

# Days-per-month approximation used for retention-window math. Kept as a
# named constant (rather than inlined 30*24*3600*1000) so purge_expired's
# month math stays consistent with STALE_AFTER_MS above.
MS_PER_MONTH = 30 * 24 * 3600 * 1000

SCHEMA_VERSION = 1


# =============================================================================
# Layer 2: Dedupe — tokenSet/jaccard fuzzy matching, ported from
# cue-light-dedupe.js. Only tokenSet/jaccard are ported (no ticketSet, no
# isDuplicate/record — those are Q-Light-specific and out of scope here).
# =============================================================================

# Verbatim 32-word stopword list from cue-light-dedupe.js:14-19.
STOPWORDS = {
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'has', 'not', 'but',
    'are', 'was', 'its', 'they', 'have', 'been', 'will', 'their', 'also',
    'into', 'than', 'then', 'when', 'our', 'can', 'all', 'any', 'one',
    'you', 'your', 'per', 'via',
}


def token_set(text):
    """Lowercase, strip non-alphanumeric to spaces, split, drop len<3 and
    stopwords, dedupe preserving order. Mirrors cue-light-dedupe.js tokenSet.
    """
    if not text or not isinstance(text, str):
        return []
    stripped = []
    for ch in text.lower():
        stripped.append(ch if (ch.isalnum() and ch.isascii()) or ch.isspace() else ' ')
    tokens = ''.join(stripped).split()
    seen = set()
    result = []
    for t in tokens:
        if len(t) >= 3 and t not in STOPWORDS and t not in seen:
            seen.add(t)
            result.append(t)
    return result


def jaccard(a_tokens, b_tokens):
    """Jaccard similarity between two token lists. 0 if either is empty."""
    if not a_tokens or not b_tokens:
        return 0
    set_a = set(a_tokens)
    set_b = set(b_tokens)
    intersection = len(set_a & set_b)
    union = len(set_a) + len(set_b) - intersection
    return 0 if union == 0 else intersection / union


# =============================================================================
# Layer 4a: Ledger I/O — atomic read/write of the JSON ledger file.
# =============================================================================

class LedgerError(Exception):
    """Raised when the ledger file exists but is unreadable/invalid. The
    ledger is NEVER overwritten in this case — the caller must fix or move
    the file by hand.
    """


def new_store(now):
    """A fresh, empty ledger/store dict."""
    return {
        'schemaVersion': SCHEMA_VERSION,
        'createdAt': now,
        'situations': {'seq': 0, 'items': {}},
    }


def save_ledger(path, store):
    """Atomically write `store` to `path` (tempfile in same dir + os.replace)."""
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.ledger-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(store, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_ledger_or_none(path):
    """Read-only commands: a missing ledger is not an error, just "empty"."""
    if not os.path.exists(path):
        return None
    return load_ledger(path)


def load_ledger(path):
    """Read `path`, raising LedgerError (never mutating the file) if it's
    corrupt JSON or an unrecognized schema version.
    """
    with open(path) as f:
        raw = f.read()
    try:
        store = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LedgerError(
            f'ledger file "{path}" contains invalid JSON ({e}); '
            'fix or move the file; hunch will NOT overwrite it'
        ) from e
    version = store.get('schemaVersion') if isinstance(store, dict) else None
    if version != SCHEMA_VERSION:
        raise LedgerError(
            f'ledger file "{path}" has schemaVersion {version!r}, expected {SCHEMA_VERSION}; '
            'fix or move the file; hunch will NOT overwrite it'
        )
    return store


def resolve_ledger_path(flag, env_value, cwd=None):
    """Precedence: --ledger flag > HUNCH_LEDGER env > default .hunch/ledger.json under cwd."""
    if flag:
        return flag
    if env_value:
        return env_value
    base = cwd if cwd is not None else os.getcwd()
    return os.path.join(base, '.hunch', 'ledger.json')


def load_or_create_ledger(path, now):
    """Mutating commands auto-create a missing ledger; read-only commands get
    an empty-but-valid in-memory store instead (never touching disk) — see
    the `is_read_only` branch in `_dispatch`, which never calls this.
    """
    existing = load_ledger_or_none(path)
    if existing is not None:
        return existing
    return new_store(now)


# =============================================================================
# Layer 3: Engine — pure functions on a plain dict store. TARS parity: all
# timestamps are injected via a `now` parameter (epoch ms), never computed
# here. All JSON wire keys stay camelCase — see module docstring.
# =============================================================================

def _ensure_root(store):
    """Ensure store['situations'] exists in the expected shape (lazy init,
    mutates `store` in place), returning the root dict.
    """
    situations = store.get('situations')
    if not isinstance(situations, dict):
        situations = {'seq': 0, 'items': {}}
        store['situations'] = situations
    if not isinstance(situations.get('seq'), int):
        situations['seq'] = 0
    if not isinstance(situations.get('items'), dict):
        situations['items'] = {}
    return situations


def _build_other_hypothesis():
    return {
        'id': TUNING['OTHER_ID'],
        'statement': 'The true explanation is not in the current candidate set',
        'prior': TUNING['OTHER_INITIAL_PRIOR'],
        'posterior': TUNING['OTHER_INITIAL_PRIOR'],
        'status': 'active',
        'predictedEvidence': [],
        'generation': 0,
    }


def _require_situation(store, sid):
    root = _ensure_root(store)
    sit = root['items'].get(sid)
    if not sit:
        known = list(root['items'].keys())
        raise ValueError(f'unknown situation id "{sid}" (known ids: {", ".join(known) or "none"})')
    return sit


def _resolve_reliability(source_type, reliability):
    if isinstance(reliability, (int, float)) and not isinstance(reliability, bool):
        return reliability
    st = source_type or 'inferred'
    defaults = TUNING['RELIABILITY_DEFAULTS']
    return defaults.get(st, defaults['inferred'])


def _top_active_hypothesis(situation):
    active = [h for h in situation['hypotheses'] if h['status'] == 'active']
    if not active:
        return None
    best = active[0]
    for h in active[1:]:
        if h['posterior'] > best['posterior']:
            best = h
    return best


def _clock_anchor(situation):
    return situation['lastObservationAt'] if situation.get('lastObservationAt') is not None else situation['createdAt']


CALIBRATION_BUCKETS = [
    {'range': '0-0.5', 'min': 0, 'max': 0.5},
    {'range': '0.5-0.7', 'min': 0.5, 'max': 0.7},
    {'range': '0.7-0.9', 'min': 0.7, 'max': 0.9},
    {'range': '0.9-1.0', 'min': 0.9, 'max': 1.0001},  # inclusive upper bound for confidence == 1
]


def _bucket_for(confidence):
    for b in CALIBRATION_BUCKETS:
        if b['min'] <= confidence < b['max']:
            return b['range']
    return CALIBRATION_BUCKETS[-1]['range']


def _is_finite_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _effective_likelihood(score, reliability, neutral=None):
    """Blend a raw evidence score with a neutral prior by source reliability.
    Non-finite score -> neutral; non-finite reliability -> 0 (unreliable).
    """
    if neutral is None:
        neutral = TUNING['NEUTRAL_LIKELIHOOD']
    rel = reliability if _is_finite_number(reliability) else 0
    s = score if _is_finite_number(score) else neutral
    if s < 0:
        s = 0
    elif s > 1:
        s = 1
    return rel * s + (1 - rel) * neutral


def _normalized_entropy(posteriors):
    """Shannon entropy of a probability distribution, normalized to [0,1] by
    dividing by log2(n). Fewer than 2 outcomes always yields 0.
    """
    values = list(posteriors.values())
    n = len(values)
    if n < 2:
        return 0
    h = 0.0
    for p in values:
        if p > 0:
            h -= p * math.log2(p)
    return h / math.log2(n)


def _truncate(text, max_chars):
    if not text:
        return ''
    return text[:max_chars] + '…' if len(text) > max_chars else text


def _best_jaccard_match(text, existing_observations):
    if not existing_observations:
        return None
    tokens = token_set(text)
    best = None
    best_score = -1
    for obs in existing_observations:
        score = jaccard(tokens, token_set(obs['text']))
        if score >= TUNING['CLUSTER_JACCARD_THRESHOLD'] and score > best_score:
            best = obs
            best_score = score
    return best


# --- Public engine API -------------------------------------------------

def open_situation(store, question=None, entity_ref=None, now=None):
    if not question or not isinstance(question, str) or not question.strip():
        raise ValueError('open_situation: "question" is required')
    root = _ensure_root(store)
    root['seq'] += 1
    sid = f"sit-{root['seq']}"
    situation = {
        'id': sid,
        'entityRef': entity_ref,
        'question': question,
        'status': 'open',
        'createdAt': now,
        'resolvedAt': None,
        'resolution': None,
        'hypotheses': [_build_other_hypothesis()],
        'observations': [],
        'posteriorHistory': [],
        'lastScoring': None,
        'lastSurfacedTopId': None,
        'calibration': [],
        'hSeq': 0,
        'obsSeq': 0,
        'clusterSeq': 0,
        'lastObservationAt': None,
    }
    root['items'][sid] = situation
    return situation


def get_situation(store, sid):
    root = _ensure_root(store)
    return root['items'].get(sid)


def list_situations(store, status=None):
    root = _ensure_root(store)
    all_sits = list(root['items'].values())
    if not status:
        return all_sits
    return [s for s in all_sits if s['status'] == status]


# Sentinel distinguishing "same_event_as not supplied at all" (-> Jaccard
# fallback clustering) from an explicit `same_event_as=None` /
# force_new_cluster (-> always a new cluster), mirroring the JS
# `sameEventAs === undefined` vs `sameEventAs === null` distinction.
_UNSET = object()


def add_observation(store, sid, text=None, provenance=None, source_type=None,
                     reliability=None, source_ref=None, same_event_as=_UNSET,
                     force_new_cluster=False, now=None):
    situation = _require_situation(store, sid)
    if not text or not isinstance(text, str) or not text.strip():
        raise ValueError('add_observation: "text" is required')
    if situation['status'] != 'open':
        raise ValueError(f'add_observation: situation "{sid}" is not open (status: {situation["status"]})')

    cluster_id = None
    cluster_method = None

    if force_new_cluster or same_event_as is None:
        # Explicit override: always a new cluster, even for identical text.
        situation['clusterSeq'] += 1
        cluster_id = f"c-{situation['clusterSeq']}"
        cluster_method = 'new'
    elif same_event_as is not _UNSET:
        target = next((o for o in situation['observations'] if o['id'] == same_event_as), None)
        if not target:
            known = [o['id'] for o in situation['observations']]
            raise ValueError(
                f'add_observation: unknown sameEventAs observation id "{same_event_as}" '
                f'(known ids: {", ".join(known) or "none"})'
            )
        cluster_id = target['clusterId']
        cluster_method = 'model'
    else:
        match = _best_jaccard_match(text, situation['observations'])
        if match:
            cluster_id = match['clusterId']
            cluster_method = 'jaccard'
        else:
            situation['clusterSeq'] += 1
            cluster_id = f"c-{situation['clusterSeq']}"
            cluster_method = 'new'

    situation['obsSeq'] += 1
    observation_id = f"obs-{situation['obsSeq']}"

    prov = provenance or {}
    resolved_source_type = source_type or prov.get('sourceType') or 'inferred'
    resolved_reliability = reliability if reliability is not None else prov.get('reliability')
    resolved_reliability = _resolve_reliability(resolved_source_type, resolved_reliability)
    resolved_source_ref = source_ref if source_ref is not None else prov.get('sourceRef')

    observation = {
        'id': observation_id,
        'text': text,
        'timestamp': now,
        'provenance': {
            'sourceType': resolved_source_type,
            'reliability': resolved_reliability,
            'sourceRef': resolved_source_ref,
        },
        'clusterId': cluster_id,
        'clusterMethod': cluster_method,
    }
    situation['observations'].append(observation)
    situation['lastObservationAt'] = now

    return {'observationId': observation_id, 'clusterId': cluster_id, 'clusterMethod': cluster_method}


def get_clusters(store, sid):
    situation = _require_situation(store, sid)
    by_cluster = {}
    order = []
    for obs in situation['observations']:
        cid = obs['clusterId']
        if cid not in by_cluster:
            by_cluster[cid] = []
            order.append(cid)
        by_cluster[cid].append(obs)
    clusters = []
    for cid in order:
        members = by_cluster[cid]
        clusters.append({
            'id': cid,
            'memberIds': [m['id'] for m in members],
            'texts': [m['text'] for m in members],
            'representativeText': members[0]['text'],
            'reliability': max((m['provenance']['reliability'] for m in members), default=0),
        })
    return clusters


def apply_hypotheses(store, sid, hyps, now=None):
    situation = _require_situation(store, sid)
    if situation['status'] != 'open':
        raise ValueError(f'apply_hypotheses: situation "{sid}" is not open (status: {situation["status"]})')

    if not isinstance(hyps, list) or not (TUNING['MIN_HYPOTHESES'] <= len(hyps) <= TUNING['MAX_HYPOTHESES']):
        got = len(hyps) if isinstance(hyps, list) else type(hyps).__name__
        raise ValueError(
            f"apply_hypotheses: expected between {TUNING['MIN_HYPOTHESES']} and "
            f"{TUNING['MAX_HYPOTHESES']} hypotheses, got {got}"
        )

    for i, h in enumerate(hyps):
        statement = h.get('statement') if isinstance(h, dict) else None
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError('apply_hypotheses: each hypothesis requires a non-empty "statement"')
        evidence = h.get('predictedEvidence')
        is_valid_evidence = (
            isinstance(evidence, list) and len(evidence) > 0
            and all(isinstance(e, str) and e.strip() for e in evidence)
        )
        if not is_valid_evidence:
            snippet = _truncate(statement, TUNING['SURFACE_SNIPPET_CHARS'])
            raise ValueError(
                f'apply_hypotheses: hypothesis {i} ("{snippet}") requires "predictedEvidence" to be a '
                'non-empty array of non-empty strings (the discriminators this hypothesis predicts)'
            )

    raw_weights = []
    for h in hyps:
        prior = h.get('prior')
        if _is_finite_number(prior) and prior > 0:
            raw_weights.append(prior)
        else:
            raw_weights.append(TUNING['INVALID_PRIOR_FALLBACK_WEIGHT'])
    raw_sum = sum(raw_weights)
    target_sum = 1 - TUNING['OTHER_INITIAL_PRIOR']
    scaled_priors = [(w / raw_sum) * target_sum for w in raw_weights]

    existing_non_other = [h for h in situation['hypotheses'] if h['id'] != TUNING['OTHER_ID']]
    active_non_other = [h for h in existing_non_other if h['status'] == 'active']
    for h in active_non_other:
        h['status'] = 'demoted'

    max_generation = max((h.get('generation', 0) or 0 for h in existing_non_other), default=-1)
    new_generation = max_generation + 1 if existing_non_other else 0

    new_hyps = []
    for i, h in enumerate(hyps):
        situation['hSeq'] += 1
        new_hyps.append({
            'id': f"h-{situation['hSeq']}",
            'statement': h['statement'],
            'prior': scaled_priors[i],
            'posterior': scaled_priors[i],
            'status': 'active',
            'predictedEvidence': h['predictedEvidence'],
            'generation': new_generation,
        })

    situation['hypotheses'].extend(new_hyps)

    other = next((h for h in situation['hypotheses'] if h['id'] == TUNING['OTHER_ID']), None)
    if other:
        other['prior'] = TUNING['OTHER_INITIAL_PRIOR']
        other['posterior'] = TUNING['OTHER_INITIAL_PRIOR']
        other['status'] = 'active'

    return situation


def resolve(store, sid, resolution=None, now=None):
    situation = _require_situation(store, sid)
    top = _top_active_hypothesis(situation)
    claimed_top_id = top['id'] if top else None
    claimed_confidence = top['posterior'] if top else None

    is_other_resolution = isinstance(resolution, str) and resolution.startswith(f"{TUNING['OTHER_ID']}:")
    if is_other_resolution:
        correct = claimed_top_id == TUNING['OTHER_ID']
    else:
        correct = claimed_top_id == resolution

    situation['status'] = 'resolved'
    situation['resolvedAt'] = now
    situation['resolution'] = resolution
    situation['calibration'].append({
        'claimedTopId': claimed_top_id,
        'claimedConfidence': claimed_confidence,
        'correct': correct,
        'resolvedAt': now,
    })
    return situation


def mark_stale(store, now_ms):
    root = _ensure_root(store)
    transitioned = []
    for situation in root['items'].values():
        if situation['status'] != 'open':
            continue
        anchor = _clock_anchor(situation)
        if now_ms - anchor > TUNING['STALE_AFTER_MS']:
            situation['status'] = 'stale'
            transitioned.append(situation['id'])
    return transitioned


def purge_expired(store, now_ms, months=None):
    if months is None:
        months = TUNING['PURGE_AFTER_MONTHS']
    root = _ensure_root(store)
    window_ms = months * MS_PER_MONTH
    purged = []
    for situation in root['items'].values():
        if situation['status'] not in ('resolved', 'stale'):
            continue
        anchor = situation['resolvedAt'] if situation['status'] == 'resolved' else _clock_anchor(situation)
        if anchor is not None and now_ms - anchor > window_ms:
            purged.append(situation['id'])
    for sid in purged:
        del root['items'][sid]
    return purged


def remove_situation(store, sid):
    root = _ensure_root(store)
    situation = _require_situation(store, sid)
    del root['items'][sid]
    return situation


def calibration_summary(store):
    root = _ensure_root(store)
    entries = []
    for situation in root['items'].values():
        entries.extend(situation['calibration'])

    buckets = [{'range': b['range'], 'count': 0, 'correct': 0} for b in CALIBRATION_BUCKETS]

    if not entries:
        return {
            'count': 0,
            'meanClaimedConfidence': None,
            'accuracy': None,
            'buckets': [{'range': b['range'], 'count': 0, 'accuracy': None} for b in buckets],
        }

    confidence_sum = 0
    correct_count = 0
    for entry in entries:
        confidence = entry['claimedConfidence'] if entry['claimedConfidence'] is not None else 0
        confidence_sum += confidence
        if entry['correct']:
            correct_count += 1
        bucket = next(b for b in buckets if b['range'] == _bucket_for(confidence))
        bucket['count'] += 1
        if entry['correct']:
            bucket['correct'] += 1

    return {
        'count': len(entries),
        'meanClaimedConfidence': confidence_sum / len(entries),
        'accuracy': correct_count / len(entries),
        'buckets': [
            {'range': b['range'], 'count': b['count'],
             'accuracy': None if b['count'] == 0 else b['correct'] / b['count']}
            for b in buckets
        ],
    }


# =============================================================================
# Posterior math + matrix stats/warnings — ported from _indexMatrix,
# _computePosteriors, _computeMatrixStats, _buildMatrixWarnings
# (js/abduction-lib.js:250-489).
# =============================================================================

def _index_matrix(matrix, cluster_by_id, active_ids, all_hypothesis_ids=None):
    """Pre-index a model-supplied likelihood matrix by clusterId ->
    (hypothesisId -> likelihood), dropping rows/cells referencing unknown
    clusters/hypotheses. Duplicate clusterId rows / duplicate hypothesisId
    cells within a row: the LAST occurrence wins wholesale.

    `matrix` is only guaranteed to be a JSON-decoded value that passed
    `isinstance(matrix, list)` (see rescore's top-level guard) — a valid
    JSON array can still contain non-dict rows (`[1,2,3]`) or non-dict cells,
    and dict rows/cells can hold non-string ids. None of that is allowed to
    raise; it's tallied in `malformedCount` and surfaced as a warning
    instead, matching this engine's JS counterpart's tolerance for
    wrong-shaped-but-valid-JSON input.
    """
    if all_hypothesis_ids is None:
        all_hypothesis_ids = active_ids

    rows_by_cluster = {}
    unknown_cluster_ids = []
    unknown_hypothesis_ids = []
    inactive_hypothesis_ids = []
    duplicate_cluster_ids = []
    duplicate_hypothesis_cells = []
    seen_unknown_cluster = set()
    seen_unknown_hyp = set()
    seen_inactive_hyp = set()
    seen_dup_cluster = set()
    malformed_count = 0

    for row in (matrix or []):
        if not isinstance(row, dict):
            malformed_count += 1
            continue
        cluster_id = row.get('clusterId')
        if cluster_id not in cluster_by_id:
            if cluster_id not in seen_unknown_cluster:
                seen_unknown_cluster.add(cluster_id)
                unknown_cluster_ids.append(cluster_id)
            continue
        if cluster_id in rows_by_cluster and cluster_id not in seen_dup_cluster:
            seen_dup_cluster.add(cluster_id)
            duplicate_cluster_ids.append(cluster_id)

        likelihoods = row.get('likelihoods')
        if not isinstance(likelihoods, list):
            likelihoods = []
        cells = {}
        for cell in likelihoods:
            if not isinstance(cell, dict):
                malformed_count += 1
                continue
            hid = cell.get('hypothesisId')
            if hid not in active_ids:
                if hid in all_hypothesis_ids:
                    if hid not in seen_inactive_hyp:
                        seen_inactive_hyp.add(hid)
                        inactive_hypothesis_ids.append(hid)
                else:
                    if hid not in seen_unknown_hyp:
                        seen_unknown_hyp.add(hid)
                        unknown_hypothesis_ids.append(hid)
                continue
            if hid in cells:
                duplicate_hypothesis_cells.append({'clusterId': cluster_id, 'hypothesisId': hid})
            cells[hid] = cell.get('likelihood')
        # Last row for a given clusterId wins wholesale.
        rows_by_cluster[cluster_id] = cells

    return {
        'rowsByCluster': rows_by_cluster,
        'unknownClusterIds': unknown_cluster_ids,
        'unknownHypothesisIds': unknown_hypothesis_ids,
        'inactiveHypothesisIds': inactive_hypothesis_ids,
        'duplicateClusterIds': duplicate_cluster_ids,
        'duplicateHypothesisCells': duplicate_hypothesis_cells,
        'malformedCount': malformed_count,
    }


def compute_posteriors(hypotheses, clusters=None, matrix=None):
    """Full re-score of active hypotheses from priors. Pure, order-invariant.
    Returns {'posteriors': {id: p}, 'entropy': float}.
    """
    clusters = clusters or []
    matrix = matrix or []
    active = [h for h in hypotheses if h['status'] == 'active']
    active_ids = {h['id'] for h in active}
    cluster_by_id = {c['id']: c for c in clusters}

    indexed = _index_matrix(matrix, cluster_by_id, active_ids)
    rows_by_cluster = indexed['rowsByCluster']

    def prior_of(h):
        p = h.get('prior')
        return p if (_is_finite_number(p) and p > 0) else 0

    raw = {}
    raw_sum = 0
    for h in active:
        product = 1
        for cluster in clusters:
            row = rows_by_cluster.get(cluster['id'])
            score = row.get(h['id']) if row is not None and h['id'] in row else None
            product *= _effective_likelihood(score, cluster['reliability'])
        prior = prior_of(h)
        raw[h['id']] = prior * product
        raw_sum += raw[h['id']]

    if raw_sum > TUNING['POSTERIOR_ZERO_EPSILON']:
        posteriors = {h['id']: raw[h['id']] / raw_sum for h in active}
    else:
        prior_sum = sum(prior_of(h) for h in active)
        if prior_sum > TUNING['POSTERIOR_ZERO_EPSILON']:
            posteriors = {h['id']: prior_of(h) / prior_sum for h in active}
        else:
            uniform = 1 / len(active) if active else 0
            posteriors = {h['id']: uniform for h in active}

    # OTHER floor: epistemic humility never dies below OTHER_FLOOR.
    other_id = TUNING['OTHER_ID']
    other_posterior = posteriors.get(other_id)
    if isinstance(other_posterior, (int, float)) and other_posterior < TUNING['OTHER_FLOOR']:
        complement_target = 1 - TUNING['OTHER_FLOOR']
        complement_current = 1 - other_posterior
        scale = (complement_target / complement_current) if complement_current > TUNING['POSTERIOR_ZERO_EPSILON'] else 0
        for hid in list(posteriors.keys()):
            if hid == other_id:
                continue
            posteriors[hid] *= scale
        posteriors[other_id] = TUNING['OTHER_FLOOR']

    return {'posteriors': posteriors, 'entropy': _normalized_entropy(posteriors)}


def compute_matrix_stats(matrix, clusters, active_hypotheses, all_hypotheses=None):
    """How much of a rescore's matrix actually landed: matched cells (from
    the FINAL deduped matrix — shares _index_matrix with compute_posteriors
    so this can never overcount what the math actually consumed) and which
    ids are unknown/inactive/duplicated/out-of-range.
    """
    if all_hypotheses is None:
        all_hypotheses = active_hypotheses
    cluster_by_id = {c['id']: c for c in clusters}
    active_ids = {h['id'] for h in active_hypotheses}
    all_hypothesis_ids = {h['id'] for h in all_hypotheses}
    total_cells = len(clusters) * len(active_hypotheses)

    indexed = _index_matrix(matrix, cluster_by_id, active_ids, all_hypothesis_ids)

    matched_cells = 0
    out_of_range = []
    for cluster_id, cells in indexed['rowsByCluster'].items():
        for hypothesis_id, likelihood in cells.items():
            if _is_finite_number(likelihood):
                matched_cells += 1
                if likelihood < 0 or likelihood > 1:
                    out_of_range.append({'clusterId': cluster_id, 'hypothesisId': hypothesis_id, 'likelihood': likelihood})

    return {
        'totalCells': total_cells,
        'matchedCells': matched_cells,
        'unknownClusterIds': indexed['unknownClusterIds'],
        'unknownHypothesisIds': indexed['unknownHypothesisIds'],
        'inactiveHypothesisIds': indexed['inactiveHypothesisIds'],
        'duplicateClusterIds': indexed['duplicateClusterIds'],
        'duplicateHypothesisCells': indexed['duplicateHypothesisCells'],
        'outOfRangeLikelihoods': out_of_range,
        'malformedCount': indexed['malformedCount'],
    }


def build_matrix_warnings(stats):
    """Human-readable warnings a model can act on without re-reading docs —
    naming unknown/inactive/duplicate ids inline enables one-shot self-correction.

    Ids named here come straight from model-supplied JSON and are not
    guaranteed to be strings (e.g. a numeric or null clusterId) — every join
    below goes through str() so a malformed id can never crash this
    function; it just gets printed as whatever it is.
    """
    unknown_parts = []
    if stats['unknownClusterIds']:
        unknown_parts.append(f"unknown cluster ids: {', '.join(str(x) for x in stats['unknownClusterIds'])}")
    if stats['unknownHypothesisIds']:
        unknown_parts.append(f"unknown hypothesis ids: {', '.join(str(x) for x in stats['unknownHypothesisIds'])}")
    if stats['inactiveHypothesisIds']:
        unknown_parts.append(
            f"inactive (demoted) hypothesis ids: {', '.join(str(x) for x in stats['inactiveHypothesisIds'])}")
    unknown_suffix = f" ({'; '.join(unknown_parts)})" if unknown_parts else ''

    warnings = []
    if stats.get('malformedCount'):
        warnings.append(
            f"{stats['malformedCount']} malformed matrix rows/cells ignored — each row must be "
            '{clusterId, likelihoods:[{hypothesisId, likelihood}]}'
        )
    if stats['matchedCells'] == 0:
        warnings.append(
            'no matrix cells matched — posteriors equal priors; matrix rows must use cluster ids '
            f'(c-1…) from `clusters`, value key must be `likelihood`{unknown_suffix}'
        )
    elif stats['matchedCells'] < stats['totalCells']:
        missing = stats['totalCells'] - stats['matchedCells']
        warnings.append(f"{missing} of {stats['totalCells']} cells missing → neutral-filled 0.5{unknown_suffix}")

    if stats['duplicateClusterIds']:
        warnings.append(
            f"duplicate matrix rows for cluster id(s): {', '.join(stats['duplicateClusterIds'])} — "
            'only the last row for each was used, earlier row(s) dropped'
        )
    if stats['duplicateHypothesisCells']:
        named = '; '.join(f"{d['hypothesisId']} in cluster {d['clusterId']}" for d in stats['duplicateHypothesisCells'])
        warnings.append(f'duplicate hypothesis cell(s) within a row: {named} — only the last cell for each was used, earlier one(s) dropped')
    if stats['outOfRangeLikelihoods']:
        named = ', '.join(f"{o['clusterId']}/{o['hypothesisId']}={o['likelihood']}" for o in stats['outOfRangeLikelihoods'])
        warnings.append(f'likelihood values outside [0,1] were clamped: {named}')

    return warnings


def normalized_entropy(posteriors):
    return _normalized_entropy(posteriors)


# =============================================================================
# Surfacing verdicts + rescore + surface text — ported from _decideSurfacing,
# rescore, _unobservedDiscriminators, _buildSurfaceText
# (js/abduction-lib.js:491-636, 1213-1312).
# =============================================================================

def decide_surfacing(posteriors, hypotheses, last_surfaced_top_id):
    """Precedence (first match wins): confused > flip > peaked > flat > none.
    `flip` requires a real lead over the runner-up (lead > SURFACE_LEAD_MIN)
    to avoid a false "changed my mind" on near-uniform posterior jitter.
    """
    active = [h for h in hypotheses if h['status'] == 'active' and h['id'] in posteriors]

    top_id = None
    top_posterior = float('-inf')
    for h in active:
        p = posteriors[h['id']]
        if p > top_posterior:
            top_posterior = p
            top_id = h['id']

    runner_up_posterior = 0
    for h in active:
        if h['id'] == top_id:
            continue
        p = posteriors[h['id']]
        if p > runner_up_posterior:
            runner_up_posterior = p

    lead = (top_posterior - runner_up_posterior) if len(active) >= 2 else 0
    entropy = _normalized_entropy(posteriors)
    other_posterior = posteriors.get(TUNING['OTHER_ID'], 0) or 0

    if other_posterior > TUNING['REGEN_OTHER_THRESHOLD']:
        verdict = 'confused'
    elif last_surfaced_top_id is not None and top_id != last_surfaced_top_id and lead > TUNING['SURFACE_LEAD_MIN']:
        verdict = 'flip'
    elif top_posterior > TUNING['SURFACE_TOP_MIN'] and lead > TUNING['SURFACE_LEAD_MIN']:
        verdict = 'peaked'
    elif entropy > TUNING['FLAT_ENTROPY_MIN']:
        verdict = 'flat'
    else:
        verdict = 'none'

    return {'verdict': verdict, 'topId': top_id, 'lead': lead, 'entropy': entropy}


def _unobserved_discriminators(situation, hypothesis):
    """Which of a hypothesis's predictedEvidence items have NOT yet been
    matched (Jaccard >= CLUSTER_JACCARD_THRESHOLD) by any observation text.
    """
    predicted = hypothesis.get('predictedEvidence') or []
    result = []
    for item in predicted:
        item_tokens = token_set(item)
        matched = any(
            jaccard(item_tokens, token_set(obs['text'])) >= TUNING['CLUSTER_JACCARD_THRESHOLD']
            for obs in situation['observations']
        )
        if not matched:
            result.append(item)
    return result


# Verbatim call-signature quoted back at the caller whenever rescore's matrix
# argument is malformed -- naming the exact expected shape lets a model
# self-correct in one step.
RESCORE_SIGNATURE = (
    'rescore(store, id, matrix, opts) — matrix must be an array of '
    '{clusterId, likelihoods:[{hypothesisId, likelihood, rationale}]}'
)


def rescore(store, sid, matrix, now=None, trigger='manual'):
    situation = _require_situation(store, sid)
    if not isinstance(matrix, list):
        raise ValueError(f'rescore: {RESCORE_SIGNATURE}')
    if situation['status'] != 'open':
        raise ValueError(f'rescore: situation "{sid}" is not open (status: {situation["status"]})')

    active_non_other = [h for h in situation['hypotheses'] if h['status'] == 'active' and h['id'] != TUNING['OTHER_ID']]
    if not active_non_other:
        raise ValueError(f'rescore: situation "{sid}" has no active hypotheses — call `hypotheses` first')
    if not situation['observations']:
        raise ValueError(f'rescore: situation "{sid}" has no observations to score against')

    clusters = get_clusters(store, sid)
    active_hyps = [h for h in situation['hypotheses'] if h['status'] == 'active']
    computed = compute_posteriors(hypotheses=active_hyps, clusters=clusters, matrix=matrix)
    posteriors = computed['posteriors']
    matrix_stats = compute_matrix_stats(matrix, clusters, active_hyps, situation['hypotheses'])
    warnings = build_matrix_warnings(matrix_stats)

    for h in active_hyps:
        if h['id'] in posteriors:
            h['posterior'] = posteriors[h['id']]

    entropy_for_audit = _normalized_entropy(posteriors)
    situation['lastScoring'] = {'timestamp': now, 'matrix': matrix, 'entropy': entropy_for_audit}
    situation['posteriorHistory'].append({'timestamp': now, 'posteriors': posteriors, 'trigger': trigger})
    if len(situation['posteriorHistory']) > TUNING['POSTERIOR_HISTORY_CAP']:
        situation['posteriorHistory'] = situation['posteriorHistory'][-TUNING['POSTERIOR_HISTORY_CAP']:]

    decision = decide_surfacing(posteriors, active_hyps, situation['lastSurfacedTopId'])
    surfaced = decision['verdict'] in ('peaked', 'flip', 'confused')
    if surfaced:
        situation['lastSurfacedTopId'] = decision['topId']

    top_hyp = next((h for h in active_hyps if h['id'] == decision['topId']), None)
    discriminators = _unobserved_discriminators(situation, top_hyp) if top_hyp else []
    regeneration_suggested = (posteriors.get(TUNING['OTHER_ID'], 0) or 0) > TUNING['REGEN_OTHER_THRESHOLD']
    surface_text = build_surface_text(store, sid) if decision['verdict'] != 'none' else None

    return {
        'verdict': decision['verdict'],
        'topId': decision['topId'],
        'topStatement': top_hyp['statement'] if top_hyp else None,
        'lead': decision['lead'],
        'entropy': decision['entropy'],
        'posteriors': posteriors,
        'discriminators': discriminators,
        'regenerationSuggested': regeneration_suggested,
        'surfaceText': surface_text,
        'matrixStats': matrix_stats,
        'warnings': warnings,
    }


def build_surface_text(store, sid):
    """Always-hypothesis-framed, human-readable surfacing text: question,
    full distribution, evidence for the top hypothesis, discriminator
    ("watch for") lines for the top and runner-up.
    """
    situation = _require_situation(store, sid)
    active = [h for h in situation['hypotheses'] if h['status'] == 'active']
    sorted_active = sorted(active, key=lambda h: h['posterior'], reverse=True)

    lines = [situation['question'], '']
    for h in sorted_active:
        lines.append(f"p={h['posterior']:.2f} — {h['statement']}")

    if not situation['lastScoring']:
        lines.append('')
        lines.append('(no evidence scored yet)')
        return '\n'.join(lines)

    top = sorted_active[0] if sorted_active else None
    runner_up = sorted_active[1] if len(sorted_active) > 1 else None

    if top:
        clusters = get_clusters(store, sid)
        cluster_by_id = {c['id']: c for c in clusters}
        rationale_rows = []
        for row in (situation['lastScoring']['matrix'] or []):
            # Persisted verbatim from whatever rescore was called with —
            # may contain malformed rows/cells that _index_matrix already
            # tolerates elsewhere; tolerate them here too rather than crash.
            if not isinstance(row, dict):
                continue
            cell = next(
                (c for c in (row.get('likelihoods') or [])
                 if isinstance(c, dict) and c.get('hypothesisId') == top['id']),
                None)
            if not cell:
                continue
            rationale_rows.append({'likelihood': cell.get('likelihood'), 'rationale': cell.get('rationale'), 'clusterId': row.get('clusterId')})
        rationale_rows.sort(key=lambda r: (r['likelihood'] if _is_finite_number(r['likelihood']) else float('-inf')), reverse=True)
        top_rationales = rationale_rows[:TUNING['SURFACE_MAX_RATIONALES']]

        if top_rationales:
            lines.append('')
            lines.append('Evidence:')
            for r in top_rationales:
                cluster = cluster_by_id.get(r['clusterId'])
                snippet = _truncate(cluster['representativeText'], TUNING['SURFACE_SNIPPET_CHARS']) if cluster else ''
                rationale_prefix = f"{r['rationale']} " if r['rationale'] else ''
                lines.append(f"- {rationale_prefix}({snippet})".strip())

        top_unobserved = _unobserved_discriminators(situation, top)
        lines.append('')
        lines.append(f"Watch for: {'; '.join(top_unobserved) if top_unobserved else 'nothing new'}")

        if runner_up:
            runner_up_unobserved = _unobserved_discriminators(situation, runner_up)
            expect = '; '.join(runner_up_unobserved) if runner_up_unobserved else 'no distinguishing evidence yet'
            lines.append(f"If instead {runner_up['statement']}: expect {expect}")

    return '\n'.join(lines)


def _round2(n):
    return None if n is None else round(n * 100) / 100


# Sort precedence for the Situations list view — open situations need
# attention first, then stale, then resolved. Unrecognized statuses sort last.
_SITUATION_LIST_STATUS_ORDER = {'open': 0, 'stale': 1, 'resolved': 2}
_SITUATION_LIST_UNKNOWN_STATUS_ORDER = 3


def _situation_verdict(situation):
    """Current surfacing verdict, recomputed from live posteriors (not
    persisted) — same inputs `rescore` used at scoring time.
    """
    if not situation['lastScoring']:
        return None
    active = [h for h in situation['hypotheses'] if h['status'] == 'active']
    posteriors = {h['id']: h['posterior'] for h in active}
    return decide_surfacing(posteriors, active, situation['lastSurfacedTopId'])['verdict']


def build_list_view(store):
    """Situations list view: one summary row per situation."""
    rows = []
    for situation in list_situations(store):
        top = _top_active_hypothesis(situation)
        rows.append({
            'id': situation['id'],
            'question': situation['question'],
            'status': situation['status'],
            'topStatement': top['statement'] if top else None,
            'topPosterior': _round2(top['posterior']) if top else None,
            'verdict': _situation_verdict(situation),
            'observationCount': len(situation['observations']),
            'hypothesisCount': len([h for h in situation['hypotheses'] if h['status'] == 'active' and h['id'] != TUNING['OTHER_ID']]),
            'lastActivityAt': _clock_anchor(situation),
            'entropy': situation['lastScoring']['entropy'] if situation['lastScoring'] else None,
        })

    def sort_key(row):
        order = _SITUATION_LIST_STATUS_ORDER.get(row['status'], _SITUATION_LIST_UNKNOWN_STATUS_ORDER)
        activity = row['lastActivityAt'] if row['lastActivityAt'] is not None else 0
        return (order, -activity)

    rows.sort(key=sort_key)
    return rows


def build_detail_view(store, sid):
    """Situation detail view: full hypothesis distribution (incl. OTHER and
    demoted), observations newest-first, cluster summary, compact
    posterior-history trail, last scoring's warnings, calibration, surface text.
    """
    situation = get_situation(store, sid)
    if not situation:
        return None

    hypotheses = sorted(situation['hypotheses'], key=lambda h: h['posterior'], reverse=True)
    hypotheses_view = [{
        'id': h['id'], 'statement': h['statement'], 'posterior': h['posterior'],
        'prior': h['prior'], 'status': h['status'], 'generation': h['generation'],
        'predictedEvidence': h['predictedEvidence'],
    } for h in hypotheses]

    observations = sorted(situation['observations'], key=lambda o: (o['timestamp'] if o['timestamp'] is not None else 0), reverse=True)
    observations_view = [{
        'id': o['id'], 'text': o['text'], 'timestamp': o['timestamp'],
        'sourceType': (o.get('provenance') or {}).get('sourceType'),
        'reliability': (o.get('provenance') or {}).get('reliability'),
        'clusterId': o['clusterId'], 'clusterMethod': o['clusterMethod'],
    } for o in observations]

    raw_clusters = get_clusters(store, sid)
    clusters_view = [{
        'id': c['id'], 'size': len(c['memberIds']), 'representativeText': c['representativeText'],
        'reliability': c['reliability'],
    } for c in raw_clusters]

    posterior_history = []
    for entry in situation['posteriorHistory']:
        top3 = sorted(entry['posteriors'].items(), key=lambda kv: kv[1], reverse=True)[:3]
        posterior_history.append({
            'timestamp': entry['timestamp'], 'trigger': entry['trigger'],
            'top3': [{'id': hid, 'p': _round2(p)} for hid, p in top3],
        })

    last_scoring = None
    if situation['lastScoring']:
        active_hyps = [h for h in situation['hypotheses'] if h['status'] == 'active']
        stats = compute_matrix_stats(situation['lastScoring']['matrix'], raw_clusters, active_hyps, situation['hypotheses'])
        last_scoring = {'timestamp': situation['lastScoring']['timestamp'], 'warnings': build_matrix_warnings(stats)}

    return {
        'id': situation['id'],
        'question': situation['question'],
        'status': situation['status'],
        'createdAt': situation['createdAt'],
        'hypotheses': hypotheses_view,
        'observations': observations_view,
        'clusters': clusters_view,
        'posteriorHistory': posterior_history,
        'lastScoring': last_scoring,
        'surfaceText': build_surface_text(store, sid),
        'calibration': situation['calibration'],
    }


# =============================================================================
# Layer 4b: CLI — argparse subcommands, JSON in/out always.
# =============================================================================

def _now_ms():
    return int(time.time() * 1000)


def _print_json(data):
    print(json.dumps(data, indent=2))


def _error(message):
    _print_json({'error': message})
    return 1


def _read_payload(json_flag):
    """Read a JSON payload from --json if given, else from stdin."""
    if json_flag is not None:
        return json.loads(json_flag)
    raw = sys.stdin.read()
    return json.loads(raw)


def _finite_float(raw):
    """argparse type= for flags that feed straight into probability math —
    rejects 'nan'/'inf'/'-inf' (which Python's float() otherwise happily
    parses) so a typo can't silently poison a posterior calculation.
    """
    value = float(raw)
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f'expected a finite number, got {raw!r}')
    return value


def _build_parser():
    parser = argparse.ArgumentParser(prog='hunch', description='Bayesian hypothesis tracking for explanation questions.')
    parser.add_argument('--ledger', default=None, help='Path to the ledger JSON file (overrides HUNCH_LEDGER and the default).')
    parser.add_argument('--now', type=int, default=None, help='Epoch ms to use as "now" (for deterministic testing).')
    sub = parser.add_subparsers(dest='command', required=True)

    p_open = sub.add_parser('open', help='Open a new situation.')
    p_open.add_argument('--question', required=True)
    p_open.add_argument('--entity-ref', default=None)

    p_hyps = sub.add_parser('hypotheses', help='Set (and regenerate) a situation\'s hypotheses.')
    p_hyps.add_argument('situation_id')
    p_hyps.add_argument('--json', default=None, help='Hypotheses array as a JSON string (else read from stdin).')

    p_observe = sub.add_parser('observe', help='Add an observation to a situation.')
    p_observe.add_argument('situation_id')
    p_observe.add_argument('--text', required=True)
    p_observe.add_argument('--source-type', default=None)
    p_observe.add_argument('--reliability', type=_finite_float, default=None)
    p_observe.add_argument('--source-ref', default=None)
    cluster_group = p_observe.add_mutually_exclusive_group()
    cluster_group.add_argument('--same-event-as', default=None)
    cluster_group.add_argument('--new-cluster', action='store_true')

    p_clusters = sub.add_parser('clusters', help='List a situation\'s observation clusters.')
    p_clusters.add_argument('situation_id')

    p_rescore = sub.add_parser('rescore', help='Re-score a situation\'s hypotheses against a likelihood matrix.')
    p_rescore.add_argument('situation_id')
    p_rescore.add_argument('--json', default=None, help='Matrix as a JSON string (else read from stdin).')
    p_rescore.add_argument('--trigger', default='manual')

    p_get = sub.add_parser('get', help='Get a situation\'s full detail view.')
    p_get.add_argument('situation_id')

    p_list = sub.add_parser('list', help='List situations (summary rows).')
    p_list.add_argument('--status', default=None, choices=['open', 'stale', 'resolved'])

    p_surface = sub.add_parser('surface', help='Get a situation\'s current surface text.')
    p_surface.add_argument('situation_id')

    p_resolve = sub.add_parser('resolve', help='Resolve a situation, recording calibration.')
    p_resolve.add_argument('situation_id')
    p_resolve.add_argument('--resolution', required=True)

    p_remove = sub.add_parser('remove', help='Permanently remove a situation.')
    p_remove.add_argument('situation_id')

    sub.add_parser('calibration', help='Aggregate calibration stats across all situations.')

    p_purge = sub.add_parser('purge', help='Mark stale situations and purge expired resolved/stale ones.')
    p_purge.add_argument('--months', type=int, default=None)

    p_demo = sub.add_parser('demo', help='Run an end-to-end Cluedo demo on a temp ledger.')
    p_demo.add_argument('--keep', action='store_true', help='Keep the temp demo ledger instead of cleaning it up.')

    return parser


def _resolve_path_and_now(args):
    path = resolve_ledger_path(flag=args.ledger, env_value=os.environ.get('HUNCH_LEDGER'))
    now = args.now if args.now is not None else _now_ms()
    return path, now


def _dispatch(args):
    if args.command == 'demo':
        result = _cmd_demo(args)
        _print_json(result)
        return 0 if result.get('ok') else 1

    path, now = _resolve_path_and_now(args)

    read_only_commands = {'clusters', 'get', 'list', 'surface', 'calibration'}
    is_read_only = args.command in read_only_commands

    try:
        store = load_ledger_or_none(path) if is_read_only else load_or_create_ledger(path, now)
    except LedgerError as e:
        return _error(str(e))
    if store is None:
        store = new_store(now)

    try:
        result = _run_command(args, store, now)
    except (ValueError, json.JSONDecodeError) as e:
        return _error(str(e))

    if not is_read_only:
        save_ledger(path, store)

    _print_json(result)
    return 0


def _run_command(args, store, now):
    cmd = args.command

    if cmd == 'open':
        return open_situation(store, question=args.question, entity_ref=args.entity_ref, now=now)

    if cmd == 'hypotheses':
        payload = _read_payload(args.json)
        situation = apply_hypotheses(store, args.situation_id, payload, now=now)
        return build_detail_view(store, args.situation_id) or situation

    if cmd == 'observe':
        if args.new_cluster:
            return add_observation(store, args.situation_id, text=args.text,
                                    source_type=args.source_type, reliability=args.reliability,
                                    source_ref=args.source_ref, force_new_cluster=True, now=now)
        if args.same_event_as is not None:
            return add_observation(store, args.situation_id, text=args.text,
                                    source_type=args.source_type, reliability=args.reliability,
                                    source_ref=args.source_ref, same_event_as=args.same_event_as, now=now)
        return add_observation(store, args.situation_id, text=args.text,
                                source_type=args.source_type, reliability=args.reliability,
                                source_ref=args.source_ref, now=now)

    if cmd == 'clusters':
        return get_clusters(store, args.situation_id)

    if cmd == 'rescore':
        matrix = _read_payload(args.json)
        return rescore(store, args.situation_id, matrix, now=now, trigger=args.trigger)

    if cmd == 'get':
        view = build_detail_view(store, args.situation_id)
        if view is None:
            _require_situation(store, args.situation_id)  # raises with known-ids message
        return view

    if cmd == 'list':
        rows = build_list_view(store)
        if args.status:
            rows = [r for r in rows if r['status'] == args.status]
        return rows

    if cmd == 'surface':
        _require_situation(store, args.situation_id)
        return {'surfaceText': build_surface_text(store, args.situation_id)}

    if cmd == 'resolve':
        return resolve(store, args.situation_id, resolution=args.resolution, now=now)

    if cmd == 'remove':
        return remove_situation(store, args.situation_id)

    if cmd == 'calibration':
        return calibration_summary(store)

    if cmd == 'purge':
        marked_stale = mark_stale(store, now)
        purged = purge_expired(store, now, months=args.months)
        return {'markedStale': marked_stale, 'purged': purged}

    raise ValueError(f'unknown command "{cmd}"')


# =============================================================================
# Demo — end-to-end Cluedo walkthrough on a temp ledger.
# =============================================================================

def _cmd_demo(args):
    import shutil

    demo_dir = tempfile.mkdtemp(prefix='hunch-demo-')
    ledger_path = os.path.join(demo_dir, 'ledger.json')
    steps = []
    t = 0

    def tick(delta=1000):
        nonlocal t
        t += delta
        return t

    try:
        store = new_store(now=tick())

        sit = open_situation(store, question='Who killed Dr. Black?', now=t)
        steps.append({'step': 'open', 'situationId': sit['id']})

        # Priors deliberately do not sum to 0.85 pre-renormalization, to
        # prove apply_hypotheses renormalizes them.
        hyps = [
            {'statement': 'Colonel Mustard did it in the library with the candlestick',
             'prior': 0.5, 'predictedEvidence': ['Mustard seen near library', 'candlestick moved']},
            {'statement': 'Professor Plum did it in the study with the revolver',
             'prior': 0.5, 'predictedEvidence': ['Plum seen near study', 'revolver fired']},
            {'statement': 'Miss Scarlett did it in the lounge with the rope',
             'prior': 0.5, 'predictedEvidence': ['Scarlett seen near lounge', 'rope missing']},
            {'statement': 'Mrs. Peacock did it in the kitchen with the lead pipe',
             'prior': 0.5, 'predictedEvidence': ['Peacock seen near kitchen', 'lead pipe missing']},
        ]
        apply_hypotheses(store, sit['id'], hyps, now=tick())
        steps.append({'step': 'hypotheses', 'count': len(hyps)})

        s = get_situation(store, sit['id'])
        h_ids = [h['id'] for h in s['hypotheses'] if h['id'] != TUNING['OTHER_ID']]

        o1 = add_observation(store, sit['id'], text='Someone heard a scream near the library',
                              provenance={'sourceType': 'secondhand'}, now=tick())
        o2 = add_observation(store, sit['id'], text='A rumor says Mustard was seen with a candlestick',
                              provenance={'sourceType': 'rumor'}, now=tick())
        o3 = add_observation(store, sit['id'], text='Someone heard a scream close to the library',
                              same_event_as=o1['observationId'], provenance={'sourceType': 'secondhand'}, now=tick())
        steps.append({'step': 'observe', 'observations': [o1['observationId'], o2['observationId'], o3['observationId']]})

        clusters = get_clusters(store, sit['id'])
        ambiguous_matrix = [{'clusterId': c['id'], 'likelihoods': [
            {'hypothesisId': hid, 'likelihood': 0.5, 'rationale': 'inconclusive'} for hid in h_ids + [TUNING['OTHER_ID']]
        ]} for c in clusters]
        r_ambiguous = rescore(store, sit['id'], ambiguous_matrix, now=tick(), trigger='ambiguous')
        steps.append({'step': 'rescore-ambiguous', 'verdict': r_ambiguous['verdict']})
        assert r_ambiguous['verdict'] in ('flat', 'none'), f"expected flat/none, got {r_ambiguous['verdict']}"

        o4 = add_observation(store, sit['id'], text='Mustard was seen holding the bloody candlestick firsthand',
                              provenance={'sourceType': 'firsthand'}, now=tick())
        steps.append({'step': 'observe-discriminating', 'observationId': o4['observationId']})

        clusters2 = get_clusters(store, sit['id'])
        decisive_matrix = []
        for c in clusters2:
            if o4['clusterId'] == c['id']:
                decisive_matrix.append({'clusterId': c['id'], 'likelihoods': [
                    {'hypothesisId': hid, 'likelihood': 0.95 if hid == h_ids[0] else 0.02, 'rationale': 'decisive'}
                    for hid in h_ids + [TUNING['OTHER_ID']]
                ]})
            else:
                decisive_matrix.append({'clusterId': c['id'], 'likelihoods': [
                    {'hypothesisId': hid, 'likelihood': 0.5, 'rationale': 'neutral'} for hid in h_ids + [TUNING['OTHER_ID']]
                ]})
        r_decisive = rescore(store, sit['id'], decisive_matrix, now=tick(), trigger='decisive')
        steps.append({'step': 'rescore-decisive', 'verdict': r_decisive['verdict'], 'topId': r_decisive['topId']})
        assert r_decisive['verdict'] == 'peaked', f"expected peaked, got {r_decisive['verdict']}"
        assert r_decisive['posteriors'][TUNING['OTHER_ID']] == TUNING['OTHER_FLOOR'], 'expected OTHER floored'

        surface_text = build_surface_text(store, sit['id'])
        steps.append({'step': 'surface', 'textPreview': surface_text.splitlines()[0]})

        resolve(store, sit['id'], resolution=h_ids[0], now=tick())
        steps.append({'step': 'resolve', 'resolution': h_ids[0]})

        summary = calibration_summary(store)
        steps.append({'step': 'calibration', 'count': summary['count'], 'accuracy': summary['accuracy']})
        assert summary['count'] == 1 and summary['accuracy'] == 1.0, 'expected 1 correct calibration entry'

        save_ledger(ledger_path, store)
        return {'ok': True, 'steps': steps, 'ledgerPath': ledger_path if args.keep else None}
    finally:
        if not args.keep:
            shutil.rmtree(demo_dir, ignore_errors=True)


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code
    return _dispatch(args)


if __name__ == '__main__':
    sys.exit(main())
