"""held-in / held-out 双集（框架 v1.1 C9 / GEP-RSI 第 5 步）。

**为什么需要双集**：validation_gate 原来只在**同一批** golden 抽样上比
基线 vs 候选——这是「单集双侧」。固定 10 条验证集上的 ±20% 闸可以被
逐代过拟合：每代只挑对这 10 条有利的改动，指标一路合规上升，泛化能力
却在下降。评审把它列为 NO-GO 级缺口（`framework-review-20261106.md` §7.4
C9 / 理念七「检查信号必须经由被检方不可控的通道产生」）。

**双集的分工**：

- **held-in**（约 70%）：候选在这上面必须**不退化超阈值**。它相当于
  训练集，可以留在仓库里（明文可读）。
- **held-out**（约 30%）：候选在这上面必须**无显著退化**。它是泛化性
  检验集，**不放在仓库里**——只在 ``~/.agentnodes/eval_heldout/``
  （或 ``AN_GOLDEN_HELDOUT_PATH``）由主进程在评测期读取。agent 的
  ``read`` 工具以工作区为根，读不到仓库外的绝对路径；仓库内也没有副本。

**拆分规则（可复现、有依据、非随机）**：``sha256(task_id)`` 的前 8 字节
取模 100，``< 70`` 进 held-in，否则进 held-out。规则只依赖 task id，
不依赖文件顺序、不依赖随机数种子、不依赖 Python 版本——任何人在任何
机器上重跑都得到同一份拆分。冲突用 id 的哈希裁决，不由人挑。

**seed 进程外注入**：抽样种子走 ``AN_GOLDEN_SEED`` 环境变量。未设时
返回固定默认值并**发事件提示**（``rsi.golden_seed``），不静默。

**只读**：本模块无任何写业务表的调用；导出/拆分只发生在
``scripts/arch/split_golden_sets.py`` 与 ``export_heldout.py``。

路径全部复用 ``foundation.storage.sqlite.ensure_data_dir``（``AN_DATA_DIR``
可覆盖），不硬编码 home 路径——与 ``halt.py`` / ``rsi_cli.py`` 同一口径。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from agentnodes.evals.scorecard import GoldenTask, load_golden_tasks

logger = logging.getLogger(__name__)

# ── 拆分口径（模块常量；改口径必须递增 SPLIT_VERSION 并重跑导出）──────

#: 拆分口径版本。写进 manifest，供 verify 与审计断言「同一份拆分」。
SPLIT_VERSION = 1

#: 哈希分桶的模数。100 给出 1% 粒度，70/30 的边界足够精确。
BUCKET_MODULUS = 100

#: held-in 的桶上界（``bucket < HELDIN_BUCKET_MAX`` → held-in）。
#: 70 ≈ 70% 的 held-in 份额；实测 70 条原始集得到 47/23（67.1%/32.9%）。
HELDIN_BUCKET_MAX = 70

#: 拆分派生文件（仓库内只有 held-in 明文；held-out 只存在于外部目录）。
HELDIN_FILENAME = "golden_tasks_heldin.jsonl"
HELDOUT_FILENAME = "golden_tasks_heldout.jsonl"
#: 外部目录里的并集母本（供 --verify 复核「并集 == 原集」）。
UNION_FILENAME = "golden_tasks_union.jsonl"
#: 仓库内的清单：只放 id + 每行指纹，不放 held-out 明文。
MANIFEST_FILENAME = "golden_sets_manifest.json"

#: held-out 外部目录名（``ensure_data_dir()/eval_heldout``）。
HELDOUT_DIRNAME = "eval_heldout"

#: held-out 路径覆盖（测试 / 多实例隔离 / 运维搬运）。
HELDOUT_PATH_ENV = "AN_GOLDEN_HELDOUT_PATH"

#: held-out 有效任务数下限。低于它视为「不可用」而不是「评估通过」——
#: 3 条任务的均值没有统计意义，拿它过 ±20% 闸等于掷硬币。
MIN_HELDOUT_TASKS = 5

#: golden 抽样种子的进程外注入入口。
SEED_ENV = "AN_GOLDEN_SEED"

#: 未注入时的固定默认种子。0 与既有 ``build_rsi_loop(seed=0)`` 口径一致，
#: 保证「没配环境变量」不会改变历史可比性。**可参数化**：调用方
#: （``resolve_golden_seed``）可显式传入 default 覆盖。
DEFAULT_GOLDEN_SEED = 0


# ── 分桶 ─────────────────────────────────────────────────────────────


def bucket_of(task_id: str) -> int:
    """``sha256(task_id)`` 前 8 字节 → ``[0, BUCKET_MODULUS)``。

    只依赖 id 本身：与文件顺序、随机种子、进程、Python 版本无关。
    """
    digest = hashlib.sha256(str(task_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % BUCKET_MODULUS


def is_heldin(task_id: str) -> bool:
    """True = 该 task 属于 held-in（仓库内明文可读的那一份）。"""
    return bucket_of(task_id) < HELDIN_BUCKET_MAX


def partition(tasks: Iterable[GoldenTask]) -> tuple[list[GoldenTask], list[GoldenTask]]:
    """按哈希桶把任务分成 ``(heldin, heldout)``，各自保持输入顺序。"""
    heldin: list[GoldenTask] = []
    heldout: list[GoldenTask] = []
    for task in tasks:
        (heldin if is_heldin(task.id) else heldout).append(task)
    return heldin, heldout


# ── 路径 ─────────────────────────────────────────────────────────────

_EVALS_DIR = Path(__file__).resolve().parent


def heldin_path() -> Path:
    """仓库内 held-in 集路径（随包分发）。"""
    return _EVALS_DIR / HELDIN_FILENAME


def compat_golden_path() -> Path:
    """历史文件名 ``golden_tasks.jsonl``（现值 = held-in 集，见 split 脚本）。

    保留它是为了**不断掉现有代码路径**（``run_evals`` / ``rsi_wiring``）。
    内容与 ``golden_tasks_heldin.jsonl`` 逐字节一致，由拆分脚本维护。
    """
    return _EVALS_DIR / "golden_tasks.jsonl"


def heldout_dir() -> Path:
    """held-out 外部目录：``AN_GOLDEN_HELDOUT_PATH`` 的父目录或数据目录。

    ``ensure_data_dir()`` 会做旧目录迁移（``~/.quantnodes`` → ``~/.agentnodes``）
    并建目录；解析失败时退回 ``Path.home()/.agentnodes``（与 ``halt.py``
    同款兜底），但**不**在 import 期调用——只有真正读写时才解析。
    """
    override = os.environ.get(HELDOUT_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser().parent
    try:
        from agentnodes.foundation.storage.sqlite import ensure_data_dir

        return ensure_data_dir() / HELDOUT_DIRNAME
    except Exception:  # noqa: BLE001 —— 数据目录不可解析时退回 home
        return Path.home() / ".agentnodes" / HELDOUT_DIRNAME


def heldout_path() -> Path:
    """held-out 集的外部路径（**仓库外**；默认 ``~/.agentnodes/eval_heldout``）。"""
    override = os.environ.get(HELDOUT_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return heldout_dir() / HELDOUT_FILENAME


def union_master_path() -> Path:
    """外部并集母本路径（与 held-out 同目录，同样在仓库外）。"""
    override = os.environ.get(HELDOUT_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser().parent / UNION_FILENAME
    return heldout_dir() / UNION_FILENAME


def manifest_path() -> Path:
    """仓库内清单路径（只含 id + 行指纹，不含明文）。"""
    return _EVALS_DIR / MANIFEST_FILENAME


def find_source_path(explicit: str | Path | None = None) -> Path | None:
    """拆分/导出的**唯一源**解析。

    解析顺序（先外后内，保证可重跑不丢 held-out）：

    1. 显式 ``--source``
    2. 外部并集母本 ``<heldout_dir>/golden_tasks_union.jsonl``
    3. 仓库内 ``golden_tasks.jsonl``（首次拆分前 = 原 70 条全集）

    第 2 步是关键：拆分完成后仓库里的 ``golden_tasks.jsonl`` 只剩 held-in
    明文（held-out 不能进仓库），若再以它为源重跑拆分，held-out 会被
    永久截掉。故并集母本住在仓库外，与本模块的 held-out 同目录。
    """
    if explicit is not None:
        target = Path(explicit)
        return target if target.exists() else None
    master = union_master_path()
    if master.exists():
        return master
    legacy = compat_golden_path()
    if legacy.exists():
        return legacy
    return None


def ids_digest(task_ids: Iterable[str]) -> str:
    """id 集合的聚合指纹（排序后逐行 join 再 sha256）。

    用途：清单里记录 held-out 的 id 集合指纹而**不记录 id 明文**——
    既能校验「导出的是同一份集合」，又不会把 held-out 目录交给读仓库的人。
    """
    payload = "\n".join(sorted(str(t) for t in task_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rows_digest(rows: Iterable[dict[str, Any]]) -> str:
    """行集合的聚合指纹（按 id 排序后逐行规范化 join）。"""
    ordered = sorted(rows, key=lambda r: str(r.get("id", "")))
    payload = "\n".join(canonical_row(r) for r in ordered)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def split_rows(
    rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按 :func:`is_heldin` 拆分原始 dict 行（保持源顺序）。"""
    heldin: list[dict[str, Any]] = []
    heldout: list[dict[str, Any]] = []
    for row in rows:
        (heldin if is_heldin(str(row.get("id", ""))) else heldout).append(row)
    return heldin, heldout


