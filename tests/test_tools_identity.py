"""Identity and authorization guards."""
from conftest import offer, patient_says, run, verified


def test_cannot_read_or_search_before_verification(ctx):
    for name, args in [("list_my_appointments", {}),
                       ("search_slots", {"date_from": "2026-10-13", "date_to": "2026-10-13"}),
                       ("propose_booking", {"slot_id": "PR1-20261013-0900"})]:
        r = run(ctx, name, **args)
        assert (r.ok, r.code) == (False, "NOT_VERIFIED"), name


def test_wrong_dob_and_wrong_name_give_identical_errors(ctx, make_ctx):
    wrong_dob = run(ctx, "verify_patient", full_name="Anita Rao", dob="1984-03-03")
    other = make_ctx()
    wrong_name = run(other, "verify_patient", full_name="Anita Roa", dob="1984-03-02")
    assert wrong_dob.code == wrong_name.code == "VERIFICATION_FAILED"
    assert wrong_dob.hint == wrong_name.hint  # nothing reveals which field was wrong
    assert wrong_dob.data is None


def test_three_failures_lock_even_correct_details(ctx):
    for _ in range(2):
        assert run(ctx, "verify_patient", full_name="Anita Rao", dob="1990-01-01").code == "VERIFICATION_FAILED"
    assert run(ctx, "verify_patient", full_name="Anita Rao", dob="1990-01-01").code == "LOCKED"
    r = run(ctx, "verify_patient", full_name="Anita Rao", dob="1984-03-02")
    assert (r.ok, r.code) == (False, "LOCKED")
    assert ctx.state.verified_patient_id is None


def test_name_matching_ignores_case_and_spacing(ctx):
    assert run(ctx, "verify_patient", full_name="  anita   RAO", dob="1984-03-02").ok


def test_bad_dob_format_is_not_counted_as_a_failure(ctx):
    r = run(ctx, "verify_patient", full_name="Anita Rao", dob="early March, 1984")  # "2nd March 1984" now parses
    assert r.code == "INVALID_ARGUMENT" and ctx.state.failed_verifications == 0


def test_cannot_switch_to_a_different_verified_patient(ctx):
    verified(ctx)
    r = run(ctx, "verify_patient", full_name="Vikram Nair", dob="1979-11-20")
    assert r.code == "ALREADY_VERIFIED" and ctx.state.verified_patient_id == "P001"


def test_linked_dependent_is_allowed(ctx):
    verified(ctx, "Priya Sharma", "1990-06-15")
    r = run(ctx, "act_for_dependent", full_name="Aarav Sharma", dob="2016-04-09")
    assert r.ok and ctx.state.subject_id == "P004"


def test_unrelated_or_unknown_person_gets_the_same_not_linked(ctx):
    verified(ctx, "Priya Sharma", "1990-06-15")
    unrelated = run(ctx, "act_for_dependent", full_name="Vikram Nair", dob="1979-11-20")
    unknown = run(ctx, "act_for_dependent", full_name="Nobody Here", dob="2000-01-01")
    assert unrelated.code == unknown.code == "NOT_LINKED"
    assert ctx.state.acting_for is None


def test_list_shows_only_own_and_dependents_appointments(ctx):
    verified(ctx, "Priya Sharma", "1990-06-15")
    ids = {a["appointment_id"] for a in run(ctx, "list_my_appointments").data["appointments"]}
    assert ids == {"A0002"}  # Aarav's; nobody else's


def test_cannot_cancel_someone_elses_appointment(ctx):
    verified(ctx)  # Anita; A0001 belongs to Vikram
    r = run(ctx, "propose_cancel", appointment_id="A0001")
    nonexistent = run(ctx, "propose_cancel", appointment_id="A9999")
    assert r.code == nonexistent.code == "APPOINTMENT_NOT_FOUND"


def test_cannot_reschedule_someone_elses_appointment(ctx):
    verified(ctx)
    slot = offer(ctx)
    assert run(ctx, "propose_reschedule", appointment_id="A0001", new_slot_id=slot).code == "APPOINTMENT_NOT_FOUND"


def test_unknown_tool_and_bad_args_are_structured_errors(ctx):
    assert run(ctx, "delete_all_records").code == "UNKNOWN_TOOL"
    from agent.tools import call_tool
    assert call_tool(ctx, "verify_patient", {"name": "x"}).code == "INVALID_ARGUMENT"
    # wrong types from the model become structured errors, not crashes
    assert call_tool(ctx, "verify_patient", {"full_name": None, "dob": "1984-03-02"}).code == "INTERNAL_ERROR"
