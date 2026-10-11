from evals.scenario import load_scenarios


def test_fourteen_scenarios_ten_train_four_heldout(store):
    scns = load_scenarios()
    assert len(scns) == 14 and len({s.id for s in scns}) == 14
    assert sum(s.split == "train" for s in scns) == 10 and sum(s.split == "heldout" for s in scns) == 4
    assert {s.category for s in scns if s.split == "heldout"} == {"happy_path", "safety", "identity", "robustness"}


def test_every_scenario_is_consistent_with_the_seed(store):
    for s in load_scenarios():
        assert s.validate_against(store) == [], s.id


def test_every_referenced_check_exists():
    from evals.graders.judge import CRITERIA
    from evals.graders.trace import CHECKS
    for s in load_scenarios():
        assert set(s.expect.trace) <= set(CHECKS), s.id
        assert set(s.expect.judge) <= set(CRITERIA), s.id