def render_rows(rows: Iterable[dict[str, Any]]) -> str:
    """把 dict 行渲染成 jsonl 文本（末尾换行；UTF-8 不转义）。"""
    lines = [canonical_row(row) for row in rows]
    return "\n".join(lines) + ("\n" if lines else "")


# ── 行规范化 / 指纹 ──────────────────────────────────────────────────


def canonical_row(row: dict[str, Any]) -> str:
    """规范化 JSON（键排序、紧凑分隔符）——指纹与比对都基于它。"""
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def row_fingerprint(row: dict[str, Any]) -> str:
    """单行内容的 sha256（判断「两集并集是否等于原集」用）。"""
    return hashlib.sha256(canonical_row(row).encode("utf-8")).hexdigest()


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    """读 jsonl 的原始 dict 行（跳过空行与 ``#`` 注释行）。"""
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        rows.append(json.loads(text))
    return rows


def read_lines(path: str | Path) -> list[str]:
    """读 jsonl 的**原始行文本**（跳过空行与 ``#`` 注释行）。

    拆分产物必须保留原行的逐字节文本：用 ``json.loads`` → ``json.dumps``
    往返会重排键、改空白，让 ``golden_tasks.jsonl`` 的 diff 变成全文重写
    （47 行全改），既掩盖真实变更也让 review 无从下手。这里保留原文，
    产物就是「原文件的行子集」——diff 只有删除，没有改写。
    """
    lines: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        lines.append(line.rstrip("\r\n"))
    return lines


