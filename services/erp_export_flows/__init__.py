from services.erp_export_flows.base import BaseExportFlow
from services.erp_export_flows.stock_position import StockPositionExportFlow
from services.erp_export_flows.item_catalogue import ItemCatalogueExportFlow

EXPORT_FLOWS = {
    'stock_position': StockPositionExportFlow,
    'item_catalogue': ItemCatalogueExportFlow,
}


def get_flow(export_name: str) -> BaseExportFlow:
    cls = EXPORT_FLOWS.get(export_name)
    if not cls:
        raise ValueError(f"Unknown export flow: '{export_name}'. Available: {list(EXPORT_FLOWS.keys())}")
    return cls()


def list_flows() -> list:
    return [
        {'name': name, 'label': cls.LABEL, 'description': cls.DESCRIPTION}
        for name, cls in EXPORT_FLOWS.items()
    ]
