from app.plans import PLANS

def test_plan_limits_are_correct():
    assert PLANS["free"]["monthly_clips"] == 5
    assert PLANS["lite"]["monthly_clips"] == 20
    assert PLANS["pro"]["monthly_clips"] == 70

def test_plan_prices_are_correct():
    assert PLANS["free"]["price_pkr"] == 0
    assert PLANS["lite"]["price_pkr"] == 1500
    assert PLANS["pro"]["price_pkr"] == 7000
