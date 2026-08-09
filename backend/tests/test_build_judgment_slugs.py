"""Unit tests for ``scripts/build_judgment_slugs.py`` — the /judgments publisher.

Everything here runs on SYNTHETIC rows. The script's own DB reads are the part
that cannot be unit-tested cheaply (they page a 29k-row corpus), so the tests
target the pure decision functions, which is where the policy actually lives:

  * ``_feed_quotas``      — stage 0, the per-feed allocation and its redistribution
  * ``_DomainPolicy``     — the scale-free sector ceiling
  * ``load_usage_scores`` — the two-stage usage dampening
  * ``select_sample``     — end to end against a stubbed client

The load-bearing invariants asserted below, each of which has a matching WARNING
in the script's docstring:

  1. The default allocation reproduces the plan's table EXACTLY at --limit 10000.
  2. A feed whose quota exceeds its supply redistributes the shortfall instead of
     under-filling the run.
  3. The sector ceiling is PER FEED — a domain-less feed can never consume the
     وزارة العدل allocation's headroom.
  4. The ceiling is a RUNNING RATIO, so a bigger --limit is a SUPERSET of a
     smaller one. Break this and re-running with a larger limit churns instead of
     topping up.
  5. One demo account cannot buy a judgment a slot (the usage caps).
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# scripts/ is not a package; load the module by path.
_SPEC = importlib.util.spec_from_file_location(
    "build_judgment_slugs", _ROOT / "scripts" / "build_judgment_slugs.py"
)
bjs = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(bjs)

from shared.library.courts import (  # noqa: E402
    FEED_BOG,
    FEED_INSURANCE,
    FEED_MOJ,
    FEED_ZATCA,
)

# Live eligible supply per feed, measured 2026-08-08. The plan's §3.1 table is
# stated against these numbers, so the allocation test is only meaningful with
# them.
LIVE_SUPPLY = {
    FEED_MOJ: 19_766,
    FEED_ZATCA: 4_934,
    FEED_BOG: 4_501,
    FEED_INSURANCE: 224,
    bjs._FEED_OTHER: 0,
}

# One raw `cases.court` string per feed, copied from shared/library/courts.py.
COURT_MOJ = "التجارية"
COURT_MOJ_ALT = "العامة"
COURT_BOG = "ديوان المظالم — الدائرة التجارية"
COURT_ZATCA = "هيئة الزكاة والضريبة — اللجنة الابتدائية الأولى"
COURT_INSURANCE = "لجان الفصل في المنازعات والمخالفات التأمينية"


# ── stage 0: the per-feed allocation ───────────────────────────────────────
class TestFeedQuotas:
    def test_default_allocation_reproduces_the_plan_table_at_10000(self):
        """§3.1: تأمين all 224 · ديوان ~1000 · زكاة ~1000 · عدل the remainder."""
        q = bjs._feed_quotas(10_000, LIVE_SUPPLY, bjs._FEED_ALLOCATION)
        assert q[FEED_INSURANCE] == 224
        assert q[FEED_BOG] == 1_000
        assert q[FEED_ZATCA] == 1_000
        assert q[FEED_MOJ] == 7_776
        assert sum(q.values()) == 10_000

    def test_shortfall_redistributes_instead_of_under_filling(self):
        """لجان التأمين is asked for 300 and holds 224. The 76-row gap must land
        somewhere, or the run silently publishes 9,924 of 10,000."""
        q = bjs._feed_quotas(10_000, LIVE_SUPPLY, bjs._FEED_ALLOCATION)
        assert q[FEED_INSURANCE] == LIVE_SUPPLY[FEED_INSURANCE]  # clamped to supply
        assert sum(q.values()) == 10_000  # and the total is still whole
        # 0.03 * 10000 = 300 wanted, so the 76 it could not take went to `rest`.
        assert q[FEED_MOJ] == 10_000 - 1_000 - 1_000 - 224

    @pytest.mark.parametrize("limit", [1, 9, 100, 250, 1_000, 9_999, 10_000, 20_000])
    def test_quotas_always_sum_to_the_limit_or_the_whole_supply(self, limit):
        q = bjs._feed_quotas(limit, LIVE_SUPPLY, bjs._FEED_ALLOCATION)
        assert sum(q.values()) == min(limit, sum(LIVE_SUPPLY.values()))
        assert all(v >= 0 for v in q.values())
        assert all(q[f] <= LIVE_SUPPLY[f] for f in q)

    def test_a_limit_above_the_corpus_takes_everything_and_no_more(self):
        q = bjs._feed_quotas(999_999, LIVE_SUPPLY, bjs._FEED_ALLOCATION)
        assert q == {f: LIVE_SUPPLY[f] for f in q}

    def test_all_spec_takes_the_whole_feed(self):
        alloc = {**bjs._FEED_ALLOCATION, FEED_INSURANCE: bjs._ALLOC_ALL}
        q = bjs._feed_quotas(10_000, LIVE_SUPPLY, alloc)
        assert q[FEED_INSURANCE] == 224

    def test_an_oversized_fixed_spec_is_trimmed_without_zeroing_the_tail(self):
        """A small run must not be swallowed whole by one `all` feed."""
        alloc = {**bjs._FEED_ALLOCATION, FEED_INSURANCE: bjs._ALLOC_ALL}
        q = bjs._feed_quotas(200, LIVE_SUPPLY, alloc)
        assert sum(q.values()) == 200
        assert q[FEED_BOG] > 0 and q[FEED_ZATCA] > 0

    def test_absolute_spec_is_honoured(self):
        alloc = {**bjs._FEED_ALLOCATION, FEED_BOG: 1_500}
        q = bjs._feed_quotas(10_000, LIVE_SUPPLY, alloc)
        assert q[FEED_BOG] == 1_500
        assert sum(q.values()) == 10_000

    def test_unclaimed_feed_gets_nothing_by_default(self):
        q = bjs._feed_quotas(10_000, {**LIVE_SUPPLY, bjs._FEED_OTHER: 500}, bjs._FEED_ALLOCATION)
        assert q[bjs._FEED_OTHER] == 0


class TestParseFeedAlloc:
    def test_aliases_and_spec_forms(self):
        alloc = bjs.parse_feed_alloc(["bog=0.2", "insurance=all", "zatca=1500", "moj=rest"])
        assert alloc[FEED_BOG] == 0.2
        assert alloc[FEED_INSURANCE] == bjs._ALLOC_ALL
        assert alloc[FEED_ZATCA] == 1500
        assert alloc[FEED_MOJ] == bjs._ALLOC_REST

    def test_defaults_are_not_mutated(self):
        before = dict(bjs._FEED_ALLOCATION)
        bjs.parse_feed_alloc(["bog=0.99"])
        assert bjs._FEED_ALLOCATION == before

    @pytest.mark.parametrize("bad", ["nosuchfeed=0.1", "bog", "bog=nope", "bog=1.5", "bog=-3"])
    def test_bad_input_raises(self, bad):
        with pytest.raises(ValueError):
            bjs.parse_feed_alloc([bad])


# ── the sector ceiling ─────────────────────────────────────────────────────
class TestDomainPolicy:
    def test_a_domainless_feed_never_constrains_anything(self):
        """ديوان / زكاة / تأمين carry zero legal_domains. Their policy must be a
        no-op — if it shared state with وزارة العدل it would eat the headroom."""
        p = bjs._DomainPolicy.for_feed(has_domains=False)
        assert p.frac is bjs._DOMAIN_CAP_OFF
        for _ in range(500):
            assert p.allows(bjs._NO_DOMAIN) is True
            p.took(bjs._NO_DOMAIN)
        assert p.cap_at(1_000) is None

    def test_the_ceiling_is_a_ratio_not_a_row_count(self):
        """The SAME policy object must behave identically whether the run is
        small or large — that is what keeps a bigger --limit a superset."""
        p = bjs._DomainPolicy.for_feed(has_domains=True)
        taken = 0
        for _ in range(1_000):
            if p.allows("تجاري"):
                p.took("تجاري")
            else:
                p.took("عقار")  # a different sector wins the seat
            taken += 1
        assert p.counts["تجاري"] == pytest.approx(taken * bjs._DOMAIN_CAP_FRAC, abs=2)

    def test_first_pick_of_a_domain_is_always_allowed(self):
        p = bjs._DomainPolicy.for_feed(has_domains=True)
        assert p.allows("أي تصنيف") is True

    def test_cap_at_reports_rows_for_the_slice(self):
        p = bjs._DomainPolicy.for_feed(has_domains=True)
        assert p.cap_at(7_776) == round(7_776 * bjs._DOMAIN_CAP_FRAC)


class TestTakeFromCourt:
    def test_ceiling_is_advisory_and_never_stalls_the_fill(self):
        """A bucket that is entirely one domain must still yield rows, or a
        corpus that is 72% commercial could never reach --limit."""
        bucket = [{"id": str(i), "legal_domains": ["تجاري"]} for i in range(60)]
        p = bjs._DomainPolicy.for_feed(has_domains=True)
        for _ in range(60):
            row = bjs._take_from_court(bucket, p)
            assert row is not None
            p.took(bjs._primary_domain(row))
        assert bucket == []

    def test_it_reaches_past_the_head_for_an_under_quota_domain(self):
        """The window is bucket-relative, so on a big bucket it reaches deep —
        this is the fix that made the ceiling stop being a no-op at 10k scale."""
        bucket = [{"id": str(i), "legal_domains": ["تجاري"]} for i in range(100)]
        bucket.insert(40, {"id": "عقار-1", "legal_domains": ["عقار"]})
        p = bjs._DomainPolicy.for_feed(has_domains=True)
        p.counts["تجاري"] = 100  # saturated
        got = bjs._take_from_court(bucket, p)
        assert got["id"] == "عقار-1"

    def test_a_fixed_30_row_window_would_not_have_reached_it(self):
        """Guards the measurement in _DOMAIN_LOOKAHEAD_DIV's comment: the window
        must grow with the bucket, or التجارية's 15k-row bucket is never escaped."""
        big = 100
        assert max(bjs._DOMAIN_LOOKAHEAD_MIN, big // bjs._DOMAIN_LOOKAHEAD_DIV) > 30

    def test_empty_bucket_is_none(self):
        assert bjs._take_from_court([], bjs._DomainPolicy.for_feed(True)) is None


# ── the usage bonus ────────────────────────────────────────────────────────
def _stub_usage_client(refs: list[dict], owners: dict[str, tuple[str, str]]):
    class _Res:
        def __init__(self, data):
            self.data = data

    class _Q:
        def __init__(self, table):
            self.table = table
            self._ids: list[str] = []

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def in_(self, _col, ids):
            self._ids = list(ids)
            return self

        def range(self, lo, hi):
            self._lo, self._hi = lo, hi
            return self

        def execute(self):
            if self.table == bjs.REFS_TABLE:
                return _Res(refs[self._lo : self._hi + 1])
            return _Res(
                [
                    {"item_id": i, "user_id": owners[i][0], "conversation_id": owners[i][1]}
                    for i in self._ids
                    if i in owners
                ]
            )

    class _Client:
        def table(self, name):
            return _Q(name)

    return _Client()


class TestUsageScores:
    def test_a_conversation_votes_at_most_once(self):
        """20 references to one judgment inside ONE conversation must not outweigh
        two references from two different conversations."""
        refs = [
            {"ref_pk": f"p{i}", "wi_id": "w1", "item_id": "case-a", "used": True,
             "relevance": "high"}
            for i in range(20)
        ]
        client = _stub_usage_client(refs, {"w1": ("user-1", "conv-1")})
        scores = bjs.load_usage_scores(client)
        assert scores["case-a"] == pytest.approx(1.0)

    def test_one_account_cannot_define_the_listing(self):
        """The user cap is the second dampener: 40 conversations from one demo
        account stop contributing at _USAGE_USER_CAP."""
        refs = [
            {"ref_pk": f"p{i}", "wi_id": f"w{i}", "item_id": "case-a", "used": True,
             "relevance": "high"}
            for i in range(40)
        ]
        owners = {f"w{i}": ("user-1", f"conv-{i}") for i in range(40)}
        scores = bjs.load_usage_scores(_stub_usage_client(refs, owners))
        assert scores["case-a"] == pytest.approx(bjs._USAGE_USER_CAP)

    def test_breadth_across_users_outscores_depth_in_one(self):
        refs = [
            {"ref_pk": f"p{i}", "wi_id": f"w{i}", "item_id": "case-a", "used": True,
             "relevance": "high"}
            for i in range(3)
        ]
        owners = {f"w{i}": (f"user-{i}", f"conv-{i}") for i in range(3)}
        scores = bjs.load_usage_scores(_stub_usage_client(refs, owners))
        assert scores["case-a"] == pytest.approx(3.0)

    def test_unknown_relevance_degrades_to_the_floor(self):
        refs = [{"ref_pk": "p", "wi_id": "w", "item_id": "c", "used": False,
                 "relevance": "surprise"}]
        scores = bjs.load_usage_scores(_stub_usage_client(refs, {"w": ("u", "cv")}))
        assert scores["c"] == pytest.approx(bjs._USAGE_POINTS_FALLBACK)

    def test_no_refs_is_an_empty_map_not_a_crash(self):
        assert bjs.load_usage_scores(_stub_usage_client([], {})) == {}


class TestQualityScore:
    def test_usage_is_a_bonus_that_cannot_outrank_the_citation_mesh(self):
        """A judgment with NO referenced_regulations and maximal usage must still
        sort below one WITH them. Usage is a tiebreaker, never a demand signal."""
        meshed = {"_refs_count": 1}
        loved = {"_refs_count": 0, "_usage": 10_000.0}
        assert bjs._quality_score(meshed) > bjs._quality_score(loved)

    def test_the_bonus_is_capped(self):
        a = bjs._quality_score({"_usage": 4.0})
        b = bjs._quality_score({"_usage": 10_000.0})
        assert a == b == pytest.approx(bjs._USAGE_BONUS_MAX)

    def test_usage_breaks_a_tie_between_otherwise_equal_rows(self):
        plain = {"_refs_count": 1, "date_gregorian": "2024-01-01"}
        used = {"_refs_count": 1, "date_gregorian": "2024-01-01", "_usage": 1.0}
        assert bjs._quality_score(used) > bjs._quality_score(plain)


# ── end to end over a stubbed corpus ───────────────────────────────────────
def _corpus_row(i: int, court: str, level: str, domain: str | None) -> dict:
    return {
        "id": f"{court[:4]}-{i:05d}",
        "case_ref": f"ref-{court[:4]}-{i:05d}",
        "court": court,
        "court_level": level,
        "city": "الرياض",
        "case_number": str(i),
        "judgment_number": str(i),
        "date_hijri": "1446",
        "date_gregorian": f"2024-01-{(i % 28) + 1:02d}",
        "legal_domains": [domain] if domain else [],
        "short_summary": f"نزاع تجاري رقم {i} حول تنفيذ التزام تعاقدي بين طرفين",
    }


def _fake_corpus() -> list[dict]:
    """A miniature of the real corpus shape: one sector-bearing feed and three
    sector-less ones, with a dominant court inside the big feed."""
    rows: list[dict] = []
    for i in range(600):
        rows.append(_corpus_row(i, COURT_MOJ, "first_instance",
                                "تجاري" if i % 4 else "عقار"))
    for i in range(200):
        rows.append(_corpus_row(1000 + i, COURT_MOJ, "appeal", "تجاري"))
    for i in range(60):
        rows.append(_corpus_row(2000 + i, COURT_MOJ_ALT, "first_instance", "إسكان"))
    for i in range(300):
        rows.append(_corpus_row(3000 + i, COURT_BOG, "first_instance", None))
    for i in range(300):
        rows.append(_corpus_row(4000 + i, COURT_ZATCA, "first_instance", None))
    for i in range(50):
        rows.append(_corpus_row(5000 + i, COURT_INSURANCE, "appeal", None))
    return rows


@pytest.fixture
def stubbed(monkeypatch):
    corpus = _fake_corpus()
    monkeypatch.setattr(bjs, "_load_candidates", lambda _c: [dict(r) for r in corpus])
    monkeypatch.setattr(bjs, "_load_refs_ids", lambda _c: {r["id"] for r in corpus[::2]})
    monkeypatch.setattr(bjs, "load_usage_scores", lambda _c, **_k: {corpus[0]["id"]: 5.0})
    return corpus


class TestSelectSample:
    def test_every_feed_is_represented_and_the_limit_is_met(self, stubbed):
        rows, stats = bjs.select_sample(None, 400)
        assert len(rows) == 400
        assert stats["selected"] == 400
        for feed in (FEED_MOJ, FEED_BOG, FEED_ZATCA, FEED_INSURANCE):
            assert stats["per_feed"].get(feed, 0) > 0, f"{feed} got no rows"

    def test_no_row_is_selected_twice(self, stubbed):
        rows, _ = bjs.select_sample(None, 800)
        ids = [str(r["id"]) for r in rows]
        assert len(ids) == len(set(ids))

    def test_a_bigger_limit_is_a_superset(self, stubbed):
        """THE idempotency invariant. Already-slugged rows are skipped on write,
        so a bigger run must TOP UP; if the selection re-shuffled instead, every
        raise of --limit would strand rows and publish an unpredictable count."""
        small = {str(r["id"]) for r in bjs.select_sample(None, 200)[0]}
        big = {str(r["id"]) for r in bjs.select_sample(None, 600)[0]}
        assert small <= big

    def test_selection_is_deterministic(self, stubbed):
        a = [str(r["id"]) for r in bjs.select_sample(None, 300)[0]]
        b = [str(r["id"]) for r in bjs.select_sample(None, 300)[0]]
        assert a == b

    def test_the_sector_ceiling_is_scoped_to_the_feed_that_has_sectors(self, stubbed):
        """The three sector-less feeds must get their own OFF policy. Sharing one
        counter would let their (بدون تصنيف) rows spend the ceiling's headroom."""
        _rows, stats = bjs.select_sample(None, 600)
        policies = stats["domain_policies"]
        assert policies[FEED_MOJ].frac == bjs._DOMAIN_CAP_FRAC
        for feed in (FEED_BOG, FEED_ZATCA, FEED_INSURANCE):
            assert policies[feed].frac is bjs._DOMAIN_CAP_OFF
            assert policies[feed] is not policies[FEED_MOJ]

    def test_the_ceiling_measurably_beats_having_no_ceiling(self, stubbed, monkeypatch):
        """THE regression guard for Task 3. An absolute threshold would be a test
        of the fixture; what must not regress is that turning the ceiling ON
        changes the outcome. It has silently failed to do so twice — once from
        being computed globally, once from a fixed 30-row window — and both times
        every other assertion in this file still passed.
        """
        def moj_share(rows):
            moj = [r for r in rows if bjs._feed_of(r) == FEED_MOJ]
            return Counter(bjs._primary_domain(r) for r in moj)["تجاري"] / len(moj)

        with_ceiling = moj_share(bjs.select_sample(None, 400)[0])
        monkeypatch.setattr(
            bjs._DomainPolicy, "for_feed",
            classmethod(lambda cls, has_domains: cls(bjs._DOMAIN_CAP_OFF)),
        )
        without = moj_share(bjs.select_sample(None, 400)[0])
        assert with_ceiling < without - 0.05, (
            f"the sector ceiling moved the dominant domain from {without:.0%} to "
            f"{with_ceiling:.0%} — it is not doing any work"
        )

    def test_a_limit_beyond_the_corpus_returns_everything_once(self, stubbed):
        rows, stats = bjs.select_sample(None, 99_999)
        assert len(rows) == stats["eligible"] == len(stubbed)

    def test_unclaimed_courts_are_reported_not_silently_dropped(self, monkeypatch):
        corpus = _fake_corpus() + [
            _corpus_row(9001, "محكمة الفضاء الخارجي", "first_instance", None)
        ]
        monkeypatch.setattr(bjs, "_load_candidates", lambda _c: [dict(r) for r in corpus])
        monkeypatch.setattr(bjs, "_load_refs_ids", lambda _c: set())
        monkeypatch.setattr(bjs, "load_usage_scores", lambda _c, **_k: {})
        _rows, stats = bjs.select_sample(None, 100)
        assert stats["unclaimed_courts"] == {"محكمة الفضاء الخارجي": 1}
        # …and it takes no allocation.
        assert stats["per_feed"].get(bjs._FEED_OTHER, 0) == 0

    def test_report_renders_without_error(self, stubbed, capsys):
        rows, stats = bjs.select_sample(None, 400)
        bjs._print_breakdown(rows, stats)
        out = capsys.readouterr().out
        assert "SOURCE FEED" in out
        assert "court section" in out
        assert "usage signal" in out
        # The sector table must name the feed it is scoped to.
        assert bjs._FEED_LABELS[FEED_MOJ] in out


class TestNoRetiredCeiling:
    def test_no_live_code_still_guards_a_publish_ceiling(self):
        """The wing paginates ``library_judgments_ranked`` now, so
        ``SAMPLE_MODE_MAX_IDS`` does not apply to it and the old warning fired
        ~700 rows early besides (it hardcoded 300 against a live 1000).

        ⚠ ASSERTED OVER THE AST, NOT THE TEXT. A substring check on the source
        cannot tell a live ``if total > 300`` from the comment that explains why
        that warning was deleted — and the explanation is worth keeping, or the
        ceiling gets "restored" by the next reader who notices it is missing. So
        this walks the parsed tree and looks for a real numeric comparison
        against a retired ceiling, which is the thing that would actually
        misbehave. Comments and docstrings are invisible to it by construction.
        """
        src = (_ROOT / "scripts" / "build_judgment_slugs.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        retired = {300, 1000}
        offenders = [
            f"line {node.lineno}: compares against {const.value}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            for const in [*([node.left]), *node.comparators]
            if isinstance(const, ast.Constant)
            and isinstance(const.value, int)
            and const.value in retired
        ]
        assert not offenders, (
            "a publish-ceiling comparison is still live in build_judgment_slugs: "
            + "; ".join(offenders)
        )

        # The prose may DISCUSS the retired ceiling; it must not instruct anyone
        # to honour it.
        assert "stay at or below 300" not in src
