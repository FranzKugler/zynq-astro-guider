from .phase_correlation import estimate_shift, cross_power_spectrum, hann2d
from .synth import synthetic_starfield, fourier_shift, rotate_field
from .fixed_point import estimate_shift as estimate_shift_fixed, FixedConfig

__version__ = "0.2.0"
__all__ = ["estimate_shift", "cross_power_spectrum", "hann2d",
           "synthetic_starfield", "fourier_shift", "rotate_field",
           "estimate_rotation",
           "estimate_shift_fixed", "FixedConfig"]


def __getattr__(name):
    # estimate_rotation lazy laden: zieht scikit-image (warp_polar) erst beim
    # Zugriff, statt schon bei `import guider_golden`. Haelt den Kern-Strang
    # (phase_correlation) frei von der skimage-Abhaengigkeit.
    if name == "estimate_rotation":
        from .fourier_mellin import estimate_rotation
        return estimate_rotation
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
