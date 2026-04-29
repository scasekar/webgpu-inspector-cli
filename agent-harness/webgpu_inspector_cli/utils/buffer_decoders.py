"""Decoders for raw buffer bytes captured from the WebGPU inspector.

The inspector returns buffer payloads in several shapes (base64 data URLs, bare
base64, hex strings, or arrays of byte values). `to_bytes` normalizes those into
a `bytes` object; the `format_*` helpers produce human-readable output for the
CLI's `capture buffer --format` flag and for the equivalent MCP tool.

`format_struct` parses a small struct-spec language for ad-hoc record decoding:
    "mat4x4 anchorToWorld; u32 chunkIdDebug; pad12; vec3 origin; f32 scale"
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
from typing import Any


# --- Input normalization ---


def to_bytes(data: Any) -> bytes:
    """Coerce inspector-returned buffer data into raw bytes.

    Accepts: bytes/bytearray, base64 data URL ('data:...;base64,XYZ'), bare
    base64, hex string, or a list of byte-valued ints. Raises ValueError on
    anything else.
    """
    if data is None:
        return b""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, list):
        return bytes(data)
    if not isinstance(data, str):
        raise ValueError(f"Cannot decode buffer data of type {type(data).__name__}")

    s = data.strip()
    if not s:
        return b""

    # Strip data URL prefix if present.
    if s.startswith("data:"):
        comma = s.find(",")
        if comma == -1:
            raise ValueError("Malformed data URL: no comma after MIME type")
        s = s[comma + 1 :]

    # Hex strings are even-length and contain only hex digits (allow whitespace).
    compact = re.sub(r"\s+", "", s)
    if compact and len(compact) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", compact):
        try:
            return bytes.fromhex(compact)
        except ValueError:
            pass  # fall through to base64

    try:
        return base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Could not decode buffer data: {exc}") from exc


def slice_bytes(buf: bytes, offset: int = 0, size: int | None = None) -> bytes:
    """Apply an offset/size window to raw bytes."""
    if offset < 0:
        raise ValueError("offset must be >= 0")
    end = len(buf) if size is None else min(len(buf), offset + size)
    return buf[offset:end]


# --- Format helpers ---


def format_hex(buf: bytes) -> str:
    """Plain lowercase hex, no separators."""
    return buf.hex()


def format_hex_dump(buf: bytes, width: int = 16, base_offset: int = 0) -> str:
    """xxd-style dump: offset | hex bytes | ASCII gutter."""
    if not buf:
        return ""
    lines = []
    for row in range(0, len(buf), width):
        chunk = buf[row : row + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        # Pad hex section so the ASCII gutter aligns when the last row is short.
        hex_part = hex_part.ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{base_offset + row:08x}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)


def format_u32_list(buf: bytes, *, per_line: int = 8) -> str:
    """Little-endian u32s, one row per `per_line` values."""
    truncated = buf[: (len(buf) // 4) * 4]
    values = struct.unpack(f"<{len(truncated) // 4}I", truncated)
    return _format_value_grid([str(v) for v in values], per_line)


def format_i32_list(buf: bytes, *, per_line: int = 8) -> str:
    """Little-endian i32s."""
    truncated = buf[: (len(buf) // 4) * 4]
    values = struct.unpack(f"<{len(truncated) // 4}i", truncated)
    return _format_value_grid([str(v) for v in values], per_line)


def format_f32_list(buf: bytes, *, per_line: int = 8, precision: int = 6) -> str:
    """Little-endian f32s formatted to `precision` decimal places."""
    truncated = buf[: (len(buf) // 4) * 4]
    values = struct.unpack(f"<{len(truncated) // 4}f", truncated)
    return _format_value_grid([f"{v:.{precision}g}" for v in values], per_line)


def format_f32_mat4(buf: bytes) -> str:
    """Sequence of 4x4 column-major f32 matrices (64 bytes each).

    Output as readable rows: each line is one row of one matrix; matrices are
    separated by a blank line.
    """
    truncated = buf[: (len(buf) // 64) * 64]
    if not truncated:
        return "(no complete 4x4 f32 matrix in buffer)"
    out = []
    for m in range(0, len(truncated), 64):
        cols = struct.unpack("<16f", truncated[m : m + 64])
        # WebGPU/WGSL convention: column-major. Print rows for readability.
        rows = [
            (cols[0], cols[4], cols[8], cols[12]),
            (cols[1], cols[5], cols[9], cols[13]),
            (cols[2], cols[6], cols[10], cols[14]),
            (cols[3], cols[7], cols[11], cols[15]),
        ]
        out.append(f"mat4x4 #{m // 64}:")
        for row in rows:
            out.append("  " + "  ".join(f"{v:>+14.6g}" for v in row))
        out.append("")
    return "\n".join(out).rstrip()


def _format_value_grid(values: list[str], per_line: int) -> str:
    if not values:
        return ""
    # Right-align within a column for scannability.
    width = max(len(v) for v in values)
    lines = []
    for i in range(0, len(values), per_line):
        row = values[i : i + per_line]
        lines.append("  ".join(v.rjust(width) for v in row))
    return "\n".join(lines)


# --- Struct spec parser ---


# Built-in primitive types: name → (struct format char, size in bytes, label).
_PRIMITIVES: dict[str, tuple[str, int, str]] = {
    "u8": ("B", 1, "u8"),
    "i8": ("b", 1, "i8"),
    "u16": ("H", 2, "u16"),
    "i16": ("h", 2, "i16"),
    "u32": ("I", 4, "u32"),
    "i32": ("i", 4, "i32"),
    "u64": ("Q", 8, "u64"),
    "i64": ("q", 8, "i64"),
    "f32": ("f", 4, "f32"),
    "f64": ("d", 8, "f64"),
    "bool": ("?", 1, "bool"),
}


def _parse_struct_spec(spec: str) -> list[dict]:
    """Parse a struct spec into a list of field descriptors.

    Field syntax (semicolons separate fields):
      <type> <name>            e.g. "u32 chunkId", "f32 scale"
      <type>                   e.g. "u32" (auto-named field_<n>)
      padN                     e.g. "pad12" — N bytes of padding (skipped)
      vec2 / vec3 / vec4 <name>     short for f32 vector
      mat4x4 <name>            16 f32s, column-major
      mat3x3 <name>            9 f32s, column-major
      mat2x2 <name>            4 f32s, column-major

    Whitespace and trailing semicolons are tolerated.
    """
    fields: list[dict] = []
    auto_index = 0
    parts = [p.strip() for p in spec.split(";") if p.strip()]
    for raw in parts:
        tokens = raw.split()
        head = tokens[0]

        # padN
        m = re.fullmatch(r"pad(\d+)", head)
        if m:
            n = int(m.group(1))
            if n <= 0:
                raise ValueError(f"pad size must be positive: '{raw}'")
            fields.append({"kind": "pad", "size": n})
            continue

        kind: str
        size: int
        fmt: str
        elements: int = 1

        if head in _PRIMITIVES:
            fmt_char, size, label = _PRIMITIVES[head]
            kind = "scalar"
            fmt = "<" + fmt_char
        elif head in ("vec2", "vec3", "vec4"):
            elements = int(head[3])
            kind = "vec"
            size = 4 * elements
            fmt = f"<{elements}f"
        elif head == "mat4x4":
            kind = "mat"
            elements = 16
            size = 64
            fmt = "<16f"
        elif head == "mat3x3":
            kind = "mat"
            elements = 9
            size = 36
            fmt = "<9f"
        elif head == "mat2x2":
            kind = "mat"
            elements = 4
            size = 16
            fmt = "<4f"
        else:
            raise ValueError(f"Unknown struct type '{head}' in field '{raw}'")

        if len(tokens) > 2:
            raise ValueError(f"Too many tokens in struct field '{raw}'")
        name = tokens[1] if len(tokens) == 2 else f"field_{auto_index}"
        auto_index += 1

        fields.append(
            {
                "kind": kind,
                "name": name,
                "type": head,
                "size": size,
                "fmt": fmt,
                "elements": elements,
            }
        )

    if not fields:
        raise ValueError("Struct spec is empty")
    return fields


def _record_size(fields: list[dict]) -> int:
    return sum(f["size"] for f in fields)


def format_struct(buf: bytes, spec: str, *, max_records: int | None = None) -> str:
    """Decode `buf` as a tightly-packed array of records described by `spec`.

    Each record in the buffer is `record_size` bytes (sum of field sizes).
    Output is human-readable: one record per block, fields prefixed by name.
    Padding fields are omitted from output.
    """
    fields = _parse_struct_spec(spec)
    rec_size = _record_size(fields)
    if rec_size == 0:
        raise ValueError("Struct spec resolves to zero bytes")

    n_records = len(buf) // rec_size
    if max_records is not None:
        n_records = min(n_records, max_records)

    if n_records == 0:
        return f"(buffer too small: have {len(buf)} bytes, struct is {rec_size} bytes)"

    out = []
    out.append(f"# struct: {spec}  ({rec_size} bytes/record, {n_records} record(s))")
    for r in range(n_records):
        base = r * rec_size
        out.append(f"[{r}]")
        cursor = base
        for field in fields:
            if field["kind"] == "pad":
                cursor += field["size"]
                continue
            chunk = buf[cursor : cursor + field["size"]]
            cursor += field["size"]
            values = struct.unpack(field["fmt"], chunk)
            out.append(_format_struct_field(field, values))
    return "\n".join(out)


def _format_struct_field(field: dict, values: tuple) -> str:
    name = field["name"]
    typ = field["type"]
    if field["kind"] == "scalar":
        v = values[0]
        if typ.startswith("f"):
            return f"  {name} ({typ}) = {v:.6g}"
        return f"  {name} ({typ}) = {v}"
    if field["kind"] == "vec":
        comps = ", ".join(f"{v:.6g}" for v in values)
        return f"  {name} ({typ}) = [{comps}]"
    if field["kind"] == "mat":
        # Column-major; print rows. Square matrix only here.
        side = int(field["elements"] ** 0.5)
        rows = []
        for r in range(side):
            row = [values[c * side + r] for c in range(side)]
            rows.append("    " + "  ".join(f"{v:>+12.6g}" for v in row))
        return f"  {name} ({typ}) =\n" + "\n".join(rows)
    raise AssertionError(f"unhandled field kind: {field['kind']}")


# --- Top-level dispatch (used by the CLI) ---


# All format identifiers that `dispatch_format` understands.
SUPPORTED_FORMATS = (
    "hex",
    "hex-dump",
    "raw",
    "u32-list",
    "i32-list",
    "f32-list",
    "f32-mat4",
    # legacy aliases:
    "uint32",
    "float32",
)


def dispatch_format(
    buf: bytes,
    fmt: str,
    *,
    base_offset: int = 0,
    struct_spec: str | None = None,
) -> str:
    """Format `buf` according to `fmt`. If `struct_spec` is provided, use it
    instead of `fmt`."""
    if struct_spec:
        return format_struct(buf, struct_spec)

    if fmt == "hex":
        return format_hex(buf)
    if fmt == "hex-dump":
        return format_hex_dump(buf, base_offset=base_offset)
    if fmt == "raw":
        return base64.b64encode(buf).decode("ascii")
    if fmt in ("u32-list", "uint32"):
        return format_u32_list(buf)
    if fmt == "i32-list":
        return format_i32_list(buf)
    if fmt in ("f32-list", "float32"):
        return format_f32_list(buf)
    if fmt == "f32-mat4":
        return format_f32_mat4(buf)
    raise ValueError(f"Unknown format '{fmt}' (supported: {', '.join(SUPPORTED_FORMATS)})")


# --- WebGPU buffer-usage bitmask decoder (used by `objects list --type Buffer`) ---


# Mirrors GPUBufferUsage flag bits from the WebGPU spec.
BUFFER_USAGE_FLAGS: list[tuple[int, str]] = [
    (0x0001, "MapRead"),
    (0x0002, "MapWrite"),
    (0x0004, "CopySrc"),
    (0x0008, "CopyDst"),
    (0x0010, "Index"),
    (0x0020, "Vertex"),
    (0x0040, "Uniform"),
    (0x0080, "Storage"),
    (0x0100, "Indirect"),
    (0x0200, "QueryResolve"),
]


def decode_buffer_usage(usage: int | None) -> list[str]:
    """Decode a GPUBufferUsage bitmask into a list of named flags."""
    if not usage:
        return []
    return [name for bit, name in BUFFER_USAGE_FLAGS if usage & bit]
