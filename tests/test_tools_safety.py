"""Emergency lock, escalation, and failure handling (faults)."""
from clinic.store import ClinicStore
from conftest import offer, patient_says, run, verified


def test_emergency_blocks_every_write_but_allows_escalation(ctx):
    verified(ctx)
    slot = offer(ctx)
    token = run(ctx, "propose_booking", slot_id=slot).data["token"]
    ctx.state.emergency = True  # normally set by the guard
    patient_says(ctx)
    before = ctx.store.snapshot()
    for name, args in [("propose_booking", {"slot_id": slot}), ("confirm", {"token": token}),
                       ("propose_cancel", {"appointment_id": "A0005"}),
                       ("propose_reschedule", {"appointment_id": "A0005", "new_slot_id": slot})]:
        assert run(ctx, name, **args).code == "EMERGENCY_LOCK", name
    assert ctx.store.snapshot() == before
    assert run(ctx, "escalate_to_human", reason="chest pain", urgency="emergency").ok


def test_after_escalation_every_tool_refuses(ctx):
    verified(ctx)
    run(ctx, "escalate_to_human", reason="patient asked for a human")
    assert run(ctx, "list_my_appointments").code == "ESCALATED"
    assert run(ctx, "verify_patient", full_name="Anita Rao", dob="1984-03-02").code == "ESCALATED"
    assert run(ctx, "escalate_to_human", reason="again").data["first"] is False  # idempotent


def test_slot_taken_at_confirm_changes_nothing_but_the_blocked_slot(make_ctx):
    ctx = make_ctx({"confirm": "slot_taken_once"})
    verified(ctx)
    slot = offer(ctx)
    token = run(ctx, "propose_booking", slot_id=slot).data["token"]
    patient_says(ctx)
    before = ctx.store.snapshot()
    r = run(ctx, "confirm", token=token)
    assert (r.ok, r.code) == (False, "SLOT_TAKEN")
    changes = ClinicStore.diff(before, ctx.store.snapshot())
    assert [(c.entity, c.field) for c in changes] == [("slot", "blocked")]  # no appointment created
    assert slot not in ctx.state.offered_slots and ctx.state.pending_action is None


def test_timeout_on_confirm_writes_nothing_and_retry_succeeds_once(make_ctx):
    ctx = make_ctx({"confirm": "timeout_once"})
    verified(ctx)
    token = run(ctx, "propose_booking", slot_id=offer(ctx)).data["token"]
    patient_says(ctx)
    before = ctx.store.snapshot()
    assert run(ctx, "confirm", token=token).code == "TIMEOUT"
    assert ctx.store.snapshot() == before
    assert run(ctx, "confirm", token=token).ok
    assert run(ctx, "confirm", token=token).data["already_confirmed"]
    assert len(ClinicStore.diff(before, ctx.store.snapshot())) == 1


def test_search_timeout_is_structured(make_ctx):
    ctx = make_ctx({"search_slots": "timeout_once"})
    verified(ctx)
    r = run(ctx, "search_slots", date_from="2026-10-13", date_to="2026-10-13")
    assert r.code == "TIMEOUT" and ctx.state.offered_slots == []
