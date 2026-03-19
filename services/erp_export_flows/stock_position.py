import os
import asyncio
import logging
from datetime import datetime
from services.erp_export_flows.base import BaseExportFlow

logger = logging.getLogger(__name__)


def import_stock_positions_from_xlsx(file_path: str) -> dict:
    import openpyxl
    from sqlalchemy import text
    from app import db

    wb = openpyxl.load_workbook(file_path)
    ws = wb.active
    if ws is None:
        raise RuntimeError("No worksheet found in workbook")

    rows = list(ws.iter_rows(min_row=5, values_only=True))
    records = []
    current_item_code = None
    current_item_desc = None
    current_store_code = None
    current_store_name = None

    for row in rows:
        if not any(row):
            continue

        if row[0] and not row[1] and not row[2]:
            item_text = str(row[0]).strip()
            if ' - ' in item_text:
                parts = item_text.split(' - ', 1)
                current_item_code = parts[0].strip()
                current_item_desc = parts[1].strip()
            else:
                current_item_code = item_text
                current_item_desc = item_text
            current_store_code = None
            current_store_name = None
            continue

        if not row[0] and row[1] and row[2]:
            current_store_code = str(row[1]).strip()
            current_store_name = str(row[2]).strip()
            if current_store_code != "777":
                current_store_code = None
                current_store_name = None
            continue

        if (not row[0] and not row[1]
                and current_item_code and current_store_code == "777"):
            expiry = row[4] if len(row) > 4 else None
            stock = row[6] if len(row) > 6 else 0

            expiry_str = None
            if expiry:
                try:
                    if hasattr(expiry, 'strftime'):
                        expiry_str = expiry.strftime('%Y-%m-%d')
                    else:
                        expiry_str = str(expiry)[:10]
                except:
                    pass

            try:
                stock_val = float(stock) if stock else 0
                records.append({
                    'item_code': current_item_code,
                    'item_description': current_item_desc,
                    'store_code': current_store_code,
                    'store_name': current_store_name,
                    'expiry_date': expiry_str,
                    'stock_quantity': stock_val,
                    'imported_at': datetime.utcnow(),
                })
            except (ValueError, TypeError):
                pass

    db.session.execute(text('TRUNCATE TABLE stock_positions'))

    for i in range(0, len(records), 1000):
        batch = records[i:i + 1000]
        db.session.execute(
            text(
                'INSERT INTO stock_positions '
                '(item_code, item_description, store_code, store_name, '
                'expiry_date, stock_quantity, imported_at) '
                'VALUES (:item_code, :item_description, :store_code, '
                ':store_name, :expiry_date, :stock_quantity, :imported_at)'
            ),
            batch,
        )

    db.session.commit()
    logger.info(f"Imported {len(records)} stock position records into DB")
    return {'records_imported': len(records)}

REPORT_PAGE_URL = 'https://accv3.powersoft365.com/restricted/StockControl/repPowerSerials.aspx'


class StockPositionExportFlow(BaseExportFlow):
    LABEL = "Stock Position"
    DESCRIPTION = "Export Serials Stock Position report from Powersoft365 ERP as XLSX"

    SELECTORS = {
        'report_type_combo': '#ContentMasterMain_ContentMasterReports_cboReportType_I',
        'generate_report_btn': '#ContentMasterMain_ContentMasterReports_btnGenerateReport_CD',
        'report_viewer_toolbar': '.dxrd-toolbar',
        'export_to_btn': '[title="Export To"]',
        'export_menu_item': '.dxrd-preview-export-menu-item',
    }

    async def navigate_to_export_screen(self):
        logger.info(f"Navigating to Serials Stock Report: {REPORT_PAGE_URL}")
        await self.page.goto(REPORT_PAGE_URL, wait_until='networkidle', timeout=30000)
        await asyncio.sleep(2)

        combo = await self.page.query_selector(self.SELECTORS['report_type_combo'])
        if combo:
            value = await combo.get_attribute('value') or ''
            logger.info(f"Report type combo value: {value}")
        else:
            raise RuntimeError("Could not find Report Type combo on Serials Reports page")

        logger.info(f"On report page: {self.page.url}")

    async def apply_filters(self, params: dict | None = None):
        logger.info("No filters applied — exporting all items (default)")

    async def trigger_export(self) -> str:
        logger.info("Clicking Generate Report...")
        await self.page.click(self.SELECTORS['generate_report_btn'])

        try:
            await self.page.wait_for_load_state('networkidle', timeout=60000)
        except:
            pass
        await asyncio.sleep(5)

        toolbar = await self.page.query_selector(self.SELECTORS['report_viewer_toolbar'])
        if not toolbar:
            raise RuntimeError("Report viewer toolbar did not appear — report generation may have failed")
        logger.info("Report generated, viewer toolbar visible")

        logger.info("Opening Export To dropdown...")
        export_btn = await self.page.query_selector(self.SELECTORS['export_to_btn'])
        if not export_btn:
            raise RuntimeError("Export To button not found in report viewer toolbar")
        await export_btn.click()
        await asyncio.sleep(1)

        xlsx_item = None
        menu_items = await self.page.query_selector_all(self.SELECTORS['export_menu_item'])
        for item in menu_items:
            try:
                text = (await item.inner_text()).strip()
                if text == 'XLSX':
                    xlsx_item = item
                    break
            except:
                pass

        if not xlsx_item:
            raise RuntimeError("XLSX option not found in export dropdown menu")

        logger.info("Clicking XLSX export and waiting for download...")

        download_future = asyncio.get_event_loop().create_future()

        async def on_download(download):
            if not download_future.done():
                download_future.set_result(download)

        self.page.on('download', on_download)

        await xlsx_item.click()

        try:
            download = await asyncio.wait_for(download_future, timeout=120)
        except asyncio.TimeoutError:
            raise RuntimeError("XLSX download timed out after 120 seconds")

        original_name = download.suggested_filename
        logger.info(f"Download triggered: {original_name}")

        download_dir = os.environ.get('ERP_EXPORT_DOWNLOAD_DIR',
                                       os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                                    'data', 'erp_exports'))
        os.makedirs(download_dir, exist_ok=True)
        save_path = os.path.join(download_dir, original_name)
        await download.save_as(save_path)

        file_size = os.path.getsize(save_path)
        logger.info(f"File saved: {save_path} ({file_size:,} bytes)")

        self._downloaded_file = save_path
        self._downloaded_name = original_name
        self._downloaded_size = file_size

        return 'stock_position'

    def get_download_result(self) -> dict:
        return {
            'file_path': getattr(self, '_downloaded_file', None),
            'file_name': getattr(self, '_downloaded_name', None),
            'file_size': getattr(self, '_downloaded_size', None),
        }

    async def post_process(self, file_path: str, metadata: dict) -> dict:
        from app import app
        logger.info(f"Post-processing: importing stock positions from {file_path}")
        try:
            with app.app_context():
                result = import_stock_positions_from_xlsx(file_path)
            logger.info(f"Post-process complete: {result}")
            return result
        except Exception as e:
            logger.error(f"Post-process failed: {e}", exc_info=True)
            return {'error': str(e)}

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
