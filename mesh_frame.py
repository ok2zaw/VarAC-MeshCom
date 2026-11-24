"""Read-only MeshCom frame inspection, based on the pinned firmware decoder.

This does not decide whether a packet should be relayed or emailed. MeshCom
FCS is a byte sum, not the radio's CRC. Original RF bytes remain the evidence.
"""

FRAME_TYPES = {"msg": "Text", "pos": "Poloha", "hey": "HEY", "hn": "Sousedé HN",
               "ack": "RF ACK", "unknown": "Neznámý"}
DECODE_STATES = {"ok": "Rozebráno", "bad_fcs": "Chybný MeshCom FCS",
                 "malformed": "Neúplný nebo vadný rámec", "unsupported": "Nepodporovaný formát"}
DECODER_VERSION = 1


def _text(raw):
    return "".join(c if c.isprintable() or c in "\n\t" else f"\\u{ord(c):04x}"
                   for c in raw.decode("utf-8", errors="replace"))


def decode_frame(raw):
    """Return bounded diagnostic fields even when FCS/format is invalid."""
    out = {"frame_type": "unknown", "decode_status": "unsupported", "error": "",
           "source": None, "destination": None, "source_path": None, "destination_path": None,
           "heard_from": None, "msg_id": None, "acked_msg_id": None, "remaining_hops": None,
           "via_server": None, "fcs_ok": None, "payload_text": _text(raw)}
    if not raw or len(raw) > 255:
        return {**out, "decode_status": "malformed", "error": "Délka rámce mimo 1–255 bajtů."}
    tag = raw[0]
    if tag == 0x41:
        out["frame_type"] = "ack"
        if len(raw) < 12 or not raw[5] & 0x80 or (raw[5] & 0x7F) > 7:
            return {**out, "decode_status": "malformed", "error": "Neúplný nebo nepravděpodobný RF ACK."}
        out.update(decode_status="ok", msg_id=f"{int.from_bytes(raw[1:5], 'little'):08X}",
                   acked_msg_id=f"{int.from_bytes(raw[6:10], 'little'):08X}",
                   remaining_hops=raw[5] & 0x7F, payload_text="")
        return out  # This format contains no sender callsign or MeshCom FCS.
    if tag not in (0x3A, 0x21, 0x40):
        out["error"] = "Rámec není podporovaný MeshCom text, poloha, HEY ani ACK."
        return out
    out["frame_type"] = {0x3A: "msg", 0x21: "pos", 0x40: "hey"}[tag]
    if len(raw) < 16:
        return {**out, "decode_status": "malformed", "error": "Krátká MeshCom hlavička."}
    out.update(msg_id=f"{int.from_bytes(raw[1:5], 'little'):08X}",
               remaining_hops=raw[5] & 0x0F, via_server=bool(raw[5] & 0x80),
               track_path=bool(raw[5] & 0x40), app_offline=bool(raw[5] & 0x20),
               mesh=bool(raw[5] & 0x10))
    try:
        source_end = raw.index(b">", 6, min(len(raw), 126))
        dest_end = raw.index(bytes([tag]), source_end + 1, min(len(raw), source_end + 121))
        source_path = raw[6:source_end].decode("ascii")
        destination_path = raw[source_end + 1:dest_end].decode("ascii")
        for path in (source_path, destination_path):
            if not path or any(not 32 <= ord(c) <= 126 for c in path) or any(not p for p in path.split(",")):
                raise ValueError("invalid path")
        if any(len(p) > 20 for p in source_path.split(",")):
            raise ValueError("long callsign")
        end = raw.index(b"\0", dest_end + 1)
        if end + 5 > len(raw):
            raise ValueError("short trailer")
    except (ValueError, UnicodeError):
        return {**out, "decode_status": "malformed", "error": "Chybí platná cesta, ukončení payloadu nebo trailer."}
    out.update(source_path=source_path, source=source_path.split(",")[0],
               heard_from=source_path.split(",")[-1], destination_path=destination_path,
               destination=destination_path.split(",")[-1], payload_text=_text(raw[dest_end + 1:end]),
               hw_id=raw[end + 1], lora_mod=raw[end + 2] & 0x0F)
    if tag == 0x40 and out["destination"] == "HN":
        out["frame_type"] = "hn"
    # The sum covers the NUL, HW and modulation bytes; FCS itself is big endian.
    fcs_offset = end + 3
    out["fcs_ok"] = int.from_bytes(raw[fcs_offset:fcs_offset + 2], "big") == sum(raw[:fcs_offset])
    out["decode_status"] = "ok" if out["fcs_ok"] else "bad_fcs"
    if not out["fcs_ok"]:
        out["error"] = "Nesouhlasí MeshCom součet bajtů; nejde o údaj rádiové CRC."
    if fcs_offset + 2 < len(raw):
        out["firmware_byte"] = raw[fcs_offset + 2]
    if fcs_offset + 3 < len(raw):
        out["last_hw"] = raw[fcs_offset + 3]
    return out