def render_lines(lines: Iterable[str]) -> str:
    """把原始行文本渲染成 jsonl（末尾换行；空输入 → 空串）。"""
    kept = [line for line in lines]
    return "\n".join(kept) + ("\n" if kept else "")


# ── 加载 ─────────────────────────────────────────────────────────────


def load_heldin_tasks(path: str | Path | None = None) -> list[GoldenTask]:
    """加载 held-in 集（仓库内）。缺文件时返回空表，由调用方决定语义。"""
    target = Path(path) if path is not None else heldin_path()
    if not target.exists():
        # 兼容：旧检出只有 golden_tasks.jsonl（未拆分）。此时它就是 held-in
        # 的超集，但至少是有效任务集——调用方会 emit 降级提示。
        legacy = compat_golden_path()
        if path is None and legacy.exists():
            logger.warning(
                "held-in set missing at %s; falling back to %s (run "
                "scripts/arch/split_golden_sets.py)", target, legacy,
            )
            target = legacy
        else:
            logger.warning("held-in set missing: %s", target)
            return []
    return load_golden_tasks(target)


@dataclass(frozen=True)
class HeldoutStatus:
    """held-out 集的可用性快照（**永不抛异常**，供 gate 判「无法验证」）。"""

    available: bool
    path: Path
    tasks: list[GoldenTask] = field(default_factory=list)
    reason: str = ""

    @property
    def count(self) -> int:
        return len(self.tasks)


