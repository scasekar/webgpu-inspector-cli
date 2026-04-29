"""Unit tests for utils/buffer_decoders. Pure Python, no browser."""

import base64
import struct

import pytest

from webgpu_inspector_cli.utils.buffer_decoders import (
    BUFFER_USAGE_FLAGS,
    decode_buffer_usage,
    dispatch_format,
    format_f32_list,
    format_f32_mat4,
    format_hex,
    format_hex_dump,
    format_struct,
    format_u32_list,
    slice_bytes,
    to_bytes,
)


# --- to_bytes ---


def test_to_bytes_passthrough_bytes():
    assert to_bytes(b"\x01\x02\x03") == b"\x01\x02\x03"


def test_to_bytes_bytearray():
    assert to_bytes(bytearray([1, 2, 3])) == b"\x01\x02\x03"


def test_to_bytes_int_list():
    assert to_bytes([0xDE, 0xAD, 0xBE, 0xEF]) == b"\xde\xad\xbe\xef"


def test_to_bytes_hex_string():
    assert to_bytes("deadbeef") == b"\xde\xad\xbe\xef"
    assert to_bytes("DEADBEEF") == b"\xde\xad\xbe\xef"


def test_to_bytes_hex_with_whitespace():
    assert to_bytes("de ad be ef") == b"\xde\xad\xbe\xef"


def test_to_bytes_base64_data_url():
    payload = b"\x01\x02\x03\x04"
    url = "data:application/octet-stream;base64," + base64.b64encode(payload).decode()
    assert to_bytes(url) == payload


def test_to_bytes_bare_base64():
    # Pick a payload whose base64 encoding is NOT a valid hex string,
    # to disambiguate from the hex path.
    payload = b"hello world!!!!!"  # encoded uses non-hex characters
    encoded = base64.b64encode(payload).decode()
    assert to_bytes(encoded) == payload


def test_to_bytes_empty():
    assert to_bytes(None) == b""
    assert to_bytes("") == b""
    assert to_bytes("   ") == b""


def test_to_bytes_rejects_garbage_type():
    with pytest.raises(ValueError):
        to_bytes(3.14)


# --- slice_bytes ---


def test_slice_bytes():
    buf = bytes(range(20))
    assert slice_bytes(buf, 0, 4) == bytes(range(4))
    assert slice_bytes(buf, 4, 4) == bytes(range(4, 8))
    assert slice_bytes(buf, 0, None) == buf
    assert slice_bytes(buf, 18, 100) == bytes([18, 19])


def test_slice_bytes_rejects_negative_offset():
    with pytest.raises(ValueError):
        slice_bytes(b"abc", -1)


# --- format_hex / format_hex_dump ---


def test_format_hex():
    assert format_hex(b"\x00\xff\x10") == "00ff10"


def test_format_hex_dump_basic():
    out = format_hex_dump(b"ABCD\x00\x01\x02")
    # Single row: 7 bytes + ASCII gutter showing printable + dots.
    assert "00000000" in out
    assert "41 42 43 44 00 01 02" in out
    assert "|ABCD...|" in out


def test_format_hex_dump_multirow():
    buf = bytes(range(20))
    out = format_hex_dump(buf, width=16)
    lines = out.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("00000000")
    assert lines[1].startswith("00000010")


def test_format_hex_dump_empty():
    assert format_hex_dump(b"") == ""


# --- u32 / f32 lists ---


def test_format_u32_list():
    buf = struct.pack("<5I", 1, 2, 3, 4, 5)
    out = format_u32_list(buf, per_line=8)
    # All 5 values on one line
    assert out.split() == ["1", "2", "3", "4", "5"]


def test_format_u32_list_truncates_trailing_partial_word():
    buf = struct.pack("<3I", 10, 20, 30) + b"\x99"  # 1 stray byte
    out = format_u32_list(buf)
    assert out.split() == ["10", "20", "30"]


def test_format_f32_list():
    buf = struct.pack("<3f", 1.5, -2.25, 3.125)
    out = format_f32_list(buf)
    tokens = out.split()
    assert tokens == ["1.5", "-2.25", "3.125"]


# --- f32 mat4 ---


def test_format_f32_mat4_identity():
    identity_cols = [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ]
    buf = struct.pack("<16f", *identity_cols)
    out = format_f32_mat4(buf)
    assert "mat4x4 #0:" in out
    # Diagonal should have 1s
    lines = [ln.strip() for ln in out.split("\n") if ln.strip().startswith("+1") or ln.strip().startswith("0")]
    # Diagonal entries appear; spot-check that ones are present in the output
    assert out.count("+1") == 4


def test_format_f32_mat4_two_matrices():
    buf = struct.pack("<32f", *([0] * 16), *([2] * 16))
    out = format_f32_mat4(buf)
    assert "mat4x4 #0:" in out
    assert "mat4x4 #1:" in out


def test_format_f32_mat4_short_buf():
    out = format_f32_mat4(b"\x00" * 32)
    assert "no complete" in out


# --- format_struct ---


def test_format_struct_basic():
    spec = "u32 chunkId; f32 scale"
    buf = struct.pack("<If", 7, 1.5)
    out = format_struct(buf, spec)
    assert "chunkId (u32) = 7" in out
    assert "scale (f32) = 1.5" in out
    assert "1 record(s)" in out


