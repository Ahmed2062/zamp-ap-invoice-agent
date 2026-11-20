"""Reference data: the 'procurement system' the agent checks invoices against."""
from __future__ import annotations

import json
from pathlib import Path

from rapidfuzz import fuzz

from app.utils.textnorm import norm_company


class Reference:
    def __init__(self, ref_dir: Path):
        self.ref_dir = ref_dir
        self.reload()

    def reload(self) -> None:
        load = lambda name: json.loads((self.ref_dir / name).read_text())  # noqa: E731
        self.company: dict = load("company.json")
        self.vendors: list[dict] = load("vendors.json")
        self.purchase_orders: list[dict] = load("purchase_orders.json")
        self.goods_receipts: list[dict] = load("goods_receipts.json")
        self.policy: dict = load("policy.json")
        self._by_id = {v["vendor_id"]: v for v in self.vendors}
        self._by_gstin = {v["gstin"]: v for v in self.vendors}
        self._po = {p["po_number"]: p for p in self.purchase_orders}

    def vendor(self, vendor_id: str | None) -> dict | None:
        return self._by_id.get(vendor_id) if vendor_id else None

    def vendor_by_gstin(self, gstin: str | None) -> dict | None:
        return self._by_gstin.get(gstin) if gstin else None

    def vendor_by_name(self, name: str | None, min_score: int = 85) -> tuple[dict | None, int]:
        if not name:
            return None, 0
        target = norm_company(name)
        best, best_score = None, 0
        for v in self.vendors:
            for cand in [v["name"], *v.get("aliases", [])]:
                c = norm_company(cand)
                score = max(fuzz.token_set_ratio(target, c), fuzz.ratio(target.replace(" ", ""), c.replace(" ", "")))
                if score > best_score:
                    best, best_score = v, int(score)
        return (best, best_score) if best_score >= min_score else (None, best_score)

    def po(self, po_number: str | None) -> dict | None:
        return self._po.get(po_number) if po_number else None

    def open_pos_for_vendor(self, vendor_id: str) -> list[dict]:
        return [p for p in self.purchase_orders if p["vendor_id"] == vendor_id and p["status"] == "open"]

    def received_qty(self, po_number: str) -> dict[int, float]:
        out: dict[int, float] = {}
        for g in self.goods_receipts:
            if g["po_number"] == po_number:
                for line_no, qty in g["lines"].items():
                    out[int(line_no)] = out.get(int(line_no), 0) + float(qty)
        return out

    def grns_for_po(self, po_number: str) -> list[dict]:
        return [g for g in self.goods_receipts if g["po_number"] == po_number]
