"""Booking, cancel and reschedule: propose -> patient reply -> confirm."""
from clinic.store import ClinicStore
from conftest import offer, patient_says, run, verified


def _propose(ctx, slot):
    r = run(ctx, "propose_booking", slot_id=slot)
    assert r.ok, r
    return r.data["token"]


def test_happy_path_booking_creates_exactly_one_appointment(ctx):
    verified(ctx)
    before = ctx.store.snapshot()
    slot = offer(ctx)
    token = _propose(ctx, slot)
    assert ctx.store.snapshot() == before  # proposing writes nothing
    patient_says(ctx)
    r = run(ctx, "confirm", token=token)
    assert r.ok and r.data["slot_id"] == slot
    changes = ClinicStore.diff(before, ctx.store.snapshot())
    assert len(changes) == 1 and changes[0].after["patient_id"] == "P001"


def test_cannot_book_a_slot_that_was_never_offered(ctx):
    verified(ctx)
    r = run(ctx, "propose_booking", slot_id="PR1-20261013-0900")  # real, free, guessable - but not offered
    assert r.code == "SLOT_NOT_OFFERED"


def test_search_rejects_past_and_far_future_dates(ctx):
    verified(ctx)
    assert run(ctx, "search_slots", date_from="2026-10-11", date_to="2026-10-13").code == "INVALID_DATE"
    assert run(ctx, "search_slots", date_from="2026-10-13", date_to="2026-12-31").code == "INVALID_DATE"
    assert run(ctx, "search_slots", date_from="2026-10-14", date_to="2026-10-13").code == "INVALID_DATE"


def test_search_never_offers_past_slots_today(ctx):
    verified(ctx)
    r = run(ctx, "search_slots", date_from="2026-10-12", date_to="2026-10-12")
    assert all("09:00 AM" not in s["when"] for s in r.data["slots"])  # now is 09:00


def test_confirm_without_proposal_is_rejected(ctx):
    verified(ctx)
    patient_says(ctx)
    assert run(ctx, "confirm", token="CONF-DEADBEEF").code == "TOKEN_INVALID"


def test_stale_token_is_rejected_after_a_newer_proposal(ctx):
    verified(ctx)
    r = run(ctx, "search_slots", date_from="2026-10-13", date_to="2026-10-13", specialty="general_medicine")
    a, b = r.data["slots"][0]["slot_id"], r.data["slots"][1]["slot_id"]
    old = _propose(ctx, a)
    _propose(ctx, b)
    patient_says(ctx)
    assert run(ctx, "confirm", token=old).code == "TOKEN_INVALID"


def test_confirm_in_the_same_turn_is_rejected(ctx):
    verified(ctx)
    token = _propose(ctx, offer(ctx))
    r = run(ctx, "confirm", token=token)
    assert r.code == "NO_NEW_PATIENT_MESSAGE" and ctx.store.appointments_for(["P001"])[0].id == "A0005"


def test_confirm_is_idempotent(ctx):
    verified(ctx)
    token = _propose(ctx, offer(ctx))
    patient_says(ctx)
    first = run(ctx, "confirm", token=token)
    second = run(ctx, "confirm", token=token)
    assert first.ok and second.ok and second.data["already_confirmed"]
    assert second.data["appointment_id"] == first.data["appointment_id"]
    assert len(ctx.store.appointments_for(["P001"])) == 2  # A0005 + exactly one new


def test_cancel_goes_through_propose_and_confirm(ctx):
    verified(ctx)  # Anita owns A0005
    r = run(ctx, "propose_cancel", appointment_id="A0005")
    assert r.ok and ctx.store.get_appointment("A0005").status == "booked"
    patient_says(ctx)
    assert run(ctx, "confirm", token=r.data["token"]).ok
    assert ctx.store.get_appointment("A0005").status == "cancelled"


def test_reschedule_goes_through_propose_and_confirm(ctx):
    verified(ctx)
    slot = offer(ctx, specialty="dermatology", day="2026-10-22")
    r = run(ctx, "propose_reschedule", appointment_id="A0005", new_slot_id=slot)
    assert r.ok and ctx.store.get_appointment("A0005").slot_id == "PR3-20261020-1000"
    patient_says(ctx)
    assert run(ctx, "confirm", token=r.data["token"]).ok
    assert ctx.store.get_appointment("A0005").slot_id == slot


def test_reschedule_to_unoffered_slot_is_rejected(ctx):
    verified(ctx)
    assert run(ctx, "propose_reschedule", appointment_id="A0005",
               new_slot_id="PR3-20261022-0900").code == "SLOT_NOT_OFFERED"


def test_dependent_booking_is_made_for_the_child(ctx):
    verified(ctx, "Priya Sharma", "1990-06-15")
    run(ctx, "act_for_dependent", full_name="Aarav Sharma", dob="2016-04-09")
    token = _propose(ctx, offer(ctx, specialty="pediatrics", day="2026-10-15"))
    patient_says(ctx)
    assert run(ctx, "confirm", token=token).data["patient"] == "Aarav Sharma"
