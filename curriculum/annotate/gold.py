"""金标生产流程（CLAUDE.md 4.3 节）的通用实现：单标签任务。

流程：首轮两个不同厂商模型独立盲标 → 一致条目直接采纳 → 不一致条目交强模型思考模式仲裁
（可见双方结论与理由）→ 仲裁置信度低于阈值进入人工队列。
每次模型调用都以 Judgment 记录落盘（eval/annotation/<task>/judgments.jsonl），可审计、可重放
（重放命中 .cache/，不产生费用）。

集合型任务（锚点召回、检索探针）有各自的分解逻辑，复用本模块的 `call_models` 与 `judgment_record`。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Hashable, Optional

from curriculum.annotate.client import AnnotationClient, AnnotationRequest, AnnotationResult
from curriculum.common import EVAL_DIR, write_jsonl
from curriculum.metrics import cohen_kappa, krippendorff_alpha_nominal

ARBITRATION_CONFIDENCE_THRESHOLD = 0.7


@dataclass(frozen=True)
class ModelConfig:
    model: str
    thinking: bool
    max_tokens: int = 1024

    @property
    def tag(self) -> str:
        return f"{self.model}{'+think' if self.thinking else ''}"


@dataclass
class LabelTask:
    name: str  # 对应 eval/annotation/<name>/
    render: Callable[[dict], str]  # 条目 → 用户消息正文
    validate: Callable[[Any], bool]
    extract: Callable[[Any], Hashable]  # 模型输出 → 用于一致性比较的标签
    guideline_path: Optional[Path] = None
    system_suffix: str = ""

    def system_prompt(self) -> str:
        path = self.guideline_path or (EVAL_DIR / "annotation" / self.name / "guideline.md")
        return path.read_text(encoding="utf-8") + self.system_suffix


def judgment_record(
    task_type: str, item_id: str, role: str, res: AnnotationResult, input_summary: str, conclusion: str
) -> dict:
    parsed = res.parsed if isinstance(res.parsed, dict) else {}
    conf = parsed.get("confidence", 0.0)
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "id": f"j.{task_type}.{role}.{res.model}{'+think' if res.thinking else ''}.{item_id}",
        "task_type": task_type,
        "item_id": item_id,
        "role": role,
        "input_summary": input_summary[:500],
        "conclusion": conclusion,
        "confidence": conf,
        "reasoning": str(parsed.get("reason", ""))[:500],
        "model": res.model,
        "mode": "thinking" if res.thinking else "non_thinking",
        "prompt_hash": res.prompt_hash,
        "timestamp": res.created_at or "unknown",
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "cost_cny": str(res.cost_cny),
        "ok": res.ok,
        "error": res.error,
    }


def call_models(
    client: AnnotationClient,
    system: str,
    items: list[tuple[str, str]],  # (item_id, user message)
    cfg: ModelConfig,
    validate: Callable[[Any], bool],
    role: str = "r1",
) -> dict[str, AnnotationResult]:
    reqs = [
        AnnotationRequest(
            request_id=f"{role}:{iid}",
            model=cfg.model,
            thinking=cfg.thinking,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": msg}],
            response_schema_validator=validate,
            max_tokens=cfg.max_tokens,
        )
        for iid, msg in items
    ]
    results = client.run_batch(reqs, label=f"{role}:{cfg.tag}")
    return {r.request_id.split(":", 1)[1]: r for r in results}


@dataclass
class GoldRunResult:
    gold: list[dict]
    judgments: list[dict]
    stats: dict = field(default_factory=dict)


def run_label_gold(
    client: AnnotationClient,
    task: LabelTask,
    items: list[dict],  # 每条至少含 id；可含 split / stratum
    annotators: tuple[ModelConfig, ModelConfig],
    arbiter: ModelConfig,
    arbiter_extra_instruction: str = "",
    judgment_task_type: Optional[str] = None,
) -> GoldRunResult:
    """跑完整金标流程，返回金标（每条：item 字段 + label + source + 两位标注者与仲裁的结论）。"""
    tt = judgment_task_type or task.name
    system = task.system_prompt()
    msgs = [(it["id"], task.render(it)) for it in items]
    r1 = {cfg.tag: call_models(client, system, msgs, cfg, task.validate, role="r1") for cfg in annotators}
    judgments = []
    a_tag, b_tag = annotators[0].tag, annotators[1].tag

    disagreements = []
    labels_a, labels_b = [], []
    for it, (_, msg) in zip(items, msgs):
        ra, rb = r1[a_tag][it["id"]], r1[b_tag][it["id"]]
        for tag, res in ((a_tag, ra), (b_tag, rb)):
            judgments.append(judgment_record(tt, it["id"], "r1", res, msg, json.dumps(res.parsed, ensure_ascii=False) if res.ok else "ERROR"))
        la = task.extract(ra.parsed) if ra.ok else None
        lb = task.extract(rb.parsed) if rb.ok else None
        labels_a.append(la)
        labels_b.append(lb)
        if la is None or lb is None or la != lb:
            disagreements.append((it, msg, ra, rb))

    arb_msgs = []
    for it, msg, ra, rb in disagreements:
        arb_msgs.append(
            (
                it["id"],
                msg
                + "\n\n---\n以下是两位标注者的独立结论（可能都对、一对一错或都错），请你独立判断后给出最终结论：\n"
                + f"标注者甲：{json.dumps(ra.parsed, ensure_ascii=False) if ra.ok else '（无有效输出）'}\n"
                + f"标注者乙：{json.dumps(rb.parsed, ensure_ascii=False) if rb.ok else '（无有效输出）'}\n"
                + arbiter_extra_instruction,
            )
        )
    arb = call_models(client, system, arb_msgs, arbiter, task.validate, role="arb") if arb_msgs else {}
    arb_msg_by_id = dict(arb_msgs)

    gold = []
    counts = {"consensus": 0, "arbitrated": 0, "human_queue": 0, "failed": 0}
    for it, la, lb in zip(items, labels_a, labels_b):
        row = {k: v for k, v in it.items() if not k.startswith("_")}
        row["annotator_labels"] = {a_tag: _jsonable(la), b_tag: _jsonable(lb)}
        row["annotator_outputs"] = {a_tag: r1[a_tag][it["id"]].parsed, b_tag: r1[b_tag][it["id"]].parsed}
        if la is not None and la == lb:
            row["label"] = _jsonable(la)
            row["source"] = "consensus"
            # 一致采纳时，保留标注者甲的完整输出（如 extends 方向）
            row["output"] = r1[a_tag][it["id"]].parsed
        else:
            res = arb.get(it["id"])
            judgments.append(judgment_record(tt, it["id"], "arb", res, arb_msg_by_id[it["id"]], json.dumps(res.parsed, ensure_ascii=False) if res.ok else "ERROR"))
            if not res.ok:
                row["label"], row["source"] = None, "failed"
            else:
                row["label"] = _jsonable(task.extract(res.parsed))
                row["output"] = res.parsed
                conf = _conf(res.parsed)
                row["arbiter_confidence"] = conf
                row["source"] = "arbitrated" if conf >= ARBITRATION_CONFIDENCE_THRESHOLD else "human_queue"
        counts[row["source"]] += 1
        gold.append(row)

    valid = [(a, b) for a, b in zip(labels_a, labels_b) if a is not None and b is not None]
    stats = {
        "n": len(items),
        "annotators": [a_tag, b_tag],
        "arbiter": arbiter.tag,
        "counts": counts,
        "raw_agreement": round(sum(1 for a, b in valid if a == b) / len(valid), 4) if valid else None,
        "cohen_kappa": cohen_kappa([_h(a) for a, _ in valid], [_h(b) for _, b in valid]),
        "krippendorff_alpha": krippendorff_alpha_nominal([[_h(a), _h(b)] for a, b in valid]),
        "cost_cny": str(sum((Decimal(j["cost_cny"]) for j in judgments), Decimal("0"))),
    }
    return GoldRunResult(gold=gold, judgments=judgments, stats=stats)


def save_gold(task_name: str, result: GoldRunResult) -> None:
    ann_dir = EVAL_DIR / "annotation" / task_name
    write_jsonl(ann_dir / "judgments.jsonl", result.judgments)
    (ann_dir / "stats.json").write_text(json.dumps(result.stats, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for split in ("val", "test"):
        rows = [g for g in result.gold if g.get("split") == split]
        write_jsonl(EVAL_DIR / "gold" / split / f"{task_name}.jsonl", rows)


def _conf(parsed: Any) -> float:
    try:
        return max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _jsonable(x):
    if isinstance(x, tuple):
        return list(x)
    return x


def _h(x):
    return tuple(x) if isinstance(x, list) else x
