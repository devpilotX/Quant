"""Event-study core (market model) — the Pillar 4 foundation.

DATA-AGNOSTIC by design: callers supply, per event, a security return series and
an aligned market return series with the event day flagged. The SAME engine then
serves every event sleeve (PEAD, index rebalancing, merger arb, corporate
actions, insider/bulk-deal flow) once that sleeve's ANNOUNCEMENT-dated event data
is wired. Nothing here assumes a particular event type — it only needs returns
and an event index, so it is unit-testable without any external data.

Model (per security i, OLS on the estimation window):
    R_it = alpha_i + beta_i * R_mt + eps_it
Abnormal / cumulative abnormal return over the event window [t1, t2]:
    AR_it = R_it - (alpha_i + beta_i * R_mt)
    CAR_i = sum_{t=t1..t2} AR_it
    CAAR  = (1/N) * sum_i CAR_i

Significance (two complementary tests, because Indian single-stock ARs are
fat-tailed and events inflate variance — a naive cross-sectional t over-rejects):
  - BMP (Boehmer, Musumeci & Poulsen 1991): standardize each CAR by its own
    Patell forecast-error-adjusted std, then take the CROSS-SECTIONAL t of those
    standardized CARs. Robust to event-induced variance inflation.
  - Corrado (1989) rank test: non-parametric, makes no normality assumption.
  - Generalized sign test (binomial) as a third, distribution-free check.

No look-ahead is STRUCTURAL: the estimation window must end strictly before the
event window begins (enforced in EventWindows, tested).

References:
  Boehmer, Musumeci & Poulsen (1991), J. Financial Economics 30, 253-272.
  Corrado (1989), J. Financial Economics 23, 385-395.
  MacKinlay (1997), "Event Studies in Economics and Finance", J. Econ. Lit. 35.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class MarketModel:
    alpha: float
    beta: float
    resid_std: float      # std of estimation-window residuals (dof-corrected)
    n_obs: int


@dataclass(frozen=True)
class EventWindows:
    """Relative-day windows around the event (day 0 = event/announcement day).

    Trading-day offsets, inclusive. The estimation window MUST end strictly
    before the event window starts — otherwise the model is fit on the very
    return it is meant to judge (look-ahead). Typical: est=(-250,-30),
    event=(-1,+1) for the announcement reaction or (+2,+60) for drift.
    """

    est: tuple[int, int] = (-250, -30)
    event: tuple[int, int] = (-1, 1)

    def __post_init__(self) -> None:
        if self.est[0] > self.est[1] or self.event[0] > self.event[1]:
            raise ValueError("window bounds must be (low <= high)")
        if self.est[1] >= self.event[0]:
            raise ValueError(
                "estimation window must END strictly before the event window "
                f"begins (no look-ahead): est={self.est} event={self.event}"
            )


@dataclass(frozen=True)
class Event:
    """One event observation.

    ``r_i``/``r_m`` are aligned daily returns over a window long enough to cover
    both the estimation and event windows; ``day0`` is the index of the event
    day within them. Callers slice nothing — the engine does, by the windows.
    """

    symbol: str
    r_i: np.ndarray
    r_m: np.ndarray
    day0: int


@dataclass(frozen=True)
class EventStudyResult:
    n_events: int
    caar: float                 # cross-sectional mean CAR over the event window
    car: np.ndarray             # per-event CAR
    scar: np.ndarray            # per-event standardized CAR (BMP)
    bmp_t: float                # BMP cross-sectional t-statistic
    bmp_p: float                # two-sided p
    rank_z: float               # Corrado (1989) rank-test z
    rank_p: float
    sign_p: float               # generalized sign test (binomial) two-sided p
    mean_beta: float
    frac_positive: float

    @property
    def significant(self) -> bool:
        """Both the parametric (BMP) and a non-parametric test agree at 5%."""
        return self.bmp_p < 0.05 and min(self.rank_p, self.sign_p) < 0.05


def fit_market_model(r_i: np.ndarray, r_m: np.ndarray) -> MarketModel:
    """OLS of security returns on market returns over the estimation window."""
    r_i = np.asarray(r_i, float)
    r_m = np.asarray(r_m, float)
    mask = np.isfinite(r_i) & np.isfinite(r_m)
    r_i, r_m = r_i[mask], r_m[mask]
    if len(r_i) < 2:
        raise ValueError("need >= 2 estimation observations")
    X = np.column_stack([np.ones_like(r_m), r_m])
    coef, *_ = np.linalg.lstsq(X, r_i, rcond=None)
    alpha, beta = float(coef[0]), float(coef[1])
    resid = r_i - (alpha + beta * r_m)
    dof = max(len(r_i) - 2, 1)
    resid_std = float(math.sqrt(float(np.sum(resid**2)) / dof))
    return MarketModel(alpha, beta, resid_std, len(r_i))


def abnormal_returns(r_i: np.ndarray, r_m: np.ndarray, model: MarketModel) -> np.ndarray:
    r_i = np.asarray(r_i, float)
    r_m = np.asarray(r_m, float)
    return r_i - (model.alpha + model.beta * r_m)


def run_event_study(
    events: list[Event],
    windows: EventWindows | None = None,
    *,
    min_est_obs: int = 120,
) -> EventStudyResult:
    """Run a market-model event study over a list of events.

    Events with insufficient/non-finite data in either window are skipped
    (reported via ``n_events`` = the count actually used). Returns NaN test
    statistics when fewer than two usable events remain.
    """
    w = windows or EventWindows()
    comb0, comb1 = w.est[0], w.event[1]          # full combined window, rel days
    n_comb = comb1 - comb0 + 1
    n_event = w.event[1] - w.event[0] + 1
    est_i0, est_i1 = 0, w.est[1] - w.est[0]      # estimation slice inside combined
    ev_i0, ev_i1 = w.event[0] - comb0, w.event[1] - comb0

    cars: list[float] = []
    scars: list[float] = []
    betas: list[float] = []
    k_accum = np.zeros(n_comb)                    # Corrado: sum of standardized ranks
    n_used = 0

    for ev in events:
        a, b = ev.day0 + comb0, ev.day0 + comb1
        if a < 0 or b >= len(ev.r_i) or len(ev.r_i) != len(ev.r_m):
            continue
        r_i = np.asarray(ev.r_i[a:b + 1], float)
        r_m = np.asarray(ev.r_m[a:b + 1], float)
        if len(r_i) != n_comb:
            continue
        ri_est, rm_est = r_i[est_i0:est_i1 + 1], r_m[est_i0:est_i1 + 1]
        finite = np.isfinite(ri_est) & np.isfinite(rm_est)
        if int(finite.sum()) < min_est_obs:
            continue
        model = fit_market_model(ri_est[finite], rm_est[finite])
        ar = r_i - (model.alpha + model.beta * r_m)   # AR over full combined window
        if not np.all(np.isfinite(ar)):
            continue
        car_i = float(np.sum(ar[ev_i0:ev_i1 + 1]))

        # Patell forecast-error variance of the CAR (estimation + prediction error):
        #   Var(CAR) = s^2 * [ L + L^2/T + (sum_event(R_m - Rbar_m))^2 / SS_m ]
        rbar = float(np.mean(rm_est[finite]))
        ss_m = float(np.sum((rm_est[finite] - rbar) ** 2))
        t_obs = int(finite.sum())
        sum_dev = float(np.sum(r_m[ev_i0:ev_i1 + 1] - rbar))
        infl = (n_event + n_event ** 2 / t_obs + (sum_dev ** 2) / ss_m) if ss_m > 0 else n_event
        sd_car = model.resid_std * math.sqrt(infl)
        if not (sd_car > 0 and math.isfinite(sd_car)):
            continue

        cars.append(car_i)
        scars.append(car_i / sd_car)
        betas.append(model.beta)
        k_accum += stats.rankdata(ar) - (n_comb + 1) / 2.0   # standardized ranks
        n_used += 1

    n = len(cars)
    if n < 2:
        nan = float("nan")
        return EventStudyResult(n, nan, np.array(cars), np.array(scars),
                                nan, nan, nan, nan, nan,
                                float(np.mean(betas)) if betas else nan, nan)

    car_arr = np.array(cars)
    scar_arr = np.array(scars)
    caar = float(np.mean(car_arr))

    # BMP: cross-sectional t of the standardized CARs (NOT the time-series std).
    scar_sd = float(np.std(scar_arr, ddof=1))
    bmp_t = math.sqrt(n) * float(np.mean(scar_arr)) / scar_sd if scar_sd > 0 else float("nan")
    bmp_p = float(2 * stats.t.sf(abs(bmp_t), n - 1)) if math.isfinite(bmp_t) else float("nan")

    # Corrado (1989) rank test over the combined window.
    kbar = k_accum / n
    s_k = math.sqrt(float(np.mean(kbar ** 2)))
    rank_z = (float(np.sum(kbar[ev_i0:ev_i1 + 1])) / (math.sqrt(n_event) * s_k)
              if s_k > 0 else float("nan"))
    rank_p = float(2 * stats.norm.sf(abs(rank_z))) if math.isfinite(rank_z) else float("nan")

    # Generalized sign test.
    n_pos = int(np.sum(car_arr > 0))
    sign_p = float(stats.binomtest(n_pos, n, 0.5).pvalue)

    return EventStudyResult(
        n_events=n, caar=caar, car=car_arr, scar=scar_arr,
        bmp_t=bmp_t, bmp_p=bmp_p, rank_z=rank_z, rank_p=rank_p, sign_p=sign_p,
        mean_beta=float(np.mean(betas)), frac_positive=n_pos / n,
    )