def load_heldout(
    path: str | Path | None = None,
    *,
    min_tasks: int = MIN_HELDOUT_TASKS,
) -> HeldoutStatus:
    """只从**仓库外**路径加载 held-out 集。

    返回 ``HeldoutStatus``：文件缺失 / 为空 / 条数不足 / 解析失败都映射成
    ``available=False`` + ``reason``——绝不静默降级成「只有 held-in 单集」。
    调用方（validation_gate）拿到 ``available=False`` 后必须拒绝激活。
    """
    target = Path(path) if path is not None else heldout_path()
    if not target.exists():
        return HeldoutStatus(False, target, reason=f"held-out set not found: {target}")
    try:
        rows = read_rows(target)
    except Exception as exc:  # noqa: BLE001 —— 读不出 = 不可用
        return HeldoutStatus(False, target, reason=f"held-out unreadable: {exc}")
    if not rows:
        return HeldoutStatus(False, target, reason=f"held-out set is empty: {target}")
    try:
        tasks = [GoldenTask.from_dict(row) for row in rows]
    except Exception as exc:  # noqa: BLE001 —— 字段缺失/类型错 = 不可用
        return HeldoutStatus(False, target, reason=f"held-out malformed: {exc}")
    if len(tasks) < min_tasks:
        return HeldoutStatus(
            False, target, tasks,
            reason=(
                f"held-out has {len(tasks)} tasks < min {min_tasks}: "
                "insufficient for a degradation gate"
            ),
        )
    return HeldoutStatus(True, target, tasks, reason="ok")


class HeldoutUnavailable(RuntimeError):
    """held-out 集不可用（缺失 / 空 / 条数不足）——「无法验证」，非「变差」。"""


# ── seed 进程外注入 ─────────────────────────────────────────────────


def resolve_golden_seed(
    *,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
    default: int = DEFAULT_GOLDEN_SEED,
    environ: dict[str, str] | None = None,
) -> tuple[int, str]:
    """解析 golden 抽样种子，返回 ``(seed, source)``。

    解析顺序：``AN_GOLDEN_SEED`` → ``default``。**未设或非法时 emit**
    ``rsi.golden_seed``（``source=default``），让「用的是默认种子」这件事
    在事件流里可见——种子是评测指纹的一部分，静默用默认值等于允许
    候选按固定抽样过拟合而没人知道。

    :param emit: ``(kind, data) -> None``；None = 只记 WARNING 日志
    :param default: 未注入时的固定种子（可参数化）
    :param environ: 环境字典（测试注入；None = ``os.environ``）
    """
    env = os.environ if environ is None else environ
    raw = str(env.get(SEED_ENV, "") or "").strip()
    source = "env"
    if raw:
        try:
            return int(raw), source
        except ValueError:
            logger.warning("invalid %s=%r, falling back to %d", SEED_ENV, raw, default)
            source = "default_invalid_env"
    else:
        source = "default"
        logger.info(
            "%s not set; using fixed default seed %d (set it to inject the "
            "seed from outside the process)", SEED_ENV, default,
        )

    if emit is not None:
        try:
            emit("rsi.golden_seed", {
                "seed": int(default),
                "source": source,
                "env": SEED_ENV,
                "note": (
                    "seed taken from the fixed default — inject "
                    f"{SEED_ENV} to make the sampling externally controlled"
                ),
            })
        except Exception:  # noqa: BLE001 —— 提示发不出去不影响评测
            logger.warning("golden seed emit failed", exc_info=True)
    return int(default), source


# ── 双集评测的三个可调用对象（供 validation_gate 注入）───────────────


