#!/usr/bin/env python3
"""Fit a supplied stellar-evolution grid to a Gaia cluster CMD, entirely offline."""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

STAR_FIELDS = (
    "source_id",
    "g",
    "bp",
    "rp",
    "g_error",
    "bp_error",
    "rp_error",
    "member_score",
    "selected",
    "exclusion_reason",
)
MODEL_FIELDS = (
    "model_id",
    "log_age",
    "mh",
    "av",
    "mini",
    "label",
    "g_abs",
    "bp_abs",
    "rp_abs",
)


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_artifact(path, fields, systems):
    """Read a CSV and its same-stem JSON; metadata is part of the input contract."""
    path = Path(path)
    metadata = json.loads(path.with_suffix(".json").read_text())
    if metadata.get("schema_version") != 1:
        raise ValueError(f"{path}: expected schema_version 1")
    if metadata.get("photometric_system") not in systems:
        raise ValueError(f"{path}: incompatible photometric_system")
    expected_hash = metadata.get("output_csv_sha256")
    if expected_hash is not None and expected_hash != sha256(path):
        raise ValueError(f"{path}: CSV hash does not match its metadata")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not set(fields) <= set(reader.fieldnames):
            raise ValueError(f"{path}: missing required columns {fields}")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"{path}: duplicate column names")
        rows = list(reader)
    if not rows or any(None in row for row in rows):
        raise ValueError(f"{path}: empty or malformed CSV")
    return rows, metadata


def load_stars(path):
    rows, metadata = read_artifact(path, STAR_FIELDS, {"Gaia DR3", "Gaia EDR3"})
    identifiers = set()
    for row in rows:
        ident = row["source_id"]
        if not ident or ident in identifiers:
            raise ValueError("Star source_id must be nonempty and unique")
        identifiers.add(ident)
        selected = row["selected"].lower()
        if selected not in {"true", "false", "1", "0"}:
            raise ValueError("selected must be true/false or 1/0")
        row["selected"] = selected in {"true", "1"}
        for name in STAR_FIELDS[1:8]:
            row[name] = float(row[name]) if row[name] else float("nan")
        if row["selected"]:
            if not all(math.isfinite(row[name]) for name in STAR_FIELDS[1:8]):
                raise ValueError(f"Selected star {ident} has missing/nonfinite photometry or score")
            if any(row[name] <= 0 for name in ("g_error", "bp_error", "rp_error")):
                raise ValueError(f"Selected star {ident} must have positive magnitude errors")
            if not 0 <= row["member_score"] <= 1:
                raise ValueError(f"Selected star {ident} has membership score outside [0, 1]")
        elif not row["exclusion_reason"]:
            raise ValueError(f"Excluded star {ident} must have an exclusion_reason")
    return rows, metadata


def load_models(path):
    rows, metadata = read_artifact(path, MODEL_FIELDS, {"Gaia EDR3 (Vega)"})
    models = []
    seen = set()
    for row in rows:
        for name in MODEL_FIELDS[1:]:
            row[name] = float(row[name])
            if not math.isfinite(row[name]):
                raise ValueError("Model values must be finite")
        if row["mini"] <= 0 or row["av"] < 0 or row["label"] != int(row["label"]):
            raise ValueError("Model initial mass must be positive; A_V nonnegative; label integral")
        row["label"] = int(row["label"])
        ident = row["model_id"]
        if not ident:
            raise ValueError("Model model_id must be nonempty")
        if not models or models[-1]["model_id"] != ident:
            if ident in seen:
                raise ValueError("Each model_id must occupy one contiguous CSV block")
            seen.add(ident)
            models.append({name: row[name] for name in MODEL_FIELDS[:4]} | {"rows": []})
        model = models[-1]
        if any(row[name] != model[name] for name in ("log_age", "mh", "av")):
            raise ValueError("Each model_id must have one age, metallicity and extinction")
        if model["rows"] and row["mini"] < model["rows"][-1]["mini"]:
            raise ValueError("Model rows must preserve nondecreasing initial-mass ordering")
        model["rows"].append(row)
    coordinates = {(m["log_age"], m["mh"], m["av"]) for m in models}
    if len(coordinates) != len(models):
        raise ValueError("Duplicate age/metallicity/extinction models")
    return models, metadata


