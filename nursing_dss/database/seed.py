"""Idempotent demo data (same unit, rooms, coordinates and acuities as the
original notebook). Safe to run repeatedly: it never resets existing patients."""
from .repository import Database

DEMO_UNIT = "Medical Unit A - Floor 2"

ROOM_COORDS = {
    101: (10.0, 10.0), 102: (25.0, 10.0), 103: (40.0, 10.0),
    104: (10.0, 0.0),  105: (25.0, 0.0),  106: (40.0, 0.0),
}
SAMPLE_ACUITIES = [3, 2, 4, 1, 5, 2, 3, 4, 2, 1, 3, 5]

# (name, contact, qualification, max_workload). Alice is the CHARGE nurse so the
# system has someone to fall back to when a bed has no assigned nurse.
NURSE_ROSTER = [
    ("Alice Smith", "EXT-401", "CHARGE", 14),
    ("Bob Jones", "EXT-402", "RN", 12),
    ("Carol Vance", "EXT-403", "RN", 12),
    ("David Lee", "EXT-404", "RN", 12),
    ("Emma Watson", "EXT-405", "RPN", 10),
    ("Frank Miller", "EXT-406", "RPN", 10),
]


def seed_demo(db: Database) -> int:
    """Create the demo unit if missing and return its unit_id."""
    unit_id = db.get_or_create_unit(DEMO_UNIT)
    idx = 0
    for room_number, (x, y) in ROOM_COORDS.items():
        room_id = db.upsert_room(str(room_number), unit_id, x, y)
        for n, letter in enumerate(("A", "B"), start=1):
            bed_id = db.get_or_create_bed(letter, room_id)
            db.ensure_bell(room_number * 10 + n, bed_id)
            db.ensure_patient(bed_id, SAMPLE_ACUITIES[idx % len(SAMPLE_ACUITIES)])
            idx += 1
    for name, contact, qual, cap in NURSE_ROSTER:
        db.upsert_nurse_by_name(name, contact, qual, cap)
    return unit_id