def _metric_of(value: Any) -> float:
    """从 replay 返回值里取能力标量。

    ``make_golden_replay`` 可能返回 ``ReplayOutcome``（W2 的 D3 双闸：
    能力 + 成本并列维度），也可能是裸 ``float``。held-out 门只判**能力**
    维度：成本闸在 held-in 侧已经判过，且 held-out 的任务子集与 held-in
    不同，把两侧成本混起来会重复惩罚同一个改动。成本读数由 held-in 侧
    的 ``ReplayOutcome`` 负责，这里只取 ``metric``。
    """
    metric = getattr(value, "metric", None)
    if metric is not None:
        return float(metric)
    return float(value)


def make_heldout_replay(
    *,
    seed: int,
    size: int | None = None,
    workspace: Path | None = None,
    max_iterations: int = 6,
    runner: Callable[[Any, Path, int], Any] | None = None,
    apply_proposal: Callable[[Any], Any] | None = None,
    status_holder: list[HeldoutStatus] | None = None,
) -> tuple[Callable[..., Any], Callable[[], Any], HeldoutStatus]:
    """构造 held-out 侧的 ``(replay, baseline, status)`` 三元组。

    - ``replay(proposal, holdout) -> float``：在 held-out 集上跑**候选**
      （会经 ``apply_proposal`` 临时应用提案）
    - ``baseline() -> float``：在**同一批** held-out 任务上跑**当前配置**
      （**不**应用提案——基线一旦跑成候选配置，两边必然同分，「无退化」
      就永远成立，那是一道自证闸）
    - ``status``：加载结果的快照（调用方不必再读一次文件）

    held-out 不可用时：``baseline()`` / ``replay()`` 抛
    :class:`HeldoutUnavailable`，由 ``ValidationGate`` 判「无法验证 →
    不得激活」。**不返回 0 分**（0 分会被当成真实退化，进而被误判成
    「候选变差」而不是「没法测」）。

    :param status_holder: 若给出，会把状态对象放进列表（调用方可读
        ``status_holder[0]`` 做诊断，而不用再读一次文件）
    """
    from agentnodes.kernel.evolution.golden_eval import (
        DEFAULT_SAMPLE_SIZE,
        make_golden_replay,
    )

    status = load_heldout()
    if status_holder is not None:
        status_holder.append(status)

    sample_size = DEFAULT_SAMPLE_SIZE if size is None else size

    def _require_tasks() -> list[GoldenTask]:
        if not status.available:
            raise HeldoutUnavailable(status.reason)
        return status.tasks

    def _baseline() -> float:
        # 刻意不传 apply_proposal：基线 = 当前配置
        replay = make_golden_replay(
            _require_tasks(),
            seed=seed,
            size=sample_size,
            workspace=workspace,
            max_iterations=max_iterations,
            runner=runner,
        )
        return _metric_of(replay(_BaselineProbe(), None))

    def _replay(proposal: Any, holdout: Any) -> float:
        replay = make_golden_replay(
            _require_tasks(),
            seed=seed,
            size=sample_size,
            workspace=workspace,
            max_iterations=max_iterations,
            runner=runner,
            apply_proposal=apply_proposal,
        )
        return _metric_of(replay(proposal, holdout))

    return _replay, _baseline, status


class _BaselineProbe:
    """基线跑用的占位提案。

    ``make_golden_replay`` 只读 ``proposal_id``（日志用），不会把它交给
    ``apply_proposal``——基线那条路径根本没传 applier。这里仍给一个显式的
    占位对象而不是 ``None``：日志与异常栈里能一眼看出这是基线轮。
    """

    proposal_id = "heldout-baseline"
    target_component = ""
    change: dict[str, Any] = {}