def values(spec):
    """Parse one value or inclusive min,max,step; reject silently truncated grids."""
    parts = [float(part) for part in spec.split(",")]
    if not all(math.isfinite(part) for part in parts):
        raise ValueError("Grid values must be finite")
    if len(parts) == 1:
        return parts
    if len(parts) != 3 or parts[2] <= 0 or parts[1] < parts[0]:
        raise ValueError("Expected value or min,max,positive_step")
    count = round((parts[1] - parts[0]) / parts[2])
    if count > 10000 or not math.isclose(parts[0] + count * parts[2], parts[1], abs_tol=1e-8):
        raise ValueError("Grid maximum must lie on the specified step (at most 10001 values)")
    return [parts[0] + index * parts[2] for index in range(count + 1)]


def pair(spec):
    result = tuple(float(part) for part in spec.split(","))
    if len(result) != 2 or not all(map(math.isfinite, result)) or result[0] >= result[1]:
        raise ValueError("Expected finite min,max with min < max")
    return result


def build_segments(rows, phases=(1, 2, 3), floor_color=0.02, floor_g=0.03):
    """Build the exact retained polyline, subdivided only to accelerate spatial search."""
    import numpy as np
    from scipy.spatial import cKDTree

    scale = np.array([floor_color, floor_g], dtype=float)
    if not np.all(np.isfinite(scale)) or np.any(scale <= 0):
        raise ValueError("Both metric floors must be positive and finite")
    starts, ends = [], []
    for left, right in zip(rows[:-1], rows[1:], strict=True):
        if left["label"] not in phases or right["label"] not in phases:
            continue
        if abs(right["label"] - left["label"]) > 1:
            continue
        if "row_index" in left and int(right["row_index"]) != int(left["row_index"]) + 1:
            continue
        a = np.array([left["bp_abs"] - left["rp_abs"], left["g_abs"]])
        b = np.array([right["bp_abs"] - right["rp_abs"], right["g_abs"]])
        length = float(np.linalg.norm((b - a) / scale))
        if length == 0:
            continue
        n = max(1, math.ceil(length / 0.5))
        if n > 100000:
            raise ValueError("Implausibly long model segment; check magnitudes and units")
        fractions = np.arange(n + 1)[:, None] / n
        knots = a + fractions * (b - a)
        starts.extend(knots[:-1])
        ends.extend(knots[1:])
    if not starts:
        raise ValueError("An isochrone has no contiguous segments in the requested phases")
    starts, ends = np.asarray(starts), np.asarray(ends)
    mids = (starts + ends) / (2 * scale)
    radius = float(np.max(np.linalg.norm((ends - starts) / (2 * scale), axis=1)))
    return {"starts": starts, "ends": ends, "scale": scale, "tree": cKDTree(mids), "radius": radius}


