import logging
from app import db
from models import SkuForecastOverride, SkuForecastResult

logger = logging.getLogger(__name__)


def get_effective_forecast(item_code_365):
    override = SkuForecastOverride.query.filter_by(
        item_code_365=item_code_365,
        is_active=True,
    ).order_by(SkuForecastOverride.created_at.desc()).first()

    if override:
        return (float(override.override_weekly_qty), "override")

    result = SkuForecastResult.query.filter_by(
        item_code_365=item_code_365
    ).first()

    if result:
        return (float(result.final_forecast_weekly_qty), "system")

    return (0.0, "system")


def get_effective_forecasts_bulk(item_codes):
    if not item_codes:
        return {}

    overrides = SkuForecastOverride.query.filter(
        SkuForecastOverride.item_code_365.in_(item_codes),
        SkuForecastOverride.is_active == True,
    ).all()

    override_map = {}
    for o in overrides:
        code = o.item_code_365
        if code not in override_map or o.created_at > override_map[code].created_at:
            override_map[code] = o

    results = SkuForecastResult.query.filter(
        SkuForecastResult.item_code_365.in_(item_codes)
    ).all()
    result_map = {r.item_code_365: r for r in results}

    effective = {}
    for code in item_codes:
        if code in override_map:
            effective[code] = (float(override_map[code].override_weekly_qty), "override")
        elif code in result_map:
            effective[code] = (float(result_map[code].final_forecast_weekly_qty), "system")
        else:
            effective[code] = (0.0, "system")

    return effective
