"""New Arrivals → Magento sync.

Paste a list of item names/codes, resolve them to item_code_365 (= Magento SKU)
against ps_items_dw, then REPLACE the contents of the Magento
'ΝΕΑ ΠΑΡΑΛΑΒΗ' category (default ID 132) via the Magento REST API.

Env:
  MAGENTO_NEW_ARRIVALS_CATEGORY_ID  (default: 132)
Uses existing M2_* OAuth secrets + MAGENTO_BASE_URL.
"""
import os
import re
import json
import logging
import urllib.parse

from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from sqlalchemy import text

from app import db
from integrations.magento_rest_oauth import (
    magento_rest_get,
    magento_rest_put,
    magento_rest_delete,
)

logger = logging.getLogger(__name__)

new_arrivals_bp = Blueprint("new_arrivals", __name__, url_prefix="/new-arrivals-sync")

CODE_RE = re.compile(r"^[A-Z]{2,4}-\d{3,5}$")
LINE_PREFIX_RE = re.compile(r"^\s*\d+[\.\)\-]\s*")  # strips "1." / "2)" / "3-"


def _category_id() -> str:
    return os.getenv("MAGENTO_NEW_ARRIVALS_CATEGORY_ID", "132")


def _role_ok():
    from services.permissions import has_permission
    return current_user.role == "admin" or has_permission(current_user, "menu.warehouse")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).upper()


def _resolve_line(line: str):
    """Resolve one pasted line to an item. Returns dict with match info."""
    raw = line
    q = _norm(LINE_PREFIX_RE.sub("", line))
    if not q:
        return None

    # Direct item code (e.g. ICC-0014)
    if CODE_RE.match(q):
        row = db.session.execute(text("""
            SELECT item_code_365, item_name, active
            FROM ps_items_dw WHERE upper(item_code_365) = :q LIMIT 1
        """), {"q": q}).mappings().first()
        if row:
            return {"input": raw, "status": "matched", "match_type": "code",
                    "item_code": row["item_code_365"], "item_name": row["item_name"],
                    "active": bool(row["active"]), "candidates": []}
        return {"input": raw, "status": "unmatched", "match_type": None,
                "item_code": None, "item_name": None, "active": None, "candidates": []}

    # Exact name match (case/whitespace-insensitive)
    row = db.session.execute(text("""
        SELECT item_code_365, item_name, active
        FROM ps_items_dw
        WHERE upper(regexp_replace(item_name, '\\s+', ' ', 'g')) = :q
        ORDER BY active DESC LIMIT 1
    """), {"q": q}).mappings().first()
    if row:
        return {"input": raw, "status": "matched", "match_type": "exact",
                "item_code": row["item_code_365"], "item_name": row["item_name"],
                "active": bool(row["active"]), "candidates": []}

    # Fuzzy: all words must appear in the name
    words = [w for w in q.split(" ") if w]
    like_clauses = " AND ".join(f"upper(item_name) LIKE :w{i}" for i in range(len(words)))
    params = {f"w{i}": f"%{w}%" for i, w in enumerate(words)}
    rows = db.session.execute(text(f"""
        SELECT item_code_365, item_name, active
        FROM ps_items_dw WHERE {like_clauses}
        ORDER BY active DESC, length(item_name) ASC LIMIT 5
    """), params).mappings().all()

    if len(rows) == 1:
        r = rows[0]
        return {"input": raw, "status": "matched", "match_type": "fuzzy",
                "item_code": r["item_code_365"], "item_name": r["item_name"],
                "active": bool(r["active"]), "candidates": []}
    if rows:
        return {"input": raw, "status": "ambiguous", "match_type": None,
                "item_code": None, "item_name": None, "active": None,
                "candidates": [{"item_code": r["item_code_365"],
                                "item_name": r["item_name"],
                                "active": bool(r["active"])} for r in rows]}
    return {"input": raw, "status": "unmatched", "match_type": None,
            "item_code": None, "item_name": None, "active": None, "candidates": []}


@new_arrivals_bp.route("/")
@login_required
def page():
    if not _role_ok():
        return render_template("403.html"), 403
    return render_template("new_arrivals_sync.html", category_id=_category_id())


@new_arrivals_bp.route("/api/resolve", methods=["POST"])
@login_required
def api_resolve():
    if not _role_ok():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    lines = (payload.get("text") or "").splitlines()
    results = []
    for line in lines:
        r = _resolve_line(line)
        if r:
            results.append(r)
    return jsonify({"ok": True, "results": results})