def segment_costs(points, errors, segments, huber=2.0):
    """Return exact nearest-polyline Huber costs, metric radii, and closest CMD points.

    points/errors are (N, 2), ordered (BP-RP, G); errors include the configured floors.
    Model and star points must already be in the same distance-modulus frame.
    """
    import numpy as np

    points, errors = np.asarray(points, dtype=float), np.asarray(errors, dtype=float)
    if (
        points.ndim != 2
        or points.shape[1] != 2
        or errors.shape != points.shape
        or not np.all(np.isfinite(points))
        or not np.all(np.isfinite(errors))
        or np.any(errors <= 0)
        or not math.isfinite(huber)
        or huber <= 0
    ):
        raise ValueError("Expected finite N-by-2 points and positive errors/Huber threshold")
    a, b, scale = segments["starts"], segments["ends"], segments["scale"]
    _, initial = segments["tree"].query(points / scale)

    def project(star_indices, segment_indices):
        left = a[segment_indices]
        delta = b[segment_indices] - left
        inv_var = 1 / errors[star_indices] ** 2
        offset = points[star_indices] - left
        fraction = np.clip(
            np.sum(offset * delta * inv_var, axis=1) / np.sum(delta * delta * inv_var, axis=1), 0, 1
        )
        matched = left + fraction[:, None] * delta
        squared = np.sum((points[star_indices] - matched) ** 2 * inv_var, axis=1)
        return squared, matched

    indices = np.arange(len(points))
    upper, _ = project(indices, initial)
    # Any closer segment's midpoint is inside this radius by the triangle inequality.
    radii = np.sqrt(upper) * np.max(errors / scale, axis=1) + segments["radius"] + 1e-10
    candidates = segments["tree"].query_ball_point(points / scale, radii)
    lengths = np.fromiter(map(len, candidates), dtype=int, count=len(points))
    star_indices = np.repeat(indices, lengths)
    segment_indices = np.concatenate(candidates).astype(int)
    squared, matched = project(star_indices, segment_indices)
    boundaries = np.r_[0, np.cumsum(lengths)]
    chosen = np.array(
        [boundaries[i] + np.argmin(squared[boundaries[i] : boundaries[i + 1]]) for i in indices]
    )
    distance = np.sqrt(squared[chosen])
    costs = np.where(distance <= huber, 0.5 * distance**2, huber * (distance - 0.5 * huber))
    return costs, distance, matched[chosen]


def fit_grid(
    stars, models, distance_moduli, *, phases=(1, 2, 3), floor_g=0.03, floor_color=0.02, huber=2.0
):
    """Return parameter dictionaries and a float64 [grid point, star] cost matrix."""
    import numpy as np

    points = np.array([[s["bp"] - s["rp"], s["g"]] for s in stars])
    errors = np.array(
        [
            [
                math.sqrt(s["bp_error"] ** 2 + s["rp_error"] ** 2 + floor_color**2),
                math.hypot(s["g_error"], floor_g),
            ]
            for s in stars
        ]
    )
    if len(stars) < 5 or not models or not distance_moduli:
        raise ValueError("Fitting requires at least five stars and a nonempty model/distance grid")
    if len(models) * len(distance_moduli) * len(stars) > 100_000_000:
        raise ValueError("Cost matrix exceeds 100 million elements; use a bounded grid")
    parameters, costs = [], []
    for model in models:
        segments = build_segments(model["rows"], phases, floor_color, floor_g)
        for mu in distance_moduli:
            cost, _, _ = segment_costs(points - [0, mu], errors, segments, huber)
            parameters.append(
                {key: model[key] for key in ("model_id", "log_age", "mh", "av")}
                | {"distance_modulus": mu}
            )
            costs.append(cost)
    return parameters, np.array(costs), points, errors


def bootstrap_grid(costs, parameters, repeats=100, seed=2026):
    """Conditional resampling of selected stars; never a posterior or total error."""
    import numpy as np

    rng = np.random.default_rng(seed)
    best_indices = []
    for _ in range(repeats):
        counts = rng.multinomial(costs.shape[1], np.full(costs.shape[1], 1 / costs.shape[1]))
        best_indices.append(int(np.argmin(costs @ counts)))
    samples = [parameters[index] for index in best_indices]
    quantiles = {}
    for key in ("log_age", "mh", "av", "distance_modulus"):
        quantiles[key] = np.quantile(
            [sample[key] for sample in samples], [0.16, 0.5, 0.84]
        ).tolist()
    quantiles["age_gyr"] = (10 ** np.array(quantiles["log_age"]) / 1e9).tolist()
    return {
        "replicates": repeats,
        "seed": seed,
        "quantile_probabilities": [0.16, 0.5, 0.84],
        "quantiles": quantiles,
        "best_indices": best_indices,
    }