def test_format_struct_with_padding():
    spec = "u32 a; pad4; u32 b"
    buf = struct.pack("<I", 1) + b"\x00" * 4 + struct.pack("<I", 2)
    out = format_struct(buf, spec)
    assert "a (u32) = 1" in out
    assert "b (u32) = 2" in out
    # Padding doesn't appear in output (no field name was assigned).
    assert "pad" not in out.lower().split("\n")[1]


def test_format_struct_multiple_records():
    spec = "u32 idx; f32 v"
    records = [(0, 0.5), (1, 1.5), (2, 2.5)]
    buf = b"".join(struct.pack("<If", i, v) for i, v in records)
    out = format_struct(buf, spec)
    assert "[0]" in out and "[1]" in out and "[2]" in out
    assert "idx (u32) = 1" in out
    assert "v (f32) = 2.5" in out


def test_format_struct_vec3():
    spec = "vec3 pos"
    buf = struct.pack("<3f", 1.0, 2.0, 3.0)
    out = format_struct(buf, spec)
    assert "pos (vec3) = [1, 2, 3]" in out


def test_format_struct_mat4x4():
    spec = "mat4x4 m"
    cols = [float(i) for i in range(16)]
    buf = struct.pack("<16f", *cols)
    out = format_struct(buf, spec)
    assert "m (mat4x4)" in out
    # Column-major: row 0 should show the first element of each column: 0, 4, 8, 12
    lines = out.split("\n")
    row0 = next(ln for ln in lines if "+0" in ln and "+4" in ln)
    assert "+0" in row0 and "+4" in row0 and "+8" in row0 and "+12" in row0


def test_format_struct_real_world_example():
    # From the feature request: "mat4x4 anchorToWorld; u32 chunkIdDebug; pad12"
    spec = "mat4x4 anchorToWorld; u32 chunkIdDebug; pad12"
    record = struct.pack("<16f", *([0.0] * 16)) + struct.pack("<I", 42) + b"\x00" * 12
    out = format_struct(record, spec)
    assert "anchorToWorld (mat4x4)" in out
    assert "chunkIdDebug (u32) = 42" in out
    assert "1 record(s)" in out


def test_format_struct_too_small_buffer():
    spec = "u32 a; u32 b"
    out = format_struct(b"\x01\x02", spec)
    assert "buffer too small" in out


def test_format_struct_rejects_unknown_type():
    with pytest.raises(ValueError):
        format_struct(b"\x00" * 4, "uint64 x")  # u64 ok, uint64 not


def test_format_struct_rejects_empty_spec():
    with pytest.raises(ValueError):
        format_struct(b"\x00", "  ;  ")


def test_format_struct_max_records():
    spec = "u32 v"
    buf = struct.pack("<5I", 10, 20, 30, 40, 50)
    out = format_struct(buf, spec, max_records=2)
    assert "[0]" in out and "[1]" in out and "[2]" not in out


# --- dispatch_format ---


@pytest.mark.parametrize(
    "fmt,buf,must_contain",
    [
        ("hex", b"\xab\xcd", "abcd"),
        ("hex-dump", b"AB", "41 42"),
        ("raw", b"\x00\x01", base64.b64encode(b"\x00\x01").decode()),
        ("u32-list", struct.pack("<I", 0xDEADBEEF), str(0xDEADBEEF)),
        ("uint32", struct.pack("<I", 1), "1"),  # legacy alias
        ("i32-list", struct.pack("<i", -3), "-3"),
        ("f32-list", struct.pack("<f", 0.5), "0.5"),
        ("float32", struct.pack("<f", 0.5), "0.5"),  # legacy alias
        ("f32-mat4", struct.pack("<16f", *([0] * 16)), "mat4x4 #0:"),
    ],
)
def test_dispatch_format_paths(fmt, buf, must_contain):
    out = dispatch_format(buf, fmt)
    assert must_contain in out


def test_dispatch_format_struct_spec_overrides_fmt():
    spec = "u32 v"
    buf = struct.pack("<I", 99)
    out = dispatch_format(buf, "hex", struct_spec=spec)
    assert "v (u32) = 99" in out


def test_dispatch_format_unknown():
    with pytest.raises(ValueError):
        dispatch_format(b"\x00", "binary-soup")


# --- decode_buffer_usage ---


def test_decode_buffer_usage_none():
    assert decode_buffer_usage(None) == []
    assert decode_buffer_usage(0) == []


def test_decode_buffer_usage_storage_copydst():
    # 0x80 (Storage) | 0x08 (CopyDst) = 0x88
    assert decode_buffer_usage(0x88) == ["CopyDst", "Storage"]


def test_decode_buffer_usage_indirect():
    assert decode_buffer_usage(0x100) == ["Indirect"]


def test_decode_buffer_usage_uniform_copydst():
    # 0x40 (Uniform) | 0x08 (CopyDst) = 0x48
    flags = decode_buffer_usage(0x48)
    assert "Uniform" in flags and "CopyDst" in flags


def test_decode_buffer_usage_known_bits_match_spec():
    # Sanity: the table mirrors the WebGPU GPUBufferUsage spec values.
    expected = {
        "MapRead": 0x0001,
        "MapWrite": 0x0002,
        "CopySrc": 0x0004,
        "CopyDst": 0x0008,
        "Index": 0x0010,
        "Vertex": 0x0020,
        "Uniform": 0x0040,
        "Storage": 0x0080,
        "Indirect": 0x0100,
        "QueryResolve": 0x0200,
    }
    actual = {name: bit for bit, name in BUFFER_USAGE_FLAGS}
    assert actual == expected
