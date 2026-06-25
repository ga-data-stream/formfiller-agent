from formfiller.config import load_profile


def test_every_profile_field_has_a_description():
    profile = load_profile("profile.yaml")
    missing = [f.name for f in profile if not f.description.strip()]
    assert missing == [], f"fields missing description: {missing}"


def test_addressing_disambiguation_is_documented():
    by_name = {f.name: f for f in load_profile("profile.yaml")}
    # the e-invoicing routing line must be documented as distinct from postal address
    al = by_name["addressing_line"].description.lower()
    assert "électronique" in al or "electronic" in al or "routing" in al
    ba = by_name["billing_address"].description.lower()
    assert "postal" in ba or "postale" in ba
