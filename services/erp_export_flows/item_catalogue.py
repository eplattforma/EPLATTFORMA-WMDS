import os
import re
import asyncio
import logging
from datetime import datetime
from services.erp_export_flows.base import BaseExportFlow

logger = logging.getLogger(__name__)

REPORT_PAGE_URL = 'https://accv3.powersoft365.com/restricted/StockControl/repPowerItemCatalogue.aspx?'

COST_CHECKBOX_ID = '#ContentMasterMain_ContentMasterReports_popupParms_chkPShowCost_S_D'
REPORT_PARAMS_BTN = '#ContentMasterMain_ContentMasterReports_grid_Title_btnReportParameters_0_CD'
SAVE_BTN = '#ContentMasterMain_ContentMasterReports_popupParms_btnSave1_CD'
EXPORT_BTN = '#ContentMasterMain_ContentMasterReports_btnExport_CD'
EXPORT_TYPE_INPUT = '#ContentMasterMain_ContentMasterReports_cboExportType_I'


def _parse_currency(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    cleaned = re.sub(r'[€$£,\s]', '', s)
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def import_item_catalogue_costs(file_path: str) -> dict:
    import openpyxl
    from sqlalchemy import text
    from app import db

    wb = openpyxl.load_workbook(file_path, read_only=True)
    ws = wb.active
    if ws is None:
        raise RuntimeError("No worksheet found in workbook")

    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if len(rows) < 3:
        raise RuntimeError(f"XLSX has only {len(rows)} rows — expected header + data")

    headers = [str(h).strip() if h else '' for h in rows[0]]
    logger.info(f"Catalogue headers: {headers}")

    item_code_col = None
    cost_col = None
    for idx, h in enumerate(headers):
        hl = h.lower()
        if hl == 'item code':
            item_code_col = idx
        elif hl == 'cost':
            cost_col = idx

    if item_code_col is None:
        raise RuntimeError(f"'Item Code' column not found in headers: {headers}")
    if cost_col is None:
        raise RuntimeError(f"'Cost' column not found in headers: {headers}")

    logger.info(f"Item Code col={item_code_col}, Cost col={cost_col}")

    updates = []
    skipped = 0
    for row in rows[1:]:
        if not row or len(row) <= max(item_code_col, cost_col):
            continue
        item_code = row[item_code_col]
        cost_raw = row[cost_col]
        if not item_code or str(item_code).strip() == '':
            continue

        cost_val = _parse_currency(cost_raw)
        if cost_val is None:
            skipped += 1
            continue

        updates.append({
            'item_code': str(item_code).strip(),
            'cost_price': cost_val,
        })

    if not updates:
        raise RuntimeError("No valid cost records found in the exported file")

    updated_count = 0
    unchanged_count = 0
    not_found = 0
    EPSILON = 0.0001  # 1/100 of a cent — anything smaller is a float-rounding artifact
    try:
        existing = {
            row[0]: row[1]
            for row in db.session.execute(
                text(
                    'SELECT item_code_365, cost_price FROM ps_items_dw '
                    'WHERE item_code_365 = ANY(:codes)'
                ),
                {'codes': [rec['item_code'] for rec in updates]},
            ).fetchall()
        }

        for i in range(0, len(updates), 500):
            batch = updates[i:i + 500]
            for rec in batch:
                code = rec['item_code']
                new_cost = rec['cost_price']
                if code not in existing:
                    not_found += 1
                    continue
                old_cost = existing[code]
                old_cost_f = float(old_cost) if old_cost is not None else None
                if old_cost_f is not None and abs(old_cost_f - new_cost) < EPSILON:
                    unchanged_count += 1
                    continue
                db.session.execute(
                    text(
                        'UPDATE ps_items_dw '
                        'SET cost_price = :cost_price, '
                        '    cost_price_updated_at = :now '
                        'WHERE item_code_365 = :item_code'
                    ),
                    {**rec, 'now': datetime.utcnow()},
                )
                updated_count += 1

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    logger.info(
        f"Cost import complete: {updated_count} items changed, "
        f"{unchanged_count} unchanged, "
        f"{not_found} not found in ps_items_dw, {skipped} skipped (no cost)"
    )
    return {
        'items_updated': updated_count,
        'items_unchanged': unchanged_count,
        'items_not_found': not_found,
        'items_skipped': skipped,
        'total_in_file': len(updates) + skipped,
    }


class ItemCatalogueExportFlow(BaseExportFlow):
    LABEL = "Item Catalogue (Cost)"
    DESCRIPTION = "Export Item Catalogue with cost prices from Powersoft365 ERP as XLSX"

    async def navigate_to_export_screen(self):
        logger.info(f"Navigating to Item Catalogue: {REPORT_PAGE_URL}")
        await self.page.goto(REPORT_PAGE_URL, wait_until='networkidle', timeout=60000)
        await asyncio.sleep(3)

        for attempt in range(3):
            export_type = await self.page.query_selector(EXPORT_TYPE_INPUT)
            if export_type:
                value = await export_type.get_attribute('value') or ''
                logger.info(f"Export type preset to: {value}")
                break
            logger.warning(f"Export Type combo not found yet (attempt {attempt + 1}/3), waiting...")
            await asyncio.sleep(5)
        else:
            await self.page.reload(wait_until='networkidle', timeout=60000)
            await asyncio.sleep(5)
            export_type = await self.page.query_selector(EXPORT_TYPE_INPUT)
            if not export_type:
                page_title = await self.page.title()
                page_url = self.page.url
                logger.error(f"Page title: {page_title}, URL: {page_url}")
                raise RuntimeError(
                    f"Could not find Export Type combo on Item Catalogue page after retries. "
                    f"Page may not have loaded correctly. URL: {page_url}"
                )
            value = await export_type.get_attribute('value') or ''
            logger.info(f"Export type preset to: {value} (after reload)")

        logger.info(f"On catalogue page: {self.page.url}")

    async def apply_filters(self, params: dict | None = None):
        logger.info("Opening Report Parameters popup...")
        await self.page.click(REPORT_PARAMS_BTN)
        await asyncio.sleep(2)

        cost_chk = await self.page.query_selector(COST_CHECKBOX_ID)
        if not cost_chk:
            raise RuntimeError("Cost checkbox not found in Report Parameters popup")

        is_checked = await self.page.evaluate(
            '''(sel) => {
                const el = document.querySelector(sel);
                return el && el.classList.contains('dxICBChecked');
            }''',
            COST_CHECKBOX_ID,
        )

        if not is_checked:
            logger.info("Checking the Cost checkbox...")
            await cost_chk.click()
            await asyncio.sleep(0.5)
        else:
            logger.info("Cost checkbox already checked")

        logger.info("Clicking Save on Report Parameters...")
        await self.page.click(SAVE_BTN)
        await asyncio.sleep(2)
        await self.page.wait_for_load_state('networkidle', timeout=30000)
        await asyncio.sleep(1)
        logger.info("Report Parameters saved")

    async def trigger_export(self) -> str:
        logger.info("Setting up download listener and clicking Export...")

        download_future = asyncio.get_event_loop().create_future()

        async def on_download(download):
            if not download_future.done():
                download_future.set_result(download)

        self.page.on('download', on_download)

        await self.page.click(EXPORT_BTN)

        try:
            download = await asyncio.wait_for(download_future, timeout=120)
        except asyncio.TimeoutError:
            raise RuntimeError("Item Catalogue XLSX download timed out after 120 seconds")

        original_name = download.suggested_filename
        logger.info(f"Download triggered: {original_name}")

        download_dir = os.environ.get(
            'ERP_EXPORT_DOWNLOAD_DIR',
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                         'data', 'erp_exports'),
        )
        os.makedirs(download_dir, exist_ok=True)
        save_path = os.path.join(download_dir, original_name)
        await download.save_as(save_path)

        file_size = os.path.getsize(save_path)
        logger.info(f"File saved: {save_path} ({file_size:,} bytes)")

        self._downloaded_file = save_path
        self._downloaded_name = original_name
        self._downloaded_size = file_size

        return 'item_catalogue'

    def get_download_result(self) -> dict:
        return {
            'file_path': getattr(self, '_downloaded_file', None),
            'file_name': getattr(self, '_downloaded_name', None),
            'file_size': getattr(self, '_downloaded_size', None),
        }

    async def post_process(self, file_path: str, metadata: dict) -> dict:
        from app import app
        logger.info(f"Post-processing: importing item catalogue costs from {file_path}")
        with app.app_context():
            result = import_item_catalogue_costs(file_path)
        logger.info(f"Post-process complete: {result}")
        return result

    def validate_download(self, file_path: str) -> bool:
        if not os.path.exists(file_path):
            return False
        size = os.path.getsize(file_path)
        if size == 0:
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.get_expected_extensions():
            logger.warning(f"Unexpected file extension: {ext}")
        return True
