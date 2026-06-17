from risk.drawdown_guard import DrawdownGuard


def test_tiers():
    g = DrawdownGuard()
    assert g.evaluate(0.0)["mode"] == "normal"
    assert g.evaluate(11.0)["mode"] == "normal"
    assert g.evaluate(12.0)["mode"] == "defensive"
    assert g.evaluate(18.0)["mode"] == "block_new_trades"
    assert g.evaluate(25.0)["mode"] == "emergency_flatten"


def test_risk_multiplier():
    g = DrawdownGuard()
    assert g.evaluate(5.0)["risk_multiplier"] == 1.0
    assert g.evaluate(13.0)["risk_multiplier"] == 0.5
    assert g.evaluate(19.0)["risk_multiplier"] == 0.0


def test_can_open_and_flatten():
    g = DrawdownGuard()
    g.evaluate(13.0)
    assert g.can_open and not g.must_flatten
    g.evaluate(19.0)
    assert not g.can_open
    g.evaluate(26.0)
    assert g.must_flatten


def test_recovery_back_to_normal():
    g = DrawdownGuard()
    g.evaluate(19.0)
    res = g.evaluate(3.0)
    assert res["mode"] == "normal" and res["changed"]
