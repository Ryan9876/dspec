from backend.dspec_app.quality import review_spec
from tests.helpers import valid_spec

def test_valid_stage_can_cross_promotion_threshold():
    for stage in ("constitution", "requirements", "solution", "tasks"):
        review = review_spec(stage, valid_spec(stage))
        assert review.score >= 0.90, (stage, review)
        assert review.must_fix == []

def test_unfinished_marker_blocks_quality():
    text = valid_spec("solution") + "\nTODO: finish the database behavior"
    review = review_spec("solution", text)
    assert any(item["id"] == "ambiguous_placeholder" for item in review.must_fix)
    assert review.score < 0.90

def test_missing_error_contract_is_reported():
    text = "# Solution\n\n## Schema\n\n" + ("Typed schema integer string relationship. " * 130) + "\n## Recovery\nRollback recovery failure handling."
    review = review_spec("solution", text)
    assert any(item["id"] == "missing_API_error_contracts" for item in review.must_fix)
