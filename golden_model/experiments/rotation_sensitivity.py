"""How badly does pure-translation phase correlation degrade under field rotation?

For each rotation angle: rotate the reference field, apply a known translation,
then (a) recover the translation with phase correlation and (b) estimate the
rotation via Fourier-Mellin. We log the translation error, the phase-only peak
height (correlation quality), and the recovered angle.

Run:  python experiments/rotation_sensitivity.py
"""
from __future__ import annotations
import numpy as np
from guider_golden import (synthetic_starfield, fourier_shift, rotate_field,
                           estimate_shift, estimate_rotation)

TRUE_SHIFT = (3.0, -2.0)
ANGLES = [0.0, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0]


def run(shape=(256, 256), seed=1):
    ref = synthetic_starfield(shape=shape, seed=seed)
    rows = []
    for ang in ANGLES:
        img = fourier_shift(rotate_field(ref, ang), TRUE_SHIFT)
        dy, dx, peak, _ = estimate_shift(ref, img, window=True)
        err = float(np.hypot(dy - TRUE_SHIFT[0], dx - TRUE_SHIFT[1]))
        rot_est, _ = estimate_rotation(ref, img)
        rows.append((ang, err, peak, rot_est))
    return rows


def main():
    rows = run()
    print(f"{'angle[deg]':>10} {'shift_err[px]':>14} {'PC_peak':>10} {'rot_est[deg]':>13}")
    for ang, err, peak, rot in rows:
        print(f"{ang:10.3f} {err:14.4f} {peak:10.4f} {rot:13.3f}")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        a = [r[0] for r in rows]; e = [r[1] for r in rows]; p = [r[2] for r in rows]
        fig, ax1 = plt.subplots(figsize=(7, 4.5))
        ax1.plot(a, e, "o-", color="tab:red", label="Translation error [px]")
        ax1.set_xlabel("field rotation [deg]"); ax1.set_ylabel("shift error [px]", color="tab:red")
        ax1.axhline(0.5, ls="--", color="gray", lw=0.8)
        ax2 = ax1.twinx()
        ax2.plot(a, p, "s-", color="tab:blue", label="phase-only peak")
        ax2.set_ylabel("phase-only peak height", color="tab:blue")
        fig.suptitle("Pure-translation phase correlation vs. field rotation")
        fig.tight_layout()
        out = "rotation_sensitivity.png"
        fig.savefig(out, dpi=120)
        print(f"\nplot saved: {out}")
    except Exception as ex:
        print(f"(plot skipped: {ex})")


if __name__ == "__main__":
    main()
