"""PyInstaller entry point — invoked as the single-file Mach-O binary."""
import multiprocessing


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from bilio_sidecar.rpc import main
    main()
