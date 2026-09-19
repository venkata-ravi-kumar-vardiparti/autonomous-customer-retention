"""Deterministic synthetic seed data for the Governed Data Layer.

Fixed random seed (SEED) and a fixed reference "now" (SEED_REFERENCE_NOW,
not wall-clock time) so that reseeding is reproducible: same inputs, same
sequence of RNG draws, same insertion order -> identical row content every
time. See compute_content_hash for how "identical" is verified.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import aiosqlite

from churnguard.data.db import get_writable_connection_for_migrations, resolve_db_path

SEED = 1337
SEED_REFERENCE_NOW = datetime(2026, 9, 19, tzinfo=UTC)
TOTAL_ACCOUNTS = 30

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

PLANS = [
    ("PLAN_UNLIMITED_PLUS", "Unlimited Plus", 55.0),
    ("PLAN_UNLIMITED_BASIC", "Unlimited Basic", 40.0),
    ("PLAN_FAMILY_SHARE", "Family Share", 35.0),
    ("PLAN_VALUE_TALK_TEXT", "Value Talk & Text", 25.0),
]

PROMOTIONS = [
    ("PROMO_LOYALTY12", "12-Month Loyalty Discount", "10% off for 12 months", 15.0),
    ("PROMO_AUTOPAY5", "Autopay Discount", "$5/mo for enrolling in autopay", 5.0),
    ("PROMO_BUNDLE_HOME", "Home + Mobile Bundle", "Discount for bundling home internet", 10.0),
    ("PROMO_MILITARY", "Military Appreciation", "Discount for active-duty/veteran accounts", 12.0),
    ("PROMO_WINBACK", "Win-back Offer", "Discount for returning customers", 20.0),
]

FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Drew",
    "Cameron", "Skyler", "Reese", "Quinn", "Rowan", "Emerson", "Finley",
    "Hayden", "Dakota", "Sage", "Peyton", "Avery",
]
LAST_NAMES = [
    "Nguyen", "Smith", "Garcia", "Patel", "Kim", "Johnson", "Brown", "Davis",
    "Martinez", "Lee", "Wilson", "Clark", "Lewis", "Walker", "Young", "Hall",
    "Allen", "King", "Wright", "Scott",
]

NOTE_TEMPLATES = [
    "Customer called about a billing question; issue was explained and closed.",
    "Customer asked about upgrade eligibility timing.",
    "Follow-up call scheduled after a service outage in the area.",
    "Customer requested clarification on autopay enrollment.",
    "Customer inquired about international roaming rates.",
]

JURISDICTIONS = ["US-CA", "US-TX", "US-NY", "US-FL"]
CONTRACT_TYPES = ["month_to_month", "24_month_term"]
USAGE_LEVELS = ["low_data", "medium_data", "high_data"]
MARKETING_SEGMENTS = ["value_seeker", "family_bundle", "premium_data", "business_lite"]
DEVICE_NAMES = ["Handset Model A", "Handset Model B", "Tablet Model C", "Handset Model D"]

CAUSES_POOL = [
    ("loyalty_promo_expired", "12-month loyalty promo ended", True),
    ("autopay_discount_lost", "card_expired", True),
    ("regulatory_fee_increase", "state regulatory fee adjustment", False),
    ("plan_change", "customer changed plan mid-cycle", True),
    ("one_time_fee", "one-time service fee", False),
    ("usage_overage", "data overage charge", False),
]


@dataclass
class SeedLine:
    line_index: int
    status: str = "active"


@dataclass
class SeedDeviceFinancing:
    line_index: int
    device_name: str
    remaining_balance: float
    monthly_payment: float
    months_remaining: int
    early_termination_fee: float


@dataclass
class SeedBillingAttribution:
    cause: str
    amount: float
    event_date: date | None
    reason: str | None
    reversible: bool


@dataclass
class SeedAccount:
    account_id: str
    holder_full_name: str
    date_of_birth: str
    zip_plus4: str
    marketing_segment: str
    autopay_card_pan: str
    autopay_card_expired: bool
    tenure_months: int
    status: str
    jurisdiction: str
    contract_type: str
    contract_end_date: str | None
    plan_code: str
    payment_on_time_count: int
    payment_late_count: int
    payment_last_late_date: str | None
    payment_current_past_due: float
    lines: list[SeedLine]
    device_financing: dict[int, SeedDeviceFinancing]
    current_bill: float
    prior_bill: float
    billing_attributions: list[SeedBillingAttribution]
    usage_by_line_index: dict[int, str | None]
    active_promo_codes: list[str]
    notes: list[str] = field(default_factory=list)


class IdGen:
    """Deterministic, monotonic, per-prefix surrogate key generator."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}-{n:06d}"


