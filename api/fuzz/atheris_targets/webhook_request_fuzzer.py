from __future__ import annotations

import sys

import atheris

# Instrument application coverage without rewriting every dependency at startup.
with atheris.instrument_imports(include=["fuzz", "src"]):
    from fuzz.harnesses import fuzz_webhook_request

    # Load only this target's application imports under instrumentation, before Setup.
    fuzz_webhook_request(b"")


def TestOneInput(data: bytes) -> None:
    fuzz_webhook_request(data)


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
