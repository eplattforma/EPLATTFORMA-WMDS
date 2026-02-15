from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from services.cypost_client import CyprusPostClient
from app import db
from datetime import datetime, timedelta
import json
import hashlib

cypost_bp = Blueprint("cypost", __name__, url_prefix="/api/cypost")

class PostalLookupCache(db.Model):
    __tablename__ = 'postal_lookup_cache'
    id = db.Column(db.Integer, primary_key=True)
    cache_key = db.Column(db.String(256), unique=True, index=True)
    request_json = db.Column(db.Text)
    response_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def get_cached_response(key, expiry_days=30):
    cached = PostalLookupCache.query.filter_by(cache_key=key).first()
    if cached:
        if datetime.utcnow() - cached.created_at < timedelta(days=expiry_days):
            return json.loads(cached.response_json)
        else:
            db.session.delete(cached)
            db.session.commit()
    return None

def set_cached_response(key, response_data):
    # Cleanup old entry if exists
    PostalLookupCache.query.filter_by(cache_key=key).delete()
    new_cache = PostalLookupCache(
        cache_key=key,
        request_json="{}",
        response_json=json.dumps(response_data),
        created_at=datetime.utcnow()
    )
    db.session.add(new_cache)
    db.session.commit()

@cypost_bp.route("/districts")
@login_required
def get_districts():
    client = CyprusPostClient()
    cache_key = f"districts|lng={client.lng}"
    cached = get_cached_response(cache_key, 30)
    if cached: return jsonify(cached)
    
    try:
        data = client.districts()
        set_cached_response(cache_key, data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cypost_bp.route("/areas")
@login_required
def get_areas():
    district = request.args.get("district")
    if not district: return jsonify({"error": "district required"}), 400
    
    client = CyprusPostClient()
    cache_key = f"areas|district={district}|lng={client.lng}"
    cached = get_cached_response(cache_key, 30)
    if cached: return jsonify(cached)
    
    try:
        data = client.areas(district)
        set_cached_response(cache_key, data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cypost_bp.route("/search")
@login_required
def search_postcode():
    district = request.args.get("district")
    param = request.args.get("param")
    area = request.args.get("area")
    
    if not district or not param:
        return jsonify({"error": "district and param required"}), 400
    if len(param) < 3:
        return jsonify({"results": []})
        
    client = CyprusPostClient()
    cache_key = f"search|district={district}|area={area}|param={param}|lng={client.lng}"
    cached = get_cached_response(cache_key, 14)
    if cached: return jsonify(cached)
    
    try:
        data = client.search(district, param, area)
        set_cached_response(cache_key, data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cypost_bp.route("/resolve", methods=["POST"])
@login_required
def resolve_address():
    data = request.get_json() or {}
    district = data.get("district")
    area = data.get("area")
    street = data.get("street")
    house_no = data.get("house_no")
    
    if not district or not street:
        return jsonify({"error": "district and street required"}), 400
    if len(street) < 3:
        return jsonify({"match": None, "candidates": []})

    client = CyprusPostClient()
    try:
        search_res = client.search(district, street, area)
        results = search_res.get("results", [])
        
        # Simple exact/contains matching
        street_upper = street.upper().strip()
        candidates = []
        best_match = None
        
        for res in results:
            res_street = res.get("street", "").upper().strip()
            if res_street == street_upper:
                best_match = res
                break
            if street_upper in res_street:
                candidates.append(res)
        
        if best_match:
            return jsonify({"match": best_match, "candidates": []})
        
        return jsonify({"match": None, "candidates": candidates[:10]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@cypost_bp.route("/admin-test")
@login_required
def admin_test_page():
    if getattr(current_user, 'role', None) != 'admin':
        return "Forbidden", 403
    from flask import render_template
    return render_template("admin/postcode_lookup_test.html")