def _random_dob(rng: random.Random) -> date:
    start = date(1950, 1, 1).toordinal()
    end = date(2005, 12, 31).toordinal()
    return date.fromordinal(rng.randint(start, end))


def _random_zip_plus4(rng: random.Random) -> str:
    return f"{rng.randint(10000, 99999)}-{rng.randint(1000, 9999)}"


def _random_pan(rng: random.Random) -> str:
    return f"4{rng.randint(0, 10**15 - 1):015d}"


def _split_amount(rng: random.Random, total: float, n: int) -> list[float]:
    weights = [rng.uniform(0.2, 1.0) for _ in range(n)]
    weight_sum = sum(weights)
    amounts = [round(total * w / weight_sum, 2) for w in weights]
    drift = round(total - sum(amounts), 2)
    amounts[-1] = round(amounts[-1] + drift, 2)
    return amounts


def _base_random_account(rng: random.Random, last4: str) -> SeedAccount:
    account_id = f"{rng.randint(0, 999999):06d}{last4}"
    line_count = rng.randint(1, 5)
    lines = [SeedLine(i) for i in range(1, line_count + 1)]

    device_financing: dict[int, SeedDeviceFinancing] = {}
    for i in range(1, line_count + 1):
        if rng.random() < 0.4:
            device_financing[i] = SeedDeviceFinancing(
                line_index=i,
                device_name=rng.choice(DEVICE_NAMES),
                remaining_balance=round(rng.uniform(50, 900), 2),
                monthly_payment=round(rng.uniform(10, 45), 2),
                months_remaining=rng.randint(1, 24),
                early_termination_fee=round(rng.uniform(0, 200), 2),
            )

    current_bill = round(rng.uniform(45, 260), 2)
    delta = round(rng.uniform(-20, 40), 2)
    prior_bill = round(current_bill - delta, 2)
    delta = round(current_bill - prior_bill, 2)

    num_causes = rng.randint(0, 2)
    attributions: list[SeedBillingAttribution] = []
    if num_causes > 0 and abs(delta) > 0.01:
        chosen = rng.sample(CAUSES_POOL, k=min(num_causes, len(CAUSES_POOL)))
        for (cause, reason, reversible), amount in zip(
            chosen, _split_amount(rng, delta, len(chosen)), strict=True
        ):
            attributions.append(
                SeedBillingAttribution(
                    cause=cause,
                    amount=amount,
                    event_date=SEED_REFERENCE_NOW.date() - timedelta(days=rng.randint(1, 20)),
                    reason=reason,
                    reversible=reversible,
                )
            )

    usage_by_line_index: dict[int, str | None] = {}
    for i in range(1, line_count + 1):
        usage_by_line_index[i] = rng.choice(USAGE_LEVELS) if rng.random() < 0.85 else None

    active_promo_codes = rng.sample(
        [p[0] for p in PROMOTIONS], k=rng.choice([0, 0, 1, 1, 2])
    )
    notes = rng.sample(NOTE_TEMPLATES, k=rng.choice([0, 0, 1]))

    return SeedAccount(
        account_id=account_id,
        holder_full_name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        date_of_birth=_random_dob(rng).isoformat(),
        zip_plus4=_random_zip_plus4(rng),
        marketing_segment=rng.choice(MARKETING_SEGMENTS),
        autopay_card_pan=_random_pan(rng),
        autopay_card_expired=rng.random() < 0.1,
        tenure_months=rng.randint(1, 96),
        status="active",
        jurisdiction=rng.choice(JURISDICTIONS),
        contract_type=rng.choice(CONTRACT_TYPES),
        contract_end_date=None,
        plan_code=rng.choice([p[0] for p in PLANS]),
        payment_on_time_count=rng.randint(0, 80),
        payment_late_count=rng.randint(0, 3),
        payment_last_late_date=None,
        payment_current_past_due=0.0,
        lines=lines,
        device_financing=device_financing,
        current_bill=current_bill,
        prior_bill=prior_bill,
        billing_attributions=attributions,
        usage_by_line_index=usage_by_line_index,
        active_promo_codes=active_promo_codes,
        notes=notes,
    )