def write_csv(path, rows, fields=None):
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def make_plots(output, all_stars, selected, segments, best, profile, g_range, color_range):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle

    selected_ids = {star["source_id"] for star in selected}
    other = [star for star in all_stars if star["source_id"] not in selected_ids]
    selected_colors = [star["bp"] - star["rp"] for star in selected]
    window_c = color_range or (min(selected_colors) - 0.05, max(selected_colors) + 0.05)
    # Join adjacent display segments so dash patterns continue along the curve.
    # Retain breaks: no line is drawn across a removed evolutionary phase.
    paths = []
    for a, b in zip(segments["starts"], segments["ends"], strict=True):
        if paths and np.allclose(paths[-1][-1], a, rtol=0, atol=1e-12):
            paths[-1].append(b)
        else:
            paths.append([a, b])
    lines = [np.asarray(path) + [0, best["distance_modulus"]] for path in paths]
    binary = [line - [0, 2.5 * math.log10(2)] for line in lines]
    finite_g = [s["g"] for s in all_stars if math.isfinite(s["g"])]
    finite_c = [
        s["bp"] - s["rp"] for s in all_stars if math.isfinite(s["bp"]) and math.isfinite(s["rp"])
    ]
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 7.2), layout="constrained")
    for index, ax in enumerate(axes):
        ax.scatter(
            [s["bp"] - s["rp"] for s in other],
            [s["g"] for s in other],
            c="0.75",
            s=8,
            alpha=0.5,
            label=f"Excluded ({len(other)})",
            rasterized=True,
        )
        ax.scatter(
            selected_colors,
            [s["g"] for s in selected],
            c="#176484",
            s=15,
            alpha=0.8,
            label=f"Fitted ({len(selected)})",
            rasterized=True,
        )
        ax.add_collection(
            LineCollection(
                lines,
                colors="#c3412d",
                linewidths=1.6,
                label=f"Best grid age {best['age_gyr']:.3g} Gyr",
            )
        )
        ax.add_collection(
            LineCollection(
                binary,
                colors="#c3412d",
                linewidths=0.8,
                linestyles="dashed",
                alpha=0.6,
                label="Equal-mass binary diagnostic (not fitted)",
            )
        )
        if index == 0:
            ax.set(
                ylim=(max(finite_g) + 0.25, min(finite_g) - 0.3),
                xlim=(min(finite_c) - 0.1, max(finite_c) + 0.1),
                title="Complete input sample",
            )
            ax.add_patch(
                Rectangle(
                    (window_c[0], g_range[0]),
                    window_c[1] - window_c[0],
                    g_range[1] - g_range[0],
                    fill=False,
                    edgecolor="#176484",
                    linestyle=":",
                    linewidth=1.3,
                    label="Fit window" if color_range else "G cut + selected-color extent",
                )
            )
            ax.legend(fontsize=8)
        else:
            ax.set(
                ylim=(g_range[1] + 0.1, g_range[0] - 0.1),
                xlim=(window_c[0] - 0.03, window_c[1] + 0.03),
                title="Fit region",
            )
        ax.set(xlabel="Gaia BP − RP (mag)", ylabel="Gaia G (mag)")
        ax.grid(alpha=0.15)
    fig.suptitle("Stellar-evolution isochrone fit · conditional on supplied grid", fontsize=12)
    fig.savefig(output / "cmd.png", dpi=180)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(7.4, 4.4), layout="constrained")
    ax.plot([p["age_gyr"] for p in profile], [p["delta_score"] for p in profile], "o-", ms=3)
    ax.axvline(best["age_gyr"], color="#c3412d", alpha=0.7)
    ax.set(
        xlabel="Age (Gyr)",
        ylabel="Profile robust objective − minimum",
        title="Age profile · no likelihood or confidence interpretation",
    )
    ax.grid(alpha=0.2)
    fig.savefig(output / "age-profile.png", dpi=180)
    plt.close(fig)


