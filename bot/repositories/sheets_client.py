"""
Единая точка доступа к Google Spreadsheet.
Кешируем объекты листов, чтобы не делать лишних API-вызовов.
"""
import json
import logging
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from config.settings import Settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class SheetsClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None
        self._worksheets: dict[str, gspread.Worksheet] = {}

    def _ensure_connected(self) -> None:
        if self._client is None:
            creds = Credentials.from_service_account_info(
                self._settings.google_credentials_dict,
                scopes=SCOPES,
            )
            self._client = gspread.authorize(creds)
            logger.info("Google Sheets client authorized")

        if self._spreadsheet is None:
            self._spreadsheet = self._client.open_by_key(self._settings.spreadsheet_id)
            logger.info("Spreadsheet opened: %s", self._settings.spreadsheet_id)

    def get_worksheet(self, sheet_name: str) -> gspread.Worksheet:
        if sheet_name not in self._worksheets:
            self._ensure_connected()
            assert self._spreadsheet is not None
            self._worksheets[sheet_name] = self._spreadsheet.worksheet(sheet_name)
        return self._worksheets[sheet_name]

    def invalidate_cache(self, sheet_name: Optional[str] = None) -> None:
        """Сбрасываем кеш листа (или всех листов) при необходимости переподключения."""
        if sheet_name:
            self._worksheets.pop(sheet_name, None)
        else:
            self._worksheets.clear()
