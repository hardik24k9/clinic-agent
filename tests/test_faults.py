import pytest

from clinic.faults import SLOT_TAKEN, TIMEOUT, FaultPlan


def test_no_faults_never_fire():
    plan = FaultPlan()
    assert [plan.check("search_slots") for _ in range(3)] == [None, None, None]
    assert plan.fired == []


def test_timeout_once_fires_only_on_first_call():
    plan = FaultPlan({"search_slots": "timeout_once"})
    assert [plan.check("search_slots") for _ in range(3)] == [TIMEOUT, None, None]
    assert [(f.tool, f.call_no) for f in plan.fired] == [("search_slots", 1)]


def test_timeout_always_fires_every_call():
    plan = FaultPlan({"confirm": "timeout_always"})
    assert [plan.check("confirm") for _ in range(3)] == [TIMEOUT] * 3


def test_slot_taken_once():
    plan = FaultPlan({"confirm": "slot_taken_once"})
    assert [plan.check("confirm") for _ in range(2)] == [SLOT_TAKEN, None]


def test_faults_are_per_tool():
    plan = FaultPlan({"confirm": "timeout_once"})
    assert plan.check("search_slots") is None
    assert plan.check("confirm") == TIMEOUT


@pytest.mark.parametrize("faults", [{"confirm": "explode"}, {"search_slots": "slot_taken_once"}])
def test_invalid_fault_config_is_rejected(faults):
    with pytest.raises(ValueError):
        FaultPlan(faults)
