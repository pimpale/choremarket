import csv

from laboratory.domain import Type
from laboratory.evaluation import profile_results
from laboratory.integrated import EqualSplitFirstBest
from laboratory.synthetic import generate_profiles, quantize_profile, write_profiles_csv


def test_synthetic_profiles_are_seeded_nonnegative_and_contain_bids_and_wtp(tmp_path):
    left = generate_profiles(n=3, count=5, seed=7)
    right = generate_profiles(n=3, count=5, seed=7)
    assert left == right
    assert all(t.value >= 0 and t.cost >= 0 for sample in left for t in sample.types)

    path = tmp_path / "profiles.csv"
    write_profiles_csv(
        left,
        path,
        [0, 15, 30, 45],
        [0, 15, 30, 45],
        [0, 7.5, 15, 22.5, 30, 37.5, 45],
    )
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 15
    assert {
        "wtp_dollars",
        "bid_dollars",
        "grid_wtp",
        "grid_bid",
        "fine_demand_grid_wtp",
    } <= rows[0].keys()


def test_quantization_maps_to_the_actual_ui_report_domain():
    sample = generate_profiles(n=3, count=1)[0]
    quantized = quantize_profile(sample.types, [0, 15, 30], [0, 15, 30])
    assert {t.value for t in quantized} <= {0, 15, 30}
    assert {t.cost for t in quantized} <= {0, 15, 30}


def test_synthetic_welfare_can_be_scored_on_raw_types_after_rounding():
    raw = ((Type(7, 8), Type(7, 20), Type(7, 20)),)
    rounded = (quantize_profile(raw[0], [0, 15], [0, 15]),)
    row = profile_results([EqualSplitFirstBest()], rounded, true_profiles=raw)[0]

    assert row["first_best_welfare"] == 13
    assert row["reported_first_best_welfare"] == 0
    assert row["welfare"] == 0
    assert row["rounding_regret"] == 13
