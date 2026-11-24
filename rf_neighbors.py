"""Signal history of the last RF transmitter named in each received path."""

from collections import Counter
import json
import re

import common


def _median(histogram):
    count = sum(histogram.values())
    middle = ((count - 1) // 2, count // 2)
    found, offset = [], 0
    for value, n in sorted(histogram.items()):
        for index in middle:
            if offset <= index < offset + n:
                found.append(value)
        offset += n
    return sum(found) / 2


def receiver_nodes():
    conn = common.get_db()
    try:
        return [r[0] for r in conn.execute("SELECT DISTINCT node FROM rf_events ORDER BY node")]
    finally:
        conn.close()


def signal_history(node, since, until, selected=None, own_only=False, gap_seconds=900):
    """Exact summaries and bounded graph samples; raw archive remains unchanged.

    The earliest archived (receiver, boot, event_seq) is canonical even when a
    duplicate arrives in another time window. Repeated RF packets have NEW
    event_seq values and remain separate measurements.
    """
    node = node.upper()
    selected_set = None if selected is None else {s.upper() for s in selected}
    bucket_seconds = next((n for n in (60, 300, 900, 1800, 3600, 10800)
                           if (until - since) / n <= 360), 10800)
    groups = {}
    conn = common.get_db()
    try:
        rows = conn.execute(
            "SELECT e.id,e.received_at,e.boot_id,e.event_seq,e.rssi,e.snr,d.details_json "
            "FROM rf_events e JOIN rf_frame_details d ON d.event_id=e.id "
            "WHERE e.node=? AND e.received_at>=? AND e.received_at<? "
            "AND e.packet_type='rf_rx' AND d.decode_status='ok' AND e.event_seq IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM rf_events p WHERE p.node=e.node AND "
            "p.boot_id=e.boot_id AND p.event_seq=e.event_seq AND p.id<e.id) "
            "ORDER BY e.received_at,e.id", (node, since, until),
        )
        for row in rows:
            frame = json.loads(row["details_json"])
            if frame.get("fcs_ok") is not True:
                continue  # Includes ACKs: their frame format does not identify a transmitter.
            heard = (frame.get("heard_from") or "").upper()
            if (heard == node or not re.fullmatch(r"[A-Z0-9/-]{3,20}", heard)
                    or not any(c.isdigit() for c in heard) or not any(c.isalpha() for c in heard)):
                continue
            own_frame = (frame.get("source") or "").upper() == heard
            if own_only and not own_frame:
                continue
            rssi, snr = row["rssi"], row["snr"]
            if rssi is None or snr is None:
                continue
            if heard not in groups:
                groups[heard] = {"station": heard, "count": 0, "own_count": 0,
                                 "relay_count": 0, "rssi_hist": Counter(),
                                 "snr_hist": Counter(), "bins": {}, "segment": 0}
            group = groups[heard]
            if "last_at" in group and (row["boot_id"] != group["last_boot"] or row["received_at"] - group["last_at"] > gap_seconds):
                group["segment"] += 1
            group["count"] += 1
            group["own_count"] += int(own_frame)
            group["relay_count"] += int(not own_frame)
            group["rssi_hist"][rssi] += 1
            group["snr_hist"][snr] += 1
            group.update(last_at=row["received_at"], last_boot=row["boot_id"], last_rssi=rssi, last_snr=snr)
            if selected_set is not None and heard not in selected_set:
                continue
            point = {"t": row["received_at"], "rssi": rssi, "snr": snr,
                     "boot": row["boot_id"], "seq": row["event_seq"]}
            key = (row["boot_id"], int((row["received_at"] - since) // bucket_seconds), group["segment"])
            if key not in group["bins"]:
                group["bins"][key] = {"rssi": Counter(), "snr": Counter(),
                                      "first": point, "last": point,
                                      "rssi_min": point, "rssi_max": point,
                                      "snr_min": point, "snr_max": point}
            bucket = group["bins"][key]
            bucket["rssi"][rssi] += 1
            bucket["snr"][snr] += 1
            bucket["last"] = point
            for field, value in (("rssi", rssi), ("snr", snr)):
                if value < bucket[field + "_min"][field]: bucket[field + "_min"] = point
                if value > bucket[field + "_max"][field]: bucket[field + "_max"] = point
    finally:
        conn.close()

    ordered = sorted(groups.values(), key=lambda g: (-g["last_at"], g["station"]))
    selection = [g["station"] for g in ordered[:5]] if selected is None else [g["station"] for g in ordered if g["station"] in selected_set]
    summaries, series = [], []
    for group in ordered:
        summary = {k: group[k] for k in ("station", "count", "own_count", "relay_count", "last_at", "last_rssi", "last_snr")}
        for metric in ("rssi", "snr"):
            hist = group[metric + "_hist"]
            summary.update({metric + "_min": min(hist), metric + "_max": max(hist), metric + "_median": _median(hist)})
        summaries.append(summary)
        if group["station"] not in selection:
            continue
        points, trend = {}, []
        for bucket in group["bins"].values():
            for name in ("first", "last", "rssi_min", "rssi_max", "snr_min", "snr_max"):
                p = bucket[name]
                points[(p["boot"], p["seq"])] = p
            first, last = bucket["first"], bucket["last"]
            trend.append({"t": (first["t"] + last["t"]) / 2,
                          "first_t": first["t"], "last_t": last["t"], "boot": first["boot"],
                          "rssi": _median(bucket["rssi"]), "snr": _median(bucket["snr"])})
        samples = sorted(points.values(), key=lambda p: p["t"])
        series.append({"station": group["station"], "count": group["count"], "points": samples,
                       "sampled": len(samples) < group["count"], "trend": sorted(trend, key=lambda p: p["t"])})
    return {"node": node, "since": since, "until": until, "bucket_seconds": bucket_seconds,
            "selected": selection, "neighbors": summaries, "series": series,
            "sample_count": sum(g["count"] for g in ordered)}
