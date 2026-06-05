"""
Google Sheets клиент v2
Два листа: "Касса Фирмы" и "Касса Офиса"
Лист "Балансы" — остатки обеих касс
"""

from __future__ import annotations
import os
import json
from datetime import date, datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADERS = ["Дата", "Время", "Тип", "Сумма", "Комментарий", "Кто внёс"]
SHEET_BAL = "Балансы"


class SheetsClient:
    def __init__(self, spreadsheet_id: str):
        self.spreadsheet_id = spreadsheet_id

        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if creds_json:
            creds_info = json.loads(creds_json)
            creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file(
                os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
                scopes=SCOPES,
            )

        self.service = build("sheets", "v4", credentials=creds)
        self.sheets = self.service.spreadsheets()
        self._ensure_sheets()

    def _ensure_sheets(self):
        meta = self.sheets.get(spreadsheetId=self.spreadsheet_id).execute()
        existing = {s["properties"]["title"] for s in meta["sheets"]}
        needed = ["Касса Фирмы", "Касса Офиса", SHEET_BAL]
        requests = [
            {"addSheet": {"properties": {"title": t}}}
            for t in needed if t not in existing
        ]
        if requests:
            self.sheets.batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests}
            ).execute()

        for sheet in ["Касса Фирмы", "Касса Офиса"]:
            h = self._read(f"'{sheet}'!A1:F1")
            if not h or h[0] != HEADERS:
                self._write(f"'{sheet}'!A1:F1", [HEADERS])

        bal = self._read(f"'{SHEET_BAL}'!A1:C3")
        if not bal or len(bal) < 3:
            self._write(f"'{SHEET_BAL}'!A1:C3", [
                ["Касса", "Остаток", "Обновлено"],
                ["Касса Фирмы", 0, ""],
                ["Касса Офиса", 0, ""],
            ])

    def add_transaction(self, cash: str, op_type: str, amount: float, comment: str, user: str):
        now = datetime.now()
        row = [
            now.strftime("%d.%m.%Y"),
            now.strftime("%H:%M"),
            "Приход" if op_type == "income" else "Расход",
            round(amount, 2),
            comment,
            user,
        ]
        self.sheets.values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{cash}'!A:F",
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ).execute()

        delta = amount if op_type == "income" else -amount
        new_balance = round(self.get_balance(cash) + delta, 2)
        self._update_balance(cash, new_balance)

    def get_balance(self, cash: str) -> float:
        data = self._read(f"'{SHEET_BAL}'!A:B")
        for row in data[1:]:
            if len(row) >= 2 and row[0] == cash:
                try:
                    return float(str(row[1]).replace(",", "."))
                except ValueError:
                    pass
        return 0.0

    def _update_balance(self, cash: str, balance: float):
        data = self._read(f"'{SHEET_BAL}'!A:A")
        for i, row in enumerate(data):
            if row and row[0] == cash:
                row_num = i + 1
                now = datetime.now().strftime("%d.%m.%Y %H:%M")
                self._write(f"'{SHEET_BAL}'!B{row_num}:C{row_num}", [[balance, now]])
                return

    def get_transactions(self, cash: str, date_from: date, date_to: date) -> list:
        data = self._read(f"'{cash}'!A2:F")
        if not data:
            return []
        result = []
        for row in data:
            if len(row) < 4:
                continue
            try:
                tx_date = datetime.strptime(row[0], "%d.%m.%Y").date()
            except ValueError:
                continue
            if not (date_from <= tx_date <= date_to):
                continue
            result.append({
                "date": tx_date,
                "time": row[1] if len(row) > 1 else "",
                "type": "income" if row[2] == "Приход" else "expense",
                "amount": float(str(row[3]).replace(",", ".")),
                "comment": row[4] if len(row) > 4 else "",
                "user": row[5] if len(row) > 5 else "",
            })
        return result

    def _read(self, range_: str) -> list:
        res = self.sheets.values().get(
            spreadsheetId=self.spreadsheet_id,
            range=range_,
        ).execute()
        return res.get("values", [])

    def _write(self, range_: str, values: list):
        self.sheets.values().update(
            spreadsheetId=self.spreadsheet_id,
            range=range_,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()