def build_worked_example(rng: random.Random, last4: str) -> SeedAccount:
    """The canonical worked example from the phase brief: ACCT_****4471."""
    account_id = f"{rng.randint(0, 999999):06d}{last4}"
    lines = [SeedLine(i) for i in range(1, 5)]
    device_financing = {
        3: SeedDeviceFinancing(
            line_index=3,
            device_name="Handset Model A",
            remaining_balance=312.40,
            monthly_payment=31.24,
            months_remaining=10,
            early_termination_fee=150.0,
        )
    }
    attributions = [
        SeedBillingAttribution(
            cause="loyalty_promo_expired",
            amount=20.00,
            event_date=SEED_REFERENCE_NOW.date() - timedelta(days=10),
            reason="12-month loyalty promo ended",
            reversible=True,
        ),
        SeedBillingAttribution(
            cause="autopay_discount_lost",
            amount=8.00,
            event_date=SEED_REFERENCE_NOW.date() - timedelta(days=8),
            reason="card_expired",
            reversible=True,
        ),
        SeedBillingAttribution(
            cause="regulatory_fee_increase",
            amount=5.23,
            event_date=SEED_REFERENCE_NOW.date() - timedelta(days=5),
            reason="state regulatory fee adjustment",
            reversible=False,
        ),
    ]
    usage_by_line_index: dict[int, str | None] = {
        1: "high_data",
        2: "medium_data",
        3: None,
        4: "low_data",
    }
    return SeedAccount(
        account_id=account_id,
        holder_full_name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        date_of_birth=_random_dob(rng).isoformat(),
        zip_plus4=_random_zip_plus4(rng),
        marketing_segment=rng.choice(MARKETING_SEGMENTS),
        autopay_card_pan=_random_pan(rng),
        autopay_card_expired=True,
        tenure_months=74,
        status="active",
        jurisdiction="US-TX",
        contract_type="month_to_month",
        contract_end_date=None,
        plan_code="PLAN_UNLIMITED_PLUS",
        payment_on_time_count=70,
        payment_late_count=2,
        payment_last_late_date=(SEED_REFERENCE_NOW.date() - timedelta(days=200)).isoformat(),
        payment_current_past_due=0.0,
        lines=lines,
        device_financing=device_financing,
        current_bill=198.43,
        prior_bill=165.20,
        billing_attributions=attributions,
        usage_by_line_index=usage_by_line_index,
        active_promo_codes=[],
        notes=["Customer flagged the bill increase and asked for a breakdown."],
    )


def build_delinquent(rng: random.Random, last4: str) -> SeedAccount:
    acct = _base_random_account(rng, last4)
    acct.status = "delinquent"
    acct.payment_on_time_count = 10
    acct.payment_late_count = 9
    acct.payment_last_late_date = (SEED_REFERENCE_NOW.date() - timedelta(days=15)).isoformat()
    acct.payment_current_past_due = 245.67
    return acct


def build_no_financing(rng: random.Random, last4: str) -> SeedAccount:
    acct = _base_random_account(rng, last4)
    acct.device_financing = {}
    return acct


def build_three_promos(rng: random.Random, last4: str) -> SeedAccount:
    acct = _base_random_account(rng, last4)
    acct.active_promo_codes = [p[0] for p in PROMOTIONS[:3]]
    return acct


def build_contradictory_billing(rng: random.Random, last4: str) -> SeedAccount:
    """delta_attribution amounts deliberately don't sum to the bill delta.

    The data layer must surface this as-is; it is not the repository's job
    to reconcile a legacy system's inconsistent bookkeeping.
    """
    acct = _base_random_account(rng, last4)
    acct.current_bill = 142.10
    acct.prior_bill = 120.00
    acct.billing_attributions = [
        SeedBillingAttribution(
            cause="loyalty_promo_expired",
            amount=15.00,
            event_date=SEED_REFERENCE_NOW.date() - timedelta(days=9),
            reason="12-month loyalty promo ended",
            reversible=True,
        ),
        SeedBillingAttribution(
            cause="duplicate_fee_charged",
            amount=20.00,
            event_date=SEED_REFERENCE_NOW.date() - timedelta(days=3),
            reason="billing system double-charged a one-time fee",
            reversible=True,
        ),
    ]
    return acct


