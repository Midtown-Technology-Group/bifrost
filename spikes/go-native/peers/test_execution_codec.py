"""Independent stream-consumer tests; executed only in supported hosted CI."""
import copy
import hashlib
import io
import json
import struct
import unittest
from pathlib import Path

from execution_codec import MAX_FRAME, Codec, CodecError

CORPUS = Path(__file__).resolve().parent.parent / "executionprofile/testdata/wire-vectors.json"
VECTORS = json.loads(CORPUS.read_text())


def specimen(vector):
    if "recipe" not in vector:
        return bytes.fromhex(vector["hex"])
    recipe = vector["recipe"]
    return (bytes.fromhex(recipe["prefix_hex"])
            + bytes.fromhex(recipe["repeat_byte_hex"]) * recipe["count"]
            + bytes.fromhex(recipe["suffix_hex"]))


class Fragmented(io.BytesIO):
    def __init__(self, initial=b""):
        super().__init__(initial)
        self.interrupt_read = True
        self.interrupt_write = True

    def read(self, size=-1):
        if self.interrupt_read:
            self.interrupt_read = False
            raise InterruptedError
        return super().read(min(size, 1))

    def write(self, value):
        if self.interrupt_write:
            self.interrupt_write = False
            raise InterruptedError
        return super().write(value[:1])


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.codec = Codec()


def wire_test(vector):
    def test(self):
        stream = io.BytesIO(specimen(vector))
        try:
            result = self.codec.read(stream)
            # A stream reader permits the next frame; a corpus specimen must
            # contain exactly one frame, unlike a concatenated stream.
            if stream.read(1):
                raise CodecError("InvalidFrame")
            actual = None
        except CodecError as exc:
            result, actual = None, exc.code
        self.assertEqual(actual, vector["error"])
        if not specimen(vector):
            self.assertIsNone(result)
    return test


for index, vector in enumerate(VECTORS):
    setattr(WireTests, f"test_wire_{index:03d}_{vector['name'].replace('-', '_')}", wire_test(vector))


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.codec = Codec()
        self.raw = next(specimen(v) for v in VECTORS if v["error"] is None and v.get("hex"))
        self.frame = self.codec.read(io.BytesIO(self.raw)).frame

    def test_fragmented_interrupted_read_write_and_concatenation(self):
        sink = Fragmented()
        self.codec.write(sink, self.frame)
        encoded = sink.getvalue()
        source = Fragmented(encoded + encoded)
        self.assertEqual(self.codec.read(source).frame, self.frame)
        self.assertEqual(self.codec.read(source).frame, self.frame)
        self.assertIsNone(self.codec.read(source))

    def test_original_receipt_bytes_survive_frame_mutation(self):
        decoded = self.codec.read(io.BytesIO(self.raw))
        digest = hashlib.sha256(self.raw[4:]).hexdigest()
        decoded.frame["body"] = {}
        self.assertEqual(decoded.payload_sha256(), digest)

    def test_preallocation_limit_and_partial_prefix(self):
        for raw, code in ((struct.pack(">I", MAX_FRAME + 1), "FrameTooLarge"),
                          (b"\x00", "TruncatedFrame")):
            with self.assertRaises(CodecError) as caught:
                self.codec.read(io.BytesIO(raw))
            self.assertEqual(caught.exception.code, code)

    def test_invalid_output_does_not_write(self):
        frame = copy.deepcopy(self.frame)
        frame["sequence"] = True
        sink = io.BytesIO()
        with self.assertRaises(CodecError):
            self.codec.write(sink, frame)
        self.assertEqual(sink.getvalue(), b"")

    def test_nonprogress_and_os_errors(self):
        class Broken:
            def read(self, _size):
                raise OSError

            def write(self, _value):
                return 0

        for operation in (lambda: self.codec.read(Broken()),
                          lambda: self.codec.write(Broken(), self.frame)):
            with self.assertRaises(CodecError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "Io")


if __name__ == "__main__":
    unittest.main(verbosity=2)
