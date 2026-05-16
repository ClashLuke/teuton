"""Deterministic expected compute-unit estimates for Teuton IR graphs."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from teuton_core.ir import Graph, Op
from teuton_core.protocol import JobManifestV3


OPS_PER_CU = 1_000_000.0
MIN_CU = 1e-6


@dataclass(frozen=True)
class TensorInfo:
    shape: tuple[int, ...]
    dtype: str = "float32"


def expected_compute_units(manifest: JobManifestV3, graph: Graph) -> float:
    """Estimate job work from owner-signed operation shapes, not receipt timing."""
    del manifest  # Params may affect values, but graph shapes define expected work.
    ops = estimate_graph_ops(graph)
    return max(MIN_CU, ops / OPS_PER_CU)


def estimate_graph_ops(graph: Graph) -> float:
    input_infos = {spec.name: TensorInfo(tuple(spec.shape), spec.dtype) for spec in graph.inputs}
    op_infos: list[list[TensorInfo]] = [[TensorInfo(())] for _ in graph.ops]
    total = 0.0

    for op in graph.ops:
        arg_infos = [_info_for_ref(arg, input_infos, op_infos) for arg in op.args]
        out_infos = _infer_outputs(op, arg_infos)
        if op.out:
            out_infos = [TensorInfo(tuple(spec.shape), spec.dtype) for spec in op.out]
        op_infos[op.id] = out_infos
        total += _op_cost(op, arg_infos, out_infos)

    return total


def _info_for_ref(ref: dict[str, Any], inputs: dict[str, TensorInfo], op_infos: list[list[TensorInfo]]) -> TensorInfo:
    kind = ref.get("kind")
    if kind == "input":
        return inputs.get(str(ref.get("name")), TensorInfo(()))
    if kind == "op":
        op_id = int(ref.get("id", 0))
        idx = int(ref.get("idx", 0))
        if 0 <= op_id < len(op_infos) and 0 <= idx < len(op_infos[op_id]):
            return op_infos[op_id][idx]
        return TensorInfo(())
    if kind == "const_blob":
        return TensorInfo(tuple(int(x) for x in ref.get("shape", ())), str(ref.get("dtype", "float32")))
    if kind in {"const", "param"}:
        return TensorInfo(())
    return TensorInfo(())


def _infer_outputs(op: Op, args: list[TensorInfo]) -> list[TensorInfo]:
    name = op.op
    shapes = [arg.shape for arg in args]
    dtype = args[0].dtype if args else str(op.kwargs.get("dtype", "float32"))

    if name in {"add", "sub", "mul", "div", "pow", "gt", "lt", "ge", "le", "eq"}:
        return [TensorInfo(_broadcast_all(shapes), dtype)]
    if name in {
        "neg", "exp", "log", "sqrt", "abs", "round", "relu", "gelu", "silu", "sin", "cos",
        "sigmoid", "tanh", "identity", "sign", "clamp", "cast", "tril", "triu",
        "dequantize_int8_per_channel",
    }:
        return [TensorInfo(shapes[0] if shapes else (), str(op.kwargs.get("dtype", dtype)))]
    if name == "where":
        return [TensorInfo(_broadcast_all(shapes), dtype)]
    if name == "matmul" and len(shapes) >= 2:
        return [TensorInfo(_matmul_shape(shapes[0], shapes[1]), dtype)]
    if name == "transpose" and shapes:
        dims = [int(x) for x in op.kwargs.get("dims", range(len(shapes[0])))]
        return [TensorInfo(tuple(shapes[0][i] for i in dims), dtype)]
    if name == "reshape":
        return [TensorInfo(tuple(int(x) for x in op.kwargs.get("shape", ())), dtype)]
    if name == "einsum" and len(shapes) >= 2:
        return [TensorInfo(_einsum_shape(str(op.kwargs.get("equation", "")), shapes[0], shapes[1]), dtype)]
    if name in {"sum", "mean", "max", "min"} and shapes:
        return [TensorInfo(_reduce_shape(shapes[0], op.kwargs.get("dim"), bool(op.kwargs.get("keepdim", False))), dtype)]
    if name == "concat":
        dim = _normalize_dim(int(op.kwargs.get("dim", 0)), len(shapes[0]) if shapes else 0)
        out = list(shapes[0] if shapes else ())
        out[dim] = sum(_dim(shape[dim]) for shape in shapes if len(shape) > dim)
        return [TensorInfo(tuple(out), dtype)]
    if name == "stack":
        dim = _normalize_dim(int(op.kwargs.get("dim", 0)), len(shapes[0]) + 1 if shapes else 1)
        out = list(shapes[0] if shapes else ())
        out.insert(dim, len(shapes))
        return [TensorInfo(tuple(out), dtype)]
    if name == "split" and shapes:
        dim = _normalize_dim(int(op.kwargs.get("dim", 0)), len(shapes[0]))
        outs = []
        for size in op.kwargs.get("sizes", ()):
            out = list(shapes[0])
            out[dim] = int(size)
            outs.append(TensorInfo(tuple(out), dtype))
        return outs or [TensorInfo(shapes[0], dtype)]
    if name == "broadcast":
        return [TensorInfo(tuple(int(x) for x in op.kwargs.get("shape", ())), dtype)]
    if name == "squeeze" and shapes:
        dim = _normalize_dim(int(op.kwargs.get("dim", 0)), len(shapes[0]))
        return [TensorInfo(tuple(x for i, x in enumerate(shapes[0]) if i != dim), dtype)]
    if name == "unsqueeze" and shapes:
        dim = _normalize_dim(int(op.kwargs.get("dim", 0)), len(shapes[0]) + 1)
        out = list(shapes[0])
        out.insert(dim, 1)
        return [TensorInfo(tuple(out), dtype)]
    if name == "slice" and shapes:
        dim = _normalize_dim(int(op.kwargs.get("dim", 0)), len(shapes[0]))
        start = int(op.kwargs.get("start", 0)) if not isinstance(op.kwargs.get("start"), dict) else 0
        end_value = op.kwargs.get("end", shapes[0][dim])
        end = int(end_value) if not isinstance(end_value, dict) else _dim(shapes[0][dim])
        out = list(shapes[0])
        out[dim] = max(0, end - start)
        return [TensorInfo(tuple(out), dtype)]
    if name in {"gather", "scatter"}:
        return [TensorInfo(shapes[1] if name == "gather" and len(shapes) > 1 else shapes[0], dtype)]
    if name == "arange":
        start = int(op.kwargs.get("start", 0))
        end = int(op.kwargs.get("end", start))
        step = max(1, abs(int(op.kwargs.get("step", 1))))
        return [TensorInfo((max(0, math.ceil((end - start) / step)),), str(op.kwargs.get("dtype", "int64")))]
    if name in {"normal", "uniform", "full"}:
        return [TensorInfo(tuple(int(x) for x in op.kwargs.get("shape", ())), str(op.kwargs.get("dtype", "float32")))]
    if name in {"sort", "softmax", "log_softmax"}:
        return [TensorInfo(shapes[0] if shapes else (), dtype)]
    if name == "topk" and shapes:
        dim = _normalize_dim(int(op.kwargs.get("dim", -1)), len(shapes[0]))
        out = list(shapes[0])
        out[dim] = int(op.kwargs.get("k", out[dim]))
        return [TensorInfo(tuple(out), dtype), TensorInfo(tuple(out), "int64")]
    if name in {"layer_norm", "rmsnorm"} and shapes:
        return [TensorInfo(shapes[0], dtype)]
    if name == "cross_entropy":
        return [TensorInfo((), dtype)]
    if name == "embedding" and len(shapes) >= 2:
        return [TensorInfo(tuple(shapes[1]) + tuple(shapes[0][1:]), dtype)]
    if name == "data_indexer":
        b = int(op.kwargs.get("B", 1))
        t = int(op.kwargs.get("T", 1))
        return [TensorInfo((b, t), "int64"), TensorInfo((b, t), "int64")]
    if name == "quantize_int8_per_channel" and shapes:
        dim = _normalize_dim(int(op.kwargs.get("dim", -1)), len(shapes[0]))
        scale = tuple(size if i == dim else 1 for i, size in enumerate(shapes[0]))
        return [TensorInfo(shapes[0], "int8"), TensorInfo(scale, "float32")]
    if name == "quantize_pack_int8" and shapes:
        dim = _normalize_dim(int(op.kwargs.get("dim", -1)), len(shapes[0]))
        scale_elems = _dim(shapes[0][dim])
        return [TensorInfo((_numel(shapes[0]) + 4 * scale_elems,), "uint8")]
    if name == "unpack_dequantize_int8":
        return [TensorInfo(tuple(int(x) for x in op.kwargs.get("shape", ())), "float32")]
    if name == "qr" and shapes:
        m = _dim(shapes[0][-2]) if len(shapes[0]) >= 2 else _numel(shapes[0])
        n = _dim(shapes[0][-1]) if shapes[0] else 1
        k = min(m, n)
        batch = shapes[0][:-2] if len(shapes[0]) >= 2 else ()
        return [TensorInfo(tuple(batch) + (m, k), dtype), TensorInfo(tuple(batch) + (k, n), dtype)]
    return [TensorInfo(shapes[0] if shapes else (), dtype)]


def _op_cost(op: Op, args: list[TensorInfo], outs: list[TensorInfo]) -> float:
    name = op.op
    out_numel = sum(_numel(out.shape) for out in outs) or 1
    in_numel = sum(_numel(arg.shape) for arg in args) or out_numel

    if name == "matmul" and len(args) >= 2:
        return _matmul_ops(args[0].shape, args[1].shape)
    if name == "einsum" and len(args) >= 2:
        return _einsum_ops(str(op.kwargs.get("equation", "")), args[0].shape, args[1].shape, outs[0].shape)
    if name in {"exp", "log", "sqrt", "gelu", "silu", "sin", "cos", "sigmoid", "tanh"}:
        return 4.0 * out_numel
    if name in {"softmax", "log_softmax"}:
        return 5.0 * in_numel
    if name in {"layer_norm", "rmsnorm"}:
        return 5.0 * in_numel
    if name == "cross_entropy":
        return 3.0 * in_numel
    if name == "qr" and args:
        shape = args[0].shape
        m = _dim(shape[-2]) if len(shape) >= 2 else _numel(shape)
        n = _dim(shape[-1]) if shape else 1
        batch = _numel(shape[:-2]) if len(shape) >= 2 else 1
        return 2.0 * batch * m * n * min(m, n)
    if name in {"sort", "topk"} and args:
        dim = _normalize_dim(int(op.kwargs.get("dim", -1)), len(args[0].shape))
        axis = _dim(args[0].shape[dim]) if args[0].shape else 1
        return in_numel * max(1.0, math.log2(max(2, axis)))
    if name in {"normal", "uniform"}:
        return 2.0 * out_numel
    if name in {"quantize_int8_per_channel", "dequantize_int8_per_channel", "quantize_pack_int8", "unpack_dequantize_int8"}:
        return 2.0 * max(in_numel, out_numel)
    return max(in_numel, out_numel)


def _numel(shape: tuple[int, ...]) -> int:
    total = 1
    for dim in shape:
        total *= _dim(dim)
    return total


def _dim(value: int) -> int:
    return max(1, int(value))


def _broadcast_all(shapes: list[tuple[int, ...]]) -> tuple[int, ...]:
    if not shapes:
        return ()
    out: tuple[int, ...] = ()
    for shape in shapes:
        out = _broadcast_two(out, shape)
    return out


def _broadcast_two(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    rev: list[int] = []
    for i in range(max(len(a), len(b))):
        da = a[-1 - i] if i < len(a) else 1
        db = b[-1 - i] if i < len(b) else 1
        rev.append(max(_dim(da), _dim(db)))
    return tuple(reversed(rev))


def _normalize_dim(dim: int, rank: int) -> int:
    if rank <= 0:
        return 0
    return dim + rank if dim < 0 else dim


def _matmul_shape(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    if len(a) == 1 and len(b) == 1:
        return ()
    if len(a) == 1:
        return _broadcast_two((), b[:-2]) + (b[-1],)
    if len(b) == 1:
        return _broadcast_two(a[:-2], ()) + (a[-2],)
    return _broadcast_two(a[:-2], b[:-2]) + (a[-2], b[-1])


def _matmul_ops(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    if len(a) == 1 and len(b) == 1:
        return 2.0 * _dim(a[0] if a else 1)
    if len(a) == 1:
        batch = _numel(b[:-2])
        return 2.0 * batch * _dim(a[0] if a else 1) * _dim(b[-1])
    if len(b) == 1:
        batch = _numel(a[:-2])
        return 2.0 * batch * _dim(a[-2]) * _dim(b[0] if b else 1)
    batch_shape = _broadcast_two(a[:-2], b[:-2])
    return 2.0 * _numel(batch_shape) * _dim(a[-2]) * _dim(a[-1]) * _dim(b[-1])


def _reduce_shape(shape: tuple[int, ...], dim: Any, keepdim: bool) -> tuple[int, ...]:
    if dim is None:
        return tuple(1 for _ in shape) if keepdim else ()
    dims = [int(x) for x in dim] if isinstance(dim, (list, tuple)) else [int(dim)]
    dims = sorted({_normalize_dim(d, len(shape)) for d in dims})
    out = []
    for i, size in enumerate(shape):
        if i in dims:
            if keepdim:
                out.append(1)
        else:
            out.append(size)
    return tuple(out)


def _einsum_shape(equation: str, a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    try:
        lhs, out_labels = equation.split("->", 1)
        a_labels, b_labels = lhs.split(",", 1)
    except ValueError:
        return ()
    sizes: dict[str, int] = {}
    for label, size in zip(a_labels, a):
        sizes[label] = _dim(size)
    for label, size in zip(b_labels, b):
        sizes[label] = max(sizes.get(label, 1), _dim(size))
    return tuple(sizes.get(label, 1) for label in out_labels)


def _einsum_ops(equation: str, a: tuple[int, ...], b: tuple[int, ...], out: tuple[int, ...]) -> float:
    try:
        lhs, out_labels = equation.split("->", 1)
        a_labels, b_labels = lhs.split(",", 1)
    except ValueError:
        return max(_numel(a), _numel(b), _numel(out))
    sizes: dict[str, int] = {}
    for label, size in zip(a_labels, a):
        sizes[label] = _dim(size)
    for label, size in zip(b_labels, b):
        sizes[label] = max(sizes.get(label, 1), _dim(size))
    contracted = set(a_labels) & set(b_labels) - set(out_labels)
    contract_size = math.prod(sizes[label] for label in contracted) if contracted else 1
    return 2.0 * _numel(out) * contract_size