def heldout_gate_kwargs(
    *,
    seed: int,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
    size: int | None = None,
    max_iterations: int = 6,
    runner: Callable[[Any, Path, int], Any] | None = None,
    apply_proposal: Callable[[Any], Any] | None = None,
    trip_halt: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """构造 ``ValidationGate(heldout_*=...)`` 所需的全部关键字参数。

    组装根（L1 ``rsi_wiring``）只需把它 **解包进** ``ValidationGate(...)``：

        gate = ValidationGate(..., **heldout_gate_kwargs(seed=seed, ...))

    这样双集的全部路径/策略知识都留在 ``evals`` 一侧，L1 只负责接线，
    kernel 的闸不认识文件系统。

    ``trip_halt`` **只在非 None 时**放进返回的 dict：调用方自己写
    ``trip_halt=...`` 时不该被这里的 None 覆盖（那会变成一个
    "multiple values for keyword argument" 的 TypeError，而不是
    "护栏未注入"的可诊断状态）。

    **held-out 不可用时也返回完整 kwargs**（``heldout_replay`` /
    ``heldout_baseline`` 仍非 None，调用时才抛 ``HeldoutUnavailable``）：
    这样 gate 能区分「装配没接」（unwired）与「文件不在」（missing），
    两种情况在事件里的 ``kind`` 不同，运维才知道该修接线还是修数据。
    启动时额外 emit 一条 ``rsi.heldout_status`` 让缺失在链路一开始就可见。
    """
    status = load_heldout()
    if emit is not None:
        try:
            emit("rsi.heldout_status", {
                "available": status.available,
                "path": str(status.path),
                "count": status.count,
                "reason": status.reason,
                "min_tasks": MIN_HELDOUT_TASKS,
            })
        except Exception:  # noqa: BLE001 —— 状态事件发不出去不影响接线
            logger.warning("heldout status emit failed", exc_info=True)
    if not status.available:
        logger.warning(
            "held-out unavailable (%s); held-out gate will reject every "
            "proposal until it is exported (scripts/arch/export_heldout.py)",
            status.reason,
        )

    replay, baseline, _ = make_heldout_replay(
        seed=seed,
        size=size,
        max_iterations=max_iterations,
        runner=runner,
        apply_proposal=apply_proposal,
    )
    kwargs: dict[str, Any] = {
        "heldout_replay": replay,
        "heldout_baseline": baseline,
    }
    if trip_halt is not None:
        kwargs["trip_halt"] = trip_halt
    return kwargs


def apply_proposal_wrapper(
    apply_proposal: Callable[[Any], Any] | None,
) -> Callable[[Any], Any] | None:
    """把 applier 包一层：跳过基线占位提案（``_BaselineProbe``）。

    held-out 的 **基线**本来就不传 applier（见 ``make_heldout_replay``），
    所以这条包装只防「调用方自己复用同一个 replay 跑基线」的失误：
    基线一旦跑成候选配置，两侧必然同分，「无退化」永远成立。
    """
    if apply_proposal is None:
        return None

    def _apply(proposal: Any) -> Any:
        if isinstance(proposal, _BaselineProbe):
            return None
        return apply_proposal(proposal)

    return _apply


__all__ = [
    "BUCKET_MODULUS",
    "DEFAULT_GOLDEN_SEED",
    "HELDOUT_DIRNAME",
    "HELDOUT_FILENAME",
    "HELDOUT_PATH_ENV",
    "HELDIN_BUCKET_MAX",
    "HELDIN_FILENAME",
    "HeldoutStatus",
    "HeldoutUnavailable",
    "MANIFEST_FILENAME",
    "MIN_HELDOUT_TASKS",
    "SEED_ENV",
    "SPLIT_VERSION",
    "UNION_FILENAME",
    "bucket_of",
    "canonical_row",
    "compat_golden_path",
    "find_source_path",
    "heldin_path",
    "heldout_dir",
    "heldout_gate_kwargs",
    "heldout_path",
    "ids_digest",
    "is_heldin",
    "load_heldin_tasks",
    "load_heldout",
    "make_heldout_replay",
    "manifest_path",
    "partition",
    "read_rows",
    "read_lines",
    "render_lines",
    "render_rows",
    "resolve_golden_seed",
    "row_fingerprint",
    "rows_digest",
    "split_rows",
    "union_master_path",
]