def build_churned(rng: random.Random, last4: str) -> SeedAccount:
    acct = _base_random_account(rng, last4)
    acct.status = "churned"
    acct.contract_end_date = (SEED_REFERENCE_NOW.date() - timedelta(days=30)).isoformat()
    return acct


def build_all_accounts(rng: random.Random) -> list[SeedAccount]:
    # 0000 is reserved and never assigned, so tests have a guaranteed-unused
    # last-4 to exercise the "account not found" path.
    candidates = [v for v in range(10000) if v not in (0, 4471)]
    rng.shuffle(candidates)
    last4_values = ["4471"] + [f"{v:04d}" for v in candidates[: TOTAL_ACCOUNTS - 1]]

    accounts = [
        build_worked_example(rng, last4_values[0]),
        build_delinquent(rng, last4_values[1]),
        build_no_financing(rng, last4_values[2]),
        build_three_promos(rng, last4_values[3]),
        build_contradictory_billing(rng, last4_values[4]),
        build_churned(rng, last4_values[5]),
    ]
    for last4 in last4_values[6:]:
        accounts.append(_base_random_account(rng, last4))
    return accounts


def build_competitor_snapshots() -> list[dict[str, object]]:
    """Curated, hand-specified — this is never scraped, so it isn't randomized."""
    tx_dfw = [
        ("RivalCo", "RivalCo Unlimited", 135.0, 3, ["unlimited_data"], 41),  # deliberately stale
        ("RivalCo", "RivalCo Value", 90.0, 2, ["shared_data"], 5),  # deliberately fresh
        ("MetroWave", "MetroWave Unlimited Plus", 150.0, 3, ["unlimited_data", "hotspot"], 12),
        ("MetroWave", "MetroWave Basic", 70.0, 2, ["shared_data"], 20),
    ]
    other = [
        ("US-CA", "RivalCo", "RivalCo Unlimited", 140.0, 3, ["unlimited_data"], 8),
        ("US-CA", "MetroWave", "MetroWave Unlimited Plus", 145.0, 3, ["unlimited_data"], 15),
    ]
    snapshots: list[dict[str, object]] = []
    for carrier, plan_name, price, line_count, includes, age in tx_dfw:
        captured_at = SEED_REFERENCE_NOW - timedelta(days=age)
        snapshots.append(
            {
                "geography": "TX-DFW",
                "carrier": carrier,
                "plan_name": plan_name,
                "monthly_price": price,
                "line_count": line_count,
                "includes": includes,
                "captured_at": captured_at.isoformat(),
                "capture_method": "curated_manual",
            }
        )
    for geography, carrier, plan_name, price, line_count, includes, age in other:
        captured_at = SEED_REFERENCE_NOW - timedelta(days=age)
        snapshots.append(
            {
                "geography": geography,
                "carrier": carrier,
                "plan_name": plan_name,
                "monthly_price": price,
                "line_count": line_count,
                "includes": includes,
                "captured_at": captured_at.isoformat(),
                "capture_method": "curated_manual",
            }
        )
    return snapshots


async def _insert_catalogs(conn: aiosqlite.Connection) -> None:
    await conn.executemany(
        "INSERT INTO plans (plan_code, plan_name, monthly_base_price) VALUES (?, ?, ?)", PLANS
    )
    await conn.executemany(
        "INSERT INTO promotions (promo_code, promo_name, description, monthly_value) "
        "VALUES (?, ?, ?, ?)",
        PROMOTIONS,
    )
    await conn.execute(
        "INSERT INTO policy_packs (policy_pack_version, policy_pack_hash, published_at, "
        "description) VALUES (?, ?, ?, ?)",
        (
            "2026.09.1",
            "sha256:seedplaceholder0001",
            SEED_REFERENCE_NOW.isoformat(),
            "Phase 0 placeholder policy pack; real rules land in Phase 5.",
        ),
    )


