import importlib
import sys


def main() -> int:
    print("python", sys.version)
    print("exe", sys.executable)

    # Validate expected API used by the editor window.
    try:
        from legacy.plot_waveform_new import plot  # type: ignore

        print("OK from legacy.plot_waveform_new import plot ->", plot)
        print("plot has attr plot_waveform?", hasattr(plot, "plot_waveform"))
        try:
            inst = plot(audio=None)
            print("instantiated plot(audio=None) OK ->", inst)
            print("instance has method plot_waveform?", hasattr(inst, "plot_waveform"))
        except Exception as e:
            print("FAIL instantiate plot(audio=None)", type(e).__name__, e)
    except Exception as e:
        print("FAIL from legacy.plot_waveform_new import plot", type(e).__name__, e)

    mods = [
        "legacy.plot_waveform_new",
        "legacy.plot_waveform_new.plot",
        "legacy.scale_audio",
        "legacy.plot_waveform",
        "legacy.normalize_numpy",
    ]
    for mod in mods:
        try:
            m = importlib.import_module(mod)
            print("OK import", mod, "->", m)
        except Exception as e:
            print("FAIL import", mod, type(e).__name__, e)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
