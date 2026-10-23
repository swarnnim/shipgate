"""Sustainability module — SCI estimate for the post-code pipeline.

Methodology: the **Software Carbon Intensity (SCI)** specification from
the Green Software Foundation (v1.0, adopted as ISO/IEC 21031:2024):

    SCI = ((E × I) + M) / R

    E = energy consumed by the software (kWh)
    I = location-based carbon intensity of that energy (gCO2e/kWh)
    M = embodied emissions of the hardware, amortised (gCO2e)
    R = functional unit — here, **one merge request** taken from
        "code written" to "release-ready verdict" (R = 1 per MR).

We compute the SCI of that functional unit twice:

* **Automated** — a CI runner executes the ShipGate gauntlet.
* **Manual baseline** — an engineer performs the equivalent review,
  security checklist and test verification by hand at a workstation,
  and (as teams observe in practice) the pipeline runs more times
  because issues are found later.

Every constant below is a stated assumption with a source or a rationale,
and every one is overridable from the CLI. These are order-of-magnitude
estimates intended to make the trade-off visible and auditable — not
laboratory measurements. See the README's "Sustainability (SCI)" section
for the sensitivity discussion.

Sources
-------
* SCI formula and accounting rules: Green Software Foundation,
  "Software Carbon Intensity Specification" v1.0 (ISO/IEC 21031:2024),
  https://sci-guide.greensoftware.foundation/
* Grid intensity default 475 gCO2e/kWh: approximate global average
  electricity carbon intensity, Ember Global Electricity Review 2024
  (2023 data), as charted by Our World in Data. Override with your
  region's figure via ``--grid-intensity``.
* Embodied carbon magnitudes (laptop ~190 kgCO2e, server ~1,500 kgCO2e
  over the product life): typical manufacturer life-cycle assessments
  (e.g. Apple/Dell product environmental reports) put laptops in the
  ~150-350 kgCO2e range and 1U/2U servers in the ~1,000-2,000 kgCO2e
  range; we take mid-range round numbers and say so.
"""
from __future__ import annotations

# --- Grid -----------------------------------------------------------------
GRID_INTENSITY_DEFAULT = 475.0  # gCO2e/kWh, global average (see above)

# --- Automated pipeline (per MR) -----------------------------------------
CI_JOB_MINUTES = 4.0        # checkout + ShipGate + report upload, typical
RUNNER_POWER_W = 12.0       # effective per-job share of host power draw
PUE = 1.2                   # efficient cloud data centre overhead factor
AUTO_CI_RUNS_PER_MR = 1.15  # occasional re-runs after fixes
SERVER_EMBODIED_G = 1_500_000.0        # ~1,500 kgCO2e over the server's life
SERVER_LIFESPAN_HOURS = 4 * 365.25 * 24  # 4 years, always on
SERVER_SHARE = 1 / 16       # this job's vCPU share of the host

# --- Manual baseline (per MR) ---------------------------------------------
MANUAL_REVIEW_MINUTES = 40.0   # review + security checklist + test checks
WORKSTATION_POWER_W = 95.0     # laptop (~65 W) + external monitor (~30 W)
LAPTOP_EMBODIED_G = 190_000.0  # ~190 kgCO2e over the laptop's life
LAPTOP_LIFESPAN_HOURS = 4 * 1900  # 4 years x ~1,900 working hours/year
MANUAL_CI_RUNS_PER_MR = 2.0    # later feedback => more pipeline iterations


def server_embodied_g_per_hour() -> float:
    """Host embodied emissions per hour, before tenant sharing."""
    return SERVER_EMBODIED_G / SERVER_LIFESPAN_HOURS


def laptop_embodied_g_per_hour() -> float:
    return LAPTOP_EMBODIED_G / LAPTOP_LIFESPAN_HOURS


def emissions_for_run(duration_min: float, power_w: float, pue: float,
                      intensity_g_kwh: float,
                      embodied_g_per_hour: float) -> dict:
    """Emissions of one run of *duration_min* at *power_w* device draw.

    E uses facility energy (device energy x PUE); M amortises embodied
    emissions over the run's duration. Returns the SCI ingredients.
    """
    hours = duration_min / 60.0
    energy_kwh = (power_w / 1000.0) * hours * pue
    operational_g = energy_kwh * intensity_g_kwh
    embodied_g = embodied_g_per_hour * hours
    return {
        "duration_min": duration_min,
        "energy_kwh": energy_kwh,
        "operational_g": operational_g,
        "embodied_g": embodied_g,
        "total_g": operational_g + embodied_g,
    }


def estimate(grid_intensity: float = GRID_INTENSITY_DEFAULT,
             mrs_per_month: int = 200) -> dict:
    """SCI comparison for one MR (R = 1): automated gate vs manual process."""
    auto_run = emissions_for_run(
        CI_JOB_MINUTES, RUNNER_POWER_W, PUE, grid_intensity,
        server_embodied_g_per_hour() * SERVER_SHARE,
    )
    automated_total = auto_run["total_g"] * AUTO_CI_RUNS_PER_MR

    human = emissions_for_run(
        MANUAL_REVIEW_MINUTES, WORKSTATION_POWER_W, 1.0, grid_intensity,
        laptop_embodied_g_per_hour(),
    )
    manual_ci = auto_run["total_g"] * MANUAL_CI_RUNS_PER_MR
    manual_total = human["total_g"] + manual_ci

    saving_g = manual_total - automated_total
    saving_pct = (saving_g / manual_total * 100.0) if manual_total > 0 else 0.0
    mrs_per_year = mrs_per_month * 12

    return {
        "methodology": "SCI = ((E x I) + M) / R, R = 1 merge request "
                       "(Green Software Foundation SCI v1.0 / ISO/IEC 21031:2024)",
        "grid_intensity_g_per_kwh": grid_intensity,
        "automated": {
            "sci_g_per_mr": round(automated_total, 3),
            "runs_per_mr": AUTO_CI_RUNS_PER_MR,
            "single_run": {k: round(v, 5) for k, v in auto_run.items()},
        },
        "manual_baseline": {
            "sci_g_per_mr": round(manual_total, 3),
            "review_minutes": MANUAL_REVIEW_MINUTES,
            "human_emissions_g": round(human["total_g"], 3),
            "ci_runs_per_mr": MANUAL_CI_RUNS_PER_MR,
            "ci_emissions_g": round(manual_ci, 3),
        },
        "saving": {
            "g_per_mr": round(saving_g, 3),
            "pct": round(saving_pct, 1),
            "kg_per_year_at_volume": round(saving_g * mrs_per_year / 1000.0, 1),
            "mrs_per_month_assumed": mrs_per_month,
        },
    }