async def _insert_account(conn: aiosqlite.Connection, acct: SeedAccount, id_gen: IdGen) -> None:
    data_as_of = SEED_REFERENCE_NOW.isoformat()
    created_at = (SEED_REFERENCE_NOW - timedelta(days=acct.tenure_months * 30)).isoformat()
    await conn.execute(
        """
        INSERT INTO accounts (
            account_id, holder_full_name, date_of_birth, zip_plus4, marketing_segment,
            autopay_card_pan, autopay_card_expired, tenure_months, status, jurisdiction,
            contract_type, contract_end_date, plan_code, payment_on_time_count,
            payment_late_count, payment_last_late_date, payment_current_past_due,
            data_as_of, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            acct.account_id,
            acct.holder_full_name,
            acct.date_of_birth,
            acct.zip_plus4,
            acct.marketing_segment,
            acct.autopay_card_pan,
            int(acct.autopay_card_expired),
            acct.tenure_months,
            acct.status,
            acct.jurisdiction,
            acct.contract_type,
            acct.contract_end_date,
            acct.plan_code,
            acct.payment_on_time_count,
            acct.payment_late_count,
            acct.payment_last_late_date,
            acct.payment_current_past_due,
            data_as_of,
            created_at,
        ),
    )

    for line in acct.lines:
        line_id = f"{acct.account_id}-L{line.line_index}"
        await conn.execute(
            "INSERT INTO lines (line_id, line_index, status) VALUES (?, ?, ?)",
            (line_id, line.line_index, line.status),
        )
        await conn.execute(
            "INSERT INTO account_edges (from_node, edge_type, to_node) VALUES (?, ?, ?)",
            (acct.account_id, "has_line", line_id),
        )

        financing = acct.device_financing.get(line.line_index)
        if financing is not None:
            device_fin_id = id_gen.next("DEV")
            await conn.execute(
                """
                INSERT INTO device_financing (
                    device_fin_id, device_name, remaining_balance, monthly_payment,
                    months_remaining, early_termination_fee
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    device_fin_id,
                    financing.device_name,
                    financing.remaining_balance,
                    financing.monthly_payment,
                    financing.months_remaining,
                    financing.early_termination_fee,
                ),
            )
            await conn.execute(
                "INSERT INTO account_edges (from_node, edge_type, to_node) VALUES (?, ?, ?)",
                (line_id, "financed_by", device_fin_id),
            )

        usage_level = acct.usage_by_line_index.get(line.line_index)
        if usage_level is not None:
            usage_id = id_gen.next("USG")
            usage_date = (SEED_REFERENCE_NOW.date() - timedelta(days=1)).isoformat()
            await conn.execute(
                "INSERT INTO usage_daily (usage_id, line_id, usage_date, usage_level) "
                "VALUES (?, ?, ?, ?)",
                (usage_id, line_id, usage_date, usage_level),
            )

    current_event_id = id_gen.next("EVT")
    await conn.execute(
        "INSERT INTO billing_events (event_id, kind, bill_period, cause, amount, event_date, "
        "reason, reversible) VALUES (?, 'bill_total', 'current', NULL, ?, NULL, NULL, NULL)",
        (current_event_id, acct.current_bill),
    )
    await conn.execute(
        "INSERT INTO account_edges (from_node, edge_type, to_node) VALUES (?, ?, ?)",
        (acct.account_id, "has_billing_event", current_event_id),
    )
    prior_event_id = id_gen.next("EVT")
    await conn.execute(
        "INSERT INTO billing_events (event_id, kind, bill_period, cause, amount, event_date, "
        "reason, reversible) VALUES (?, 'bill_total', 'prior', NULL, ?, NULL, NULL, NULL)",
        (prior_event_id, acct.prior_bill),
    )
    await conn.execute(
        "INSERT INTO account_edges (from_node, edge_type, to_node) VALUES (?, ?, ?)",
        (acct.account_id, "has_billing_event", prior_event_id),
    )
    for attribution in acct.billing_attributions:
        event_id = id_gen.next("EVT")
        await conn.execute(
            "INSERT INTO billing_events (event_id, kind, bill_period, cause, amount, "
            "event_date, reason, reversible) VALUES (?, 'delta_attribution', 'current', "
            "?, ?, ?, ?, ?)",
            (
                event_id,
                attribution.cause,
                attribution.amount,
                attribution.event_date.isoformat() if attribution.event_date else None,
                attribution.reason,
                int(attribution.reversible),
            ),
        )
        await conn.execute(
            "INSERT INTO account_edges (from_node, edge_type, to_node) VALUES (?, ?, ?)",
            (acct.account_id, "has_billing_event", event_id),
        )

    for promo_code in acct.active_promo_codes:
        elig_id = id_gen.next("ELG")
        await conn.execute(
            "INSERT INTO promo_eligibility (elig_id, promo_code, status, effective_date) "
            "VALUES (?, ?, 'active', ?)",
            (elig_id, promo_code, data_as_of),
        )
        await conn.execute(
            "INSERT INTO account_edges (from_node, edge_type, to_node) VALUES (?, ?, ?)",
            (acct.account_id, "has_promo_eligibility", elig_id),
        )

    for note_text in acct.notes:
        note_id = id_gen.next("NOTE")
        await conn.execute(
            "INSERT INTO account_notes (note_id, author_ref, created_at, note_text) "
            "VALUES (?, ?, ?, ?)",
            (note_id, "AGT_****0192", data_as_of, note_text),
        )
        await conn.execute(
            "INSERT INTO account_edges (from_node, edge_type, to_node) VALUES (?, ?, ?)",
            (acct.account_id, "has_note", note_id),
        )


