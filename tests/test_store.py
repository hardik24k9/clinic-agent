from datetime import date, datetime, timedelta

import pytest

from clinic.clock import IST
from clinic.store import ClinicStore, StoreError


def test_seed_shape(store):
    assert len(store.patients) == 8
    assert len(store.providers) == 4
    assert {p.specialty for p in store.providers.values()} == {"general_medicine", "dermatology", "pediatrics"}
    assert [p.id for p in store.dependents_of("P003")] == ["P004"]
    days = {s.start.date() for s in store.slots.values()}
    assert min(days) >= date(2026, 10, 12) and max(days) <= date(2026, 10, 25)


def test_seed_is_deterministic(clock):
    a = ClinicStore.load_seed(clock=clock).snapshot()
    b = ClinicStore.load_seed(clock=clock).snapshot()
    assert a == b


def test_find_patient_needs_name_and_dob_and_ignores_case_spacing(store):
    assert store.find_patient("  anita   RAO ", date(1984, 3, 2)).id == "P001"
    assert store.find_patient("Anita Rao", date(1984, 3, 3)) is None
    # Same name, different people: DOB disambiguates.
    assert store.find_patient("Rohan Das", date(1965, 1, 30)).id == "P005"
    assert store.find_patient("Rohan Das", date(2001, 7, 14)).id == "P008"


def test_free_slots_exclude_booked_and_blocked(store):
    start = datetime(2026, 10, 12, tzinfo=IST)
    free = store.free_slots(start, start + timedelta(days=14))
    ids = {s.id for s in free}
    assert "PR1-20261014-1000" not in ids          # booked (A0001)
    assert "PR1-20261016-1100" in ids              # A0004 is cancelled -> slot is free again
    assert not any(s.id.startswith("PR3-20261015") for s in free)  # full day
    assert free == sorted(free, key=lambda s: (s.start, s.provider_id))


def test_free_slots_filter_by_specialty(store):
    start = datetime(2026, 10, 12, tzinfo=IST)
    derm = store.free_slots(start, start + timedelta(days=14), specialty="dermatology")
    assert derm and all(s.provider_id == "PR3" for s in derm)


def test_create_appointment_diff_is_exactly_one_creation(store):
    before = store.snapshot()
    appt = store.create_appointment("P007", "PR1-20261013-0900")
    changes = ClinicStore.diff(before, store.snapshot())
    assert len(changes) == 1
    c = changes[0]
    assert (c.entity, c.id, c.field, c.before) == ("appointment", appt.id, "*", None)
    assert c.after["slot_id"] == "PR1-20261013-0900"


def test_cannot_double_book_a_slot(store):
    store.create_appointment("P007", "PR1-20261013-0900")
    with pytest.raises(StoreError) as e:
        store.create_appointment("P001", "PR1-20261013-0900")
    assert e.value.code == "SLOT_UNAVAILABLE"


def test_cancel_frees_slot_and_diffs_status(store):
    before = store.snapshot()
    store.cancel_appointment("A0001")
    assert store.is_slot_free("PR1-20261014-1000")
    changes = ClinicStore.diff(before, store.snapshot())
    assert [(c.id, c.field, c.before, c.after) for c in changes] == [("A0001", "status", "booked", "cancelled")]
    with pytest.raises(StoreError) as e:
        store.cancel_appointment("A0001")
    assert e.value.code == "ALREADY_CANCELLED"


def test_move_appointment(store):
    before = store.snapshot()
    store.move_appointment("A0001", "PR1-20261014-1100")
    changes = ClinicStore.diff(before, store.snapshot())
    assert [(c.field, c.before, c.after) for c in changes] == [("slot_id", "PR1-20261014-1000", "PR1-20261014-1100")]
    assert store.is_slot_free("PR1-20261014-1000")


def test_snapshot_is_a_deep_copy(store):
    snap = store.snapshot()
    store.cancel_appointment("A0001")
    assert snap["appointments"]["A0001"]["status"] == "booked"


def test_block_slot_shows_in_diff(store):
    before = store.snapshot()
    store.block_slot("PR1-20261013-0900")
    assert not store.is_slot_free("PR1-20261013-0900")
    changes = ClinicStore.diff(before, store.snapshot())
    assert [(c.entity, c.field, c.after) for c in changes] == [("slot", "blocked", True)]