@new_arrivals_bp.route("/api/search")
@login_required
def api_search():
    if not _role_ok():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    q = _norm(request.args.get("q") or "")
    if len(q) < 2:
        return jsonify({"ok": True, "items": []})
    words = [w for w in q.split(" ") if w]
    like = " AND ".join(
        f"(upper(item_name) LIKE :w{i} OR upper(item_code_365) LIKE :w{i})"
        for i in range(len(words)))
    params = {f"w{i}": f"%{w}%" for i, w in enumerate(words)}
    rows = db.session.execute(text(f"""
        SELECT item_code_365, item_name, active FROM ps_items_dw
        WHERE {like} ORDER BY active DESC, item_name LIMIT 10
    """), params).mappings().all()
    return jsonify({"ok": True, "items": [
        {"item_code": r["item_code_365"], "item_name": r["item_name"],
         "active": bool(r["active"])} for r in rows]})


@new_arrivals_bp.route("/api/current")
@login_required
def api_current():
    """Products currently assigned to the new-arrivals category in Magento."""
    if not _role_ok():
        return jsonify({"ok": False, "error": "forbidden"}), 403
    cat = _category_id()
    status, body = magento_rest_get(f"/rest/V1/categories/{cat}/products")
    if status != 200:
        return jsonify({"ok": False, "error": f"Magento {status}: {body[:300]}"}), 502
    links = json.loads(body)
    return jsonify({"ok": True, "category_id": cat,
                    "skus": [l.get("sku") for l in links]})


@new_arrivals_bp.route("/api/notify", methods=["POST"])
@login_required
def api_notify():
    """Broadcast a OneSignal push listing the new-arrival items, deep-linking
    to the ΝΕΑ ΠΑΡΑΛΑΒΗ category in the mobile app."""
    if not _role_ok():
        return jsonify({"ok": False, "error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    names = [n.strip() for n in (payload.get("item_names") or []) if n and n.strip()]
    if not names:
        return jsonify({"ok": False, "error": "No items provided"}), 400

    cat = _category_id()
    title = "Νέα Παραλαβή 📦"

    max_list = 6
    listed = names[:max_list]
    body = "Μόλις παραλάβαμε: " + ", ".join(listed)
    if len(names) > max_list:
        body += f" και άλλα {len(names) - max_list} προϊόντα"
    body += ". Δείτε τα στην κατηγορία Νέα Παραλαβή!"

    from services.onesignal_service import send_broadcast_push
    result = send_broadcast_push(
        title=title,
        body=body,
        push_target_type="category",
        category_id=cat,
        source_screen="new_arrivals_sync",
        username=getattr(current_user, "username", None),
    )

    logger.info("New-arrivals push broadcast by %s: ok=%s recipients=%s",
                getattr(current_user, "username", "?"),
                result.get("ok"), result.get("recipients"))

    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error") or "Push failed"}), 502
    return jsonify({"ok": True, "message_id": result.get("message_id"),
                    "recipients": result.get("recipients"),
                    "title": title, "body": body})


@new_arrivals_bp.route("/api/sync", methods=["POST"])
@login_required
def api_sync():
    """REPLACE the category contents with the given SKUs."""
    if not _role_ok():
        return jsonify({"ok": False, "error": "forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    skus = [s.strip() for s in (payload.get("skus") or []) if s and s.strip()]
    if not skus:
        return jsonify({"ok": False, "error": "No SKUs provided"}), 400
    skus = list(dict.fromkeys(skus))  # dedupe, keep order

    cat = _category_id()

    # 1) Current contents
    status, body = magento_rest_get(f"/rest/V1/categories/{cat}/products")
    if status != 200:
        return jsonify({"ok": False,
                        "error": f"Cannot read category {cat}: Magento {status}: {body[:300]}"}), 502
    current = [l.get("sku") for l in json.loads(body)]

    new_set = set(skus)
    to_remove = [s for s in current if s not in new_set]
    to_add = [s for s in skus if s not in set(current)]

    removed, remove_errors = [], []
    for sku in to_remove:
        enc = urllib.parse.quote(sku, safe="")
        st, tx = magento_rest_delete(f"/rest/V1/categories/{cat}/products/{enc}")
        if 200 <= st < 300:
            removed.append(sku)
        else:
            remove_errors.append({"sku": sku, "status": st, "error": tx[:200]})

    added, add_errors = [], []
    for pos, sku in enumerate(skus):
        if sku not in to_add:
            continue
        st, tx = magento_rest_put(f"/rest/V1/categories/{cat}/products", body={
            "productLink": {"sku": sku, "position": pos, "category_id": cat}
        })
        if 200 <= st < 300:
            added.append(sku)
        else:
            add_errors.append({"sku": sku, "status": st, "error": tx[:200]})

    ok = not remove_errors and not add_errors
    logger.info("New-arrivals sync by %s: cat=%s added=%d removed=%d kept=%d errors=%d",
                getattr(current_user, "username", "?"), cat, len(added), len(removed),
                len(current) - len(to_remove), len(remove_errors) + len(add_errors))

    return jsonify({
        "ok": ok,
        "category_id": cat,
        "added": added,
        "removed": removed,
        "kept": [s for s in current if s in new_set],
        "errors": remove_errors + add_errors,
    })