async def _insert_competitor_snapshots(conn: aiosqlite.Connection, id_gen: IdGen) -> None:
    for snap in build_competitor_snapshots():
        snapshot_id = id_gen.next("SNAP")
        await conn.execute(
            """
            INSERT INTO competitor_snapshots (
                snapshot_id, geography, carrier, plan_name, monthly_price, line_count,
                includes_json, captured_at, capture_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                snap["geography"],
                snap["carrier"],
                snap["plan_name"],
                snap["monthly_price"],
                snap["line_count"],
                json.dumps(snap["includes"]),
                snap["captured_at"],
                snap["capture_method"],
            ),
        )


async def seed_database(db_path: str, *, seed: int = SEED) -> None:
    path = Path(db_path)
    if path.exists():
        path.unlink()

    rng = random.Random(seed)
    accounts = build_all_accounts(rng)
    id_gen = IdGen()

    conn = get_writable_connection_for_migrations(db_path)
    async with conn:
        await conn.executescript(SCHEMA_PATH.read_text())
        await _insert_catalogs(conn)
        for acct in accounts:
            await _insert_account(conn, acct, id_gen)
        await _insert_competitor_snapshots(conn, id_gen)
        await conn.commit()


_CONTENT_HASH_TABLES = [
    ("plans", "plan_code"),
    ("accounts", "account_id"),
    ("lines", "line_id"),
    ("billing_events", "event_id"),
    ("usage_daily", "usage_id"),
    ("device_financing", "device_fin_id"),
    ("promotions", "promo_code"),
    ("promo_eligibility", "elig_id"),
    ("account_notes", "note_id"),
    ("competitor_snapshots", "snapshot_id"),
    ("policy_packs", "policy_pack_version"),
    ("audit_log", "audit_id"),
    ("account_edges", "from_node, edge_type, to_node"),
]


async def compute_content_hash(db_path: str) -> str:
    """Hash the DB's row content (not its raw bytes) in a fixed table/row order.

    Hashing content rather than the file's bytes sidesteps SQLite's own
    storage-layer nondeterminism (page/freelist layout) and tests the thing
    that actually matters: does reseeding produce the same data.
    """
    conn = get_writable_connection_for_migrations(db_path)
    hasher = hashlib.sha256()
    async with conn:
        conn.row_factory = None
        for table, order_by in _CONTENT_HASH_TABLES:
            cursor = await conn.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
            columns = [description[0] for description in cursor.description]
            async for row in cursor:
                record = dict(zip(columns, row, strict=True))
                hasher.update(json.dumps(record, sort_keys=True, default=str).encode("utf-8"))
            await cursor.close()
            hasher.update(b"--table-boundary--")
    return hasher.hexdigest()


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Seed the ChurnGuard Governed Data Layer.")
    parser.add_argument("--db-path", default=resolve_db_path())
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    await seed_database(args.db_path, seed=args.seed)
    content_hash = await compute_content_hash(args.db_path)
    print(f"seeded {args.db_path} (seed={args.seed}) content_hash={content_hash}")


if __name__ == "__main__":
    asyncio.run(_main())
