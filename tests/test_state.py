from agent.state import PendingAction, SessionState


def test_turn_counter():
    s = SessionState()
    s.begin_patient_turn(); s.begin_patient_turn()
    assert s.turn == 2


def test_subject_defaults_to_verified_patient_and_switches_to_dependent():
    s = SessionState(verified_patient_id="P003")
    assert s.subject_id == "P003"
    s.acting_for = "P004"
    assert s.subject_id == "P004"


def test_tokens_are_deterministic_and_unique():
    a, b = SessionState(), SessionState()
    ta = [a.new_token() for _ in range(3)]
    assert ta == [b.new_token() for _ in range(3)]
    assert len(set(ta)) == 3 and all(t.startswith("CONF-") for t in ta)


def test_state_round_trips_as_json():
    s = SessionState(verified_patient_id="P001", offered_slots=["PR1-20261013-0900"])
    s.pending_action = PendingAction(kind="book", token="CONF-X", patient_id="P001",
                                     slot_id="PR1-20261013-0900", proposed_at_turn=2, summary="...")
    assert SessionState.model_validate_json(s.model_dump_json()) == s
