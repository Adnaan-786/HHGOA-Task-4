from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from .repository import DatasetRepository, Transaction

FEATURE_VERSION = "behavior-v1"


@dataclass(frozen=True)
class Finding:
    claim: str
    transaction_ids: tuple[str, ...]
    group: str | None = None
    direction: str = "neutral"


@dataclass(frozen=True)
class BehaviorAnalysis:
    features: dict[str, float]
    pattern: str
    episode_ids: tuple[str, ...]
    findings: tuple[Finding, ...]
    missing: tuple[str, ...]

    @property
    def supporting_groups(self) -> set[str]:
        return {f.group for f in self.findings if f.group and f.direction == "supports"}

    @property
    def legitimate_groups(self) -> set[str]:
        return {f.group for f in self.findings if f.group and f.direction == "contradicts"}


def analyze_behavior(repository: DatasetRepository, flagged_id: str, cutoff: datetime) -> BehaviorAnalysis:
    """Derive observations without accessing case outcomes, narratives, or case IDs."""
    tx = repository.transactions[flagged_id]
    if tx.timestamp > cutoff:
        raise ValueError("Flagged transaction is later than the investigation cutoff")
    history = [t for t in repository.by_card[tx.card_id]
               if tx.timestamp - timedelta(days=90) <= t.timestamp <= cutoff]
    episode_start = tx.timestamp - timedelta(hours=48)
    baseline = [t for t in history if t.timestamp < episode_start]
    recent = [t for t in history if t.timestamp >= episode_start]
    recent.sort(key=lambda t: (t.timestamp, t.transaction_id))
    identity = repository.identities.get(tx.transaction_id)
    baseline_amounts = [abs(t.amount_cents) for t in baseline]
    baseline_median = median(baseline_amounts) if baseline else 0
    mad = median([abs(v - baseline_median) for v in baseline_amounts]) if baseline else 0
    amounts_known = len(baseline) >= 3
    amount_ratio = abs(tx.amount_cents) / max(baseline_median, 100) if amounts_known else 1
    amount_deviation = abs(abs(tx.amount_cents) - baseline_median) / max(mad * 1.4826, baseline_median * .10, 100) if amounts_known else 0
    products = Counter(t.product_code for t in baseline)
    regions = {t.addr1 for t in baseline if t.addr1}
    profiles = {repository.identities[t.transaction_id].device_profile for t in baseline
                if t.transaction_id in repository.identities and repository.identities[t.transaction_id].device_profile}
    product_novel = bool(baseline and tx.product_code not in products)
    region_novel = bool(regions and tx.addr1 and tx.addr1 not in regions)
    profile = identity.device_profile if identity else ""
    device_familiar = bool(profile and profile in profiles)
    marked_new = bool(identity and identity.device_newness.lower() == "new")
    proxy_signal = bool(identity and identity.proxy and "unknown" not in identity.proxy.lower())
    online = [t for t in recent if t.channel == "online"]
    online_baseline = [t for t in baseline if t.channel == "online"]
    one_hour = [t for t in recent if tx.timestamp - timedelta(hours=1) <= t.timestamp <= tx.timestamp]
    baseline_days = max((episode_start - baseline[0].timestamp).total_seconds() / 86400, 1) if baseline else 1
    expected_48h = len(baseline) / baseline_days * 2 if baseline else 0
    velocity_ratio = len(recent) / max(expected_48h, 1)
    unusual_amount = amounts_known and amount_deviation >= 3
    unusual_product = len(baseline) >= 3 and product_novel
    unusual_online = len(baseline) >= 3 and not online_baseline
    burst = len(online) >= 2 and velocity_ratio >= 2 and bool(unusual_amount or unusual_product or unusual_online)

    testing: dict[str, Transaction] = {}
    for purchase in online:
        if purchase.amount_cents < 500:
            continue
        small = [t for t in online if 0 < t.amount_cents < 500
                 and purchase.timestamp - timedelta(hours=1) <= t.timestamp < purchase.timestamp]
        if len(small) >= 3 and any(t.transaction_id == flagged_id for t in [purchase, *small]):
            testing.update({t.transaction_id: t for t in [purchase, *small]})

    region_transactions = [t for t in recent if t.addr1 and t.addr1 == tx.addr1]
    region_days = len({t.timestamp.date() for t in region_transactions})
    home_continues = bool(region_novel and any(t.addr1 in regions for t in recent if t.transaction_id != flagged_id))
    trip_candidate = region_novel and region_days >= 2 and not home_continues
    mixed_channels = len({t.channel for t in recent}) >= 2
    anomalous_behavior = unusual_amount or unusual_product or burst
    takeover_candidate = mixed_channels and anomalous_behavior and marked_new and proxy_signal

    features = {
        "risk_score": tx.risk_score,
        "amount_log": math.log1p(abs(tx.amount_cents) / 100),
        "online": float(tx.channel == "online"),
        "baseline_count_log": math.log1p(len(baseline)),
        "baseline_available": float(amounts_known),
        "amount_ratio_log": math.log1p(min(amount_ratio, 100)),
        "amount_deviation": min(amount_deviation, 20),
        "product_novel": float(product_novel),
        "product_history_share": products[tx.product_code] / len(baseline) if baseline else 0,
        "region_novel": float(region_novel),
        "region_missing": float(not tx.addr1),
        "new_region_days": float(region_days if region_novel else 0),
        "home_activity_continues": float(home_continues),
        "trip_candidate": float(trip_candidate),
        "identity_available": float(identity is not None),
        "device_marked_new": float(marked_new),
        "device_familiar": float(device_familiar),
        "device_baseline_available": float(bool(profiles)),
        "proxy_signal": float(proxy_signal),
        "recent_count_log": math.log1p(len(recent)),
        "online_48h_count_log": math.log1p(len(online)),
        "one_hour_count_log": math.log1p(len(one_hour)),
        "velocity_ratio": min(velocity_ratio, 20),
        "mixed_channels": float(mixed_channels),
        "testing_sequence": float(bool(testing)),
        "online_burst": float(burst),
        "new_device_and_behavior": float(marked_new and anomalous_behavior),
        "proxy_and_behavior": float(proxy_signal and anomalous_behavior),
        "takeover_candidate": float(takeover_candidate),
    }
    findings: list[Finding] = []
    missing: list[str] = ["merchant identity", "payment authorization/settlement status"]
    if amounts_known:
        findings.append(Finding(
            f"A pre-episode baseline contains {len(baseline)} transactions; median amount is USD {baseline_median / 100:.2f} and the flagged amount is {amount_ratio:.2f} times that median.",
            (flagged_id, *[t.transaction_id for t in baseline[-10:]]),
            "transaction_behavior", "supports" if unusual_amount else "contradicts",
        ))
    else:
        missing.append("sufficient pre-episode spending history")
    if unusual_product:
        findings.append(Finding("The product code is absent from the pre-episode baseline; it is not a merchant identifier.", (flagged_id,), "transaction_behavior", "supports"))
    if identity:
        if marked_new or proxy_signal:
            findings.append(Finding(
                f"Identity signals: device newness={identity.device_newness or 'unknown'}, proxy indicator={identity.proxy or 'unknown'}. These correlated identity signals count as one evidence group and do not alone establish fraud.",
                (flagged_id,), "device_identity", "supports" if marked_new and anomalous_behavior else "neutral",
            ))
        if device_familiar:
            supporting_ids = tuple(t.transaction_id for t in baseline if t.transaction_id in repository.identities and repository.identities[t.transaction_id].device_profile == profile)
            findings.append(Finding("The same device profile appears in pre-episode card history; this is a profile match, not proof of the same physical device.", (flagged_id, *supporting_ids[-10:]), "device_identity", "contradicts"))
    elif tx.channel == "online":
        missing.append("online identity record")
    if region_novel:
        findings.append(Finding(
            "The billing region is absent from the baseline. " + ("Activity in familiar regions continues in the same window." if home_continues else "The source does not establish a precise purchase location or customer travel."),
            (flagged_id,), "transaction_behavior", "supports" if home_continues else "neutral",
        ))
    if trip_candidate:
        findings.append(Finding("Purchases span multiple days in the new billing region without observed concurrent home-region activity; travel is a competing explanation.", tuple(t.transaction_id for t in region_transactions), "transaction_behavior", "contradicts"))

    episode = {tx.transaction_id: tx}
    pattern = "none"
    if testing:
        pattern = "card_testing"
        episode.update(testing)
        findings.append(Finding("At least three small online transactions within one hour precede a larger purchase; authorization status remains unknown.", tuple(testing), "transaction_behavior", "supports"))
    elif takeover_candidate:
        pattern = "account_takeover"
        episode.update({t.transaction_id: t for t in recent if t.channel == "online" or t.addr1 not in regions})
    elif tx.channel == "online" and (burst or unusual_amount or unusual_product):
        pattern = "card_not_present_new_device" if marked_new else "card_not_present_fraud"
        if burst:
            # Episode membership needs both a bounded burst and a shared feature.
            episode.update({t.transaction_id: t for t in online if t.product_code == tx.product_code or (
                profile and t.transaction_id in repository.identities and repository.identities[t.transaction_id].device_profile == profile
            )})
            findings.append(Finding("A 48-hour online burst exceeds the card's baseline and includes unusual amount, product, or channel behavior.", tuple(episode), "transaction_behavior", "supports"))
    elif tx.channel == "in_person" and region_novel and home_continues and not trip_candidate:
        pattern = "out_of_region_use"
        episode.update({t.transaction_id: t for t in region_transactions})
    return BehaviorAnalysis(features, pattern,
                            tuple(t.transaction_id for t in sorted(episode.values(), key=lambda t: (t.timestamp, t.transaction_id))),
                            tuple(findings), tuple(missing))