def run(args):
    import numpy as np

    stars, star_metadata = load_stars(args.stars)
    models, model_metadata = load_models(args.models)
    if not math.isfinite(args.floor_g) or not math.isfinite(args.floor_color):
        raise ValueError("Metric floors must be finite")
    if min(args.floor_g, args.floor_color, args.huber) <= 0 or args.bootstrap < 0:
        raise ValueError("Floors/Huber threshold must be positive; bootstrap nonnegative")
    g_range = pair(args.g_range)
    color_range = pair(args.color_range) if args.color_range else None
    for star in stars:
        if not star["selected"]:
            continue
        reason = []
        if not g_range[0] <= star["g"] <= g_range[1]:
            reason.append("outside_fit_g_range")
        if color_range and not color_range[0] <= star["bp"] - star["rp"] <= color_range[1]:
            reason.append("outside_fit_color_range")
        if reason:
            star["selected"] = False
            star["exclusion_reason"] = ";".join(reason)
    selected = [star for star in stars if star["selected"]]
    for key in ("log_age", "mh", "av"):
        spec = getattr(args, key)
        if spec:
            allowed = values(spec)
            missing = [
                value
                for value in allowed
                if not any(math.isclose(model[key], value, abs_tol=1e-7) for model in models)
            ]
            if missing:
                raise ValueError(f"Requested {key} values absent from model grid: {missing}")
            models = [
                model
                for model in models
                if any(math.isclose(model[key], value, abs_tol=1e-7) for value in allowed)
            ]
    if len({model["log_age"] for model in models}) < 3:
        raise ValueError("An age fit requires at least three distinct model ages")
    phases = tuple(int(part) for part in args.phases.split(","))
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        raise ValueError("Output path already exists; choose a new directory")
    if output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("Output must be outside the installed skill directory")
    parameters, costs, points, errors = fit_grid(
        selected,
        models,
        values(args.distance_modulus),
        phases=phases,
        floor_g=args.floor_g,
        floor_color=args.floor_color,
        huber=args.huber,
    )
    scores = costs.sum(axis=1)
    best_index = int(np.argmin(scores))
    best = parameters[best_index] | {
        "score": float(scores[best_index]),
        "age_gyr": 10 ** parameters[best_index]["log_age"] / 1e9,
    }
    profile = []
    for age in sorted({p["log_age"] for p in parameters}):
        score = min(float(scores[i]) for i, p in enumerate(parameters) if p["log_age"] == age)
        profile.append(
            {
                "log_age": age,
                "age_gyr": 10**age / 1e9,
                "score": score,
                "delta_score": score - best["score"],
            }
        )
    warnings = [
        "Robust geometric fit: scores are not a normalized likelihood or posterior.",
        "Conditional bootstrap excludes stellar-model, calibration, membership "
        "and nuisance assumptions.",
        "Unresolved binaries, blue stragglers and field contamination are not population-modelled.",
        "Metric floors describe fitting tolerance, not additional measured "
        "photometric uncertainty.",
    ]
    for key in ("log_age", "mh", "av", "distance_modulus"):
        grid = sorted({p[key] for p in parameters})
        if len(grid) == 1:
            warnings.append(f"{key} is fixed at {grid[0]}; result is conditional on that value.")
        elif best[key] in (grid[0], grid[-1]):
            warnings.append(f"Best {key} reaches the tested grid boundary; expand or justify it.")
    if max(p["delta_score"] for p in profile) / len(selected) < 0.1:
        warnings.append("Age profile is nearly flat per star; selected CMD may not constrain age.")
    model = next(model for model in models if model["model_id"] == best["model_id"])
    segments = build_segments(model["rows"], phases, args.floor_color, args.floor_g)
    _, distance, closest = segment_costs(
        points - [0, best["distance_modulus"]], errors, segments, args.huber
    )
    residuals = []
    for star, radius, match in zip(selected, distance, closest, strict=True):
        residuals.append(
            {
                "source_id": star["source_id"],
                "metric_radius": float(radius),
                "color_residual": star["bp"] - star["rp"] - float(match[0]),
                "g_residual": star["g"] - best["distance_modulus"] - float(match[1]),
                "huber_linear_regime": bool(radius > args.huber),
            }
        )
    if float(np.mean(distance > args.huber)) > 0.25:
        warnings.append(
            "More than 25% of fitted stars lie in the Huber linear regime; inspect mismatch."
        )
    bootstrap = (
        bootstrap_grid(costs, parameters, args.bootstrap, args.seed) if args.bootstrap else None
    )
    if bootstrap and len({parameters[i]["log_age"] for i in bootstrap["best_indices"]}) == 1:
        warnings.append(
            "Every bootstrap selected one grid age; finite grid resolution "
            "does not imply zero age uncertainty."
        )
    age_values = sorted({model["log_age"] for model in models})
    report = {
        "schema_version": 1,
        "method": "exact-polyline-huber-profile-grid",
        "created_utc": datetime.now(UTC).isoformat(),
        "configuration": vars(args),
        "runtime": {
            "python": platform.python_version(),
            "packages": {
                name: importlib.metadata.version(name) for name in ("numpy", "scipy", "matplotlib")
            },
            "helper_path": str(Path(__file__).resolve()),
            "helper_sha256": sha256(__file__),
        },
        "age_grid": {
            "log_age_values": age_values,
            "adjacent_log_age_steps": np.diff(age_values).tolist(),
        },
        "inputs": {
            name: {
                "path": str(Path(path).resolve()),
                "csv_sha256": sha256(path),
                "metadata_sha256": sha256(Path(path).with_suffix(".json")),
                "metadata": meta,
            }
            for name, path, meta in (
                ("stars", args.stars, star_metadata),
                ("models", args.models, model_metadata),
            )
        },
        "counts": {
            "input_stars": len(stars),
            "fitted_stars": len(selected),
            "isochrones": len(models),
            "grid_points": len(parameters),
        },
        "best_fit": best,
        "conditional_bootstrap": bootstrap,
        "warnings": warnings,
    }
    output.mkdir(parents=True)
    write_csv(
        output / "score-grid.csv",
        [p | {"score": float(score)} for p, score in zip(parameters, scores, strict=True)],
    )
    write_csv(output / "age-profile.csv", profile)
    write_csv(output / "stars.csv", stars, STAR_FIELDS)
    write_csv(output / "residuals.csv", residuals)
    write_csv(output / "best-model.csv", model["rows"], MODEL_FIELDS)
    write_csv(
        output / "best-segments.csv",
        [
            {
                "color_start": float(a[0]),
                "g_abs_start": float(a[1]),
                "color_end": float(b[0]),
                "g_abs_end": float(b[1]),
            }
            for a, b in zip(segments["starts"], segments["ends"], strict=True)
        ],
    )
    np.savez_compressed(
        output / "star-costs.npz",
        costs=costs,
        source_id=np.array([star["source_id"] for star in selected]),
    )
    make_plots(output, stars, selected, segments, best, profile, g_range, color_range)
    report["artifacts"] = {
        path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
        for path in sorted(output.iterdir())
    }
    (output / "fit.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "output": str(output.resolve()),
                "best_fit": best,
                "fitted_stars": len(selected),
                "warnings": warnings,
            },
            indent=2,
        )
    )
    return report


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--stars", required=True, help="Standardized CSV with same-stem JSON")
    result.add_argument(
        "--models", required=True, help="Model CSV with same-stem JSON; already reddened"
    )
    result.add_argument("--output", required=True, help="New output directory (must not exist)")
    result.add_argument(
        "--distance-modulus", required=True, help="True modulus: value or min,max,step"
    )
    result.add_argument(
        "--g-range", required=True, help="Observed fit-selection min,max in G magnitudes"
    )
    result.add_argument("--color-range", help="Optional observed BP-RP min,max")
    result.add_argument("--log-age", help="Keep grid log10(age/year): value or min,max,step")
    result.add_argument("--mh", help="Keep grid [M/H]: value or min,max,step (negative: --mh=-0.1)")
    result.add_argument("--av", help="Keep grid A_V: value or min,max,step")
    result.add_argument("--phases", default="1,2,3", help="Retained PARSEC phase labels")
    result.add_argument("--floor-g", type=float, default=0.03)
    result.add_argument("--floor-color", type=float, default=0.02)
    result.add_argument("--huber", type=float, default=2.0)
    result.add_argument("--bootstrap", type=int, default=100)
    result.add_argument("--seed", type=int, default=2026)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        run(args)
    except (ValueError, OSError, KeyError, ImportError, json.JSONDecodeError) as error:
        print(f"fit_cmd: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
