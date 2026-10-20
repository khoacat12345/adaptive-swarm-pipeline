"""Cau noi de chay duoc `python -m agents.<module>`.

Python KHONG ho tro ten module bat dau bang dau cham, nen `python -m .agents.scout`
luon bao loi: "Relative module names not supported". Package `agents/` nay chi lam
mot viec duy nhat: dua thu muc that `.agents/` vao sys.path roi chuyen tiep.

Neu khong can dang lenh `python -m agents.X`, co the xoa ca thu muc `agents/` va
dung truc tiep: `python .agents\\scout.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REAL_PACKAGE = Path(__file__).resolve().parent.parent / ".agents"
if str(REAL_PACKAGE) not in sys.path:
    sys.path.insert(0, str(REAL_PACKAGE))
