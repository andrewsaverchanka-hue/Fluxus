"""Парсер skills-battle-frontend-лога (W2 Frontend).

Восстанавливает:
  - Жизненный цикл выдачи наличных на W2-терминале по trx (uuid операции).
  - Инкассации, промежуточные изъятия и пополнения кассет (cash events).
"""

import os
import re
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from core.utils import classify_dispense_error, open_log_file
from core.models import W2WithdrawSession, W2CashEvent


class W2FrontendAnalyzer:
    """
    Парсер skills-battle-frontend-лога: восстанавливает жизненный цикл
    выдачи наличных на W2-терминале по trx (uuid операции).
    Собирает commands, dispensed_events, boxes, статус и т.д.

    Также парсит cash events (collection/withdraw_report/cash_in)
    из строк printSlip + rawTemplate, которые ловят grep-паттерны
    (COLLECTION REPORT / Withdraw report / Cash in).
    """

    TS_RE = re.compile(
        r'\[(\d{1,2})/(\d{1,2})/(\d{4}),\s*(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)\]'
    )

    # ==================================================================
    # РЕГУЛЯРКИ для парсинга cash events (ЭТАП 2)
    # ==================================================================

    # Поля placeholders внутри rawTemplate.
    # Формат frontend: "outByNotes":"$5*429 = $2145\n\n$10*300..."
    # Формат devices:   outByNotes="$5*429 = $2145\n\n$10..."
    # Регулярки работают с обоими форматами.
    _RE_OUT_BY_NOTES = re.compile(
    r'outByNotes["\s]*(?:,\s*"value"\s*)?[":=\s]+"?([^"]*?)(?=\\?"|$)',
    re.IGNORECASE
    )
    _RE_TAKEN_DISP = re.compile(
        r'takenFromDispenserBoxes["\s]*(?:,\s*"value"\s*)?[":=\s]+"?([^"]*?)(?=\\?"|$)',
        re.IGNORECASE
    )
    _RE_TAKEN_REJECT = re.compile(
        r'takenFromRejectedBox["\s]*(?:,\s*"value"\s*)?[":=\s]+"?([^"]*?)(?=\\?"|$)',
        re.IGNORECASE
    )
    _RE_ADDED_BOXES = re.compile(
        r'addedToDispenserBoxes["\s]*(?:,\s*"value"\s*)?[":=\s]+"?([^"]*?)(?=\\?"|$)',
        re.IGNORECASE
    )
    _RE_TOTAL_SUM = re.compile(
        r'totalSum["\s]*(?:,\s*"value"\s*)?[":=\s]+"?\$?(\d+)',
        re.IGNORECASE
    )
    _RE_TOTAL_OUT = re.compile(
        r'totalOut["\s]*(?:,\s*"value"\s*)?[":=\s]+"?\$?(\d+)',
        re.IGNORECASE
    )
    _RE_TOTAL_LOADED = re.compile(
        r'totalLoaded["\s]*(?:,\s*"value"\s*)?[":=\s]+"?\$?(\d+)',
        re.IGNORECASE
    )

    # Записи внутри placeholder: "$5*429 = $2145" → (nominal, count, total)
    _RE_NOTE_ENTRY = re.compile(r'\$(\d+)\s*\*\s*(\d+)\s*=\s*\$?(\d+)')

    # Записи "Box N" секций: "Box 1\n$5*270 = $1350" → (box, nominal, count, total)
    _RE_BOX_ENTRY = re.compile(
        r'Box\s+(\d+)[^\n]*\n+(?:Inserted:\s*\n+)?'
        r'\$(\d+)\s*\*\s*(\d+)\s*=\s*\$?(\d+)',
        re.IGNORECASE
    )

    def __init__(self):
        self.sessions_by_trx = {}
        self.sessions = []
        self.dispensers = {}
        self.lines_processed = 0
        self.lines_matched = 0
        self._active_trx_by_user = {}
        self._active_trx = None
        self._tt_id_to_trx = {}

        # ===== ЭТАП 2: cash events =====
        # События изменения баланса кассет (collection/withdraw/cash_in)
        self.cash_events: list[W2CashEvent] = []
        # Защита от дублей: одно событие может встретиться в devices+frontend
        # Ключ: (type, dt_isoformat)
        self._seen_cash_events: set = set()

    # ====================================================================
    # Чтение файла (поддерживает путь и stream)
    # ====================================================================

    def parse_log_file(self, file_path_or_stream, progress_callback=None,
                       display_name=None):
        """Парсит лог. Принимает:

        - путь к файлу (str/Path) — обычный режим (скачивание на диск),
        - file-like объект с методом read() — streaming-режим.
        """
        try:
            is_stream = hasattr(file_path_or_stream, 'read')

            if is_stream:
                stream = file_path_or_stream
                file_size = None
                display = display_name or 'stream'
                close_after = False
                is_gz = False
            else:
                file_path = Path(file_path_or_stream)
                file_size = os.path.getsize(file_path)
                is_gz = str(file_path).lower().endswith('.gz')
                display = file_path.name
                stream = open_log_file(file_path)
                close_after = True

            bytes_read = 0
            chunk_size = 1024 * 1024
            try:
                buffer = ""
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        self._process_chunk(buffer)
                        break
                    bytes_read += len(chunk.encode('utf-8', errors='ignore'))
                    buffer += chunk
                    lines = buffer.split('\n')
                    buffer = lines[-1]
                    for line in lines[:-1]:
                        self._process_line(line)
                    if progress_callback:
                        if is_stream:
                            progress = min(99,
                                           bytes_read // (10 * 1024 * 1024))
                        elif is_gz:
                            progress = min(99, int(
                                (bytes_read / (file_size * 10)) * 100
                            ))
                        else:
                            progress = int((bytes_read / file_size) * 100)
                        progress_callback(progress, display)
            finally:
                if close_after:
                    try:
                        stream.close()
                    except Exception:
                        pass
        except Exception as e:
            name = (display_name if hasattr(file_path_or_stream, 'read')
                    else file_path_or_stream)
            print(f"[W2Frontend] Ошибка чтения {name}: {e}")

    def _process_chunk(self, chunk):
        for line in chunk.split('\n'):
            self._process_line(line)

    def _extract_dt(self, line):
        m = self.TS_RE.search(line)
        if not m:
            return None, None, None
        month, day, year, h, mi, s, ampm = m.groups()
        h = int(h)
        if ampm == 'PM' and h != 12:
            h += 12
        if ampm == 'AM' and h == 12:
            h = 0
        try:
            dt = datetime(int(year), int(month), int(day), h, int(mi), int(s))
            return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M:%S"), dt
        except ValueError:
            return None, None, None

    def _ensure_session(self, trx):
        if trx not in self.sessions_by_trx:
            self.sessions_by_trx[trx] = W2WithdrawSession(trx)
        return self.sessions_by_trx[trx]

    def _extract_login(self, line):
        m = re.search(r"login:\s*'(\d+)'", line)
        if m:
            return m.group(1)
        return None

    # ====================================================================
    # Основная обработка строки
    # ====================================================================

    def _process_line(self, line):
        self.lines_processed += 1
        date_str, time_str, dt = self._extract_dt(line)
        if dt is None:
            return

        # ---- payoutCoupon ----
        if 'payoutCoupon' in line and ('status' in line or 'coupon' in line):
            trx_m = re.search(r'"trx"\s*:\s*"([0-9a-f\-]{36})"', line)
            if trx_m:
                trx = trx_m.group(1)
                sess = self._ensure_session(trx)
                coupon_m = re.search(
                    r'"coupon"\s*:\s*\{\s*"low"\s*:\s*(-?\d+)', line
                )
                if coupon_m:
                    sess.coupon = str(abs(int(coupon_m.group(1))))
                gu_m = re.search(r'"groupUuid"\s*:\s*"(\w+)"', line)
                if gu_m:
                    sess.group_uuid = gu_m.group(1)
                st_m = re.search(
                    r'"payoutCoupon"\s*:\s*\{\s*"status"\s*:\s*"(\w+)"', line
                )
                if st_m:
                    sess.payout_coupon_status = st_m.group(1)
                amt_m = re.search(
                    r'"amount"\s*:\s*\{\s*"low"\s*:\s*(-?\d+)', line
                )
                if amt_m:
                    sess.requested_amount = abs(int(amt_m.group(1)))
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'payoutCoupon', 'raw': line.strip()[:200],
                    'status': sess.payout_coupon_status,
                    'amount': sess.requested_amount,
                })
                self._active_trx = trx
                self.lines_matched += 1
            return

        if 'payoutCouponAPI' in line:
            trx_m = re.search(r'trx\s+([0-9a-f\-]{36})', line)
            login_m = re.search(r'login:\s*(\d+)', line)
            if trx_m:
                sess = self._ensure_session(trx_m.group(1))
                if login_m:
                    sess.user_login = login_m.group(1)
                    self._active_trx_by_user[login_m.group(1)] = trx_m.group(1)
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'payoutCouponAPI', 'raw': line.strip()[:200],
                })
                self.lines_matched += 1
            return

        if 'INSERT INTO terminal_balances' in line:
            trx_m = re.search(r"`trx`\s*=\s*'([0-9a-f\-]{36})'", line)
            sum_m = re.search(r"`sum`\s*=\s*(\d+)", line)
            user_m = re.search(r"`user_login`\s*=\s*'(\d+)'", line)
            if trx_m:
                sess = self._ensure_session(trx_m.group(1))
                if sum_m:
                    sess.requested_amount = int(sum_m.group(1))
                if user_m:
                    sess.user_login = user_m.group(1)
                    self._active_trx_by_user[user_m.group(1)] = trx_m.group(1)
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'insertBalance', 'raw': line.strip()[:150],
                })
                self.lines_matched += 1
            return

        if 'UPDATE terminal_balances' in line and 'in_progress' in line:
            id_m = re.search(r'where id\s*=\s*(\d+)', line)
            login = self._extract_login(line)
            trx = (self._active_trx_by_user.get(login) if login
                   else self._active_trx)
            if trx and id_m:
                sess = self._ensure_session(trx)
                sess.balance_id = int(id_m.group(1))
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'balanceInProgress', 'raw': line.strip()[:150],
                })
                self.lines_matched += 1
            return

        if ('INSERT INTO terminal_transactions ' in line
                or 'INSERT INTO terminal_transactions SET' in line):
            trx_m = re.search(r"`trx`\s*=\s*'([0-9a-f\-]{36})'", line)
            if trx_m:
                sess = self._ensure_session(trx_m.group(1))
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'insertTransaction', 'raw': line.strip()[:150],
                })
                self.lines_matched += 1
            return

        # ---- DispenserService.dispense (команды + cassette_snapshot) ----
        if 'DispenserService.dispense' in line:
            login = self._extract_login(line)
            trx = (self._active_trx_by_user.get(login) if login
                   else self._active_trx)
            if not trx:
                return
            sess = self._ensure_session(trx)
            cmd_block_m = re.search(r'commands\s+(\[.*?\])', line)
            if cmd_block_m:
                try:
                    commands = json.loads(cmd_block_m.group(1))
                    sess.commands = commands
                    if commands and not sess.dispenser_device_id:
                        sess.dispenser_device_id = commands[0].get('deviceId')
                except (json.JSONDecodeError, ValueError):
                    pass
            snap_m = re.search(
                r'nominal and current count\s+(\[\[.*?\]\])', line
            )
            if snap_m:
                try:
                    sess.cassette_snapshot = json.loads(snap_m.group(1))
                except (json.JSONDecodeError, ValueError):
                    pass
            sess.add_event({
                'dt': dt, 'date': date_str, 'time': time_str,
                'kind': 'dispenseCommand', 'raw': line.strip()[:300],
            })
            self.lines_matched += 1
            return

        # ---- processNewEvent dispensed ----
        if 'processNewEvent' in line and '"type":16' in line:
            login = self._extract_login(line)
            trx = (self._active_trx_by_user.get(login) if login
                   else self._active_trx)
            if not trx:
                return
            sess = self._ensure_session(trx)
            disp_m = re.search(r'"dispensed"\s*:\s*(\{[^{}]*\})', line)
            disp = {}
            if disp_m:
                try:
                    disp = json.loads(disp_m.group(1))
                except (json.JSONDecodeError, ValueError):
                    disp = {}
            model_m = re.search(r'"model"\s*:\s*"([^"]+)"', line)
            device_m = re.search(
                r'"device"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)"', line
            )
            vendor_m = re.search(r'"vendor"\s*:\s*"([^"]+)"', line)
            if device_m:
                dev_id = device_m.group(1)
                sess.dispenser_device_id = dev_id
                if model_m:
                    sess.dispenser_model = model_m.group(1)
                if vendor_m:
                    sess.dispenser_vendor = vendor_m.group(1)
                self.dispensers.setdefault(dev_id, {
                    'model': sess.dispenser_model or '?',
                    'vendor': sess.dispenser_vendor or '?',
                })
            sess.dispensed_events.append(disp)
            if 'statusDetail' in disp:
                sess.retry_count += 1
            sess.add_event({
                'dt': dt, 'date': date_str, 'time': time_str,
                'kind': 'dispensedEvent', 'raw': line.strip()[:300],
                'bankIndex': disp.get('bankIndex'),
                'requested': disp.get('notesAmountRequested'),
                'dispensed': disp.get('notesAmountDispensed', 0),
                'statusDetail': disp.get('statusDetail'),
            })
            self.lines_matched += 1
            return

        if 'INSERT INTO terminal_transactions_box' in line:
            login = self._extract_login(line)
            trx = (self._active_trx_by_user.get(login) if login
                   else self._active_trx)
            if not trx:
                return
            sess = self._ensure_session(trx)
            for m in re.finditer(
                    r'\((\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)', line):
                tt_id, box, amount, count, is_rejected = map(int, m.groups())
                sess.transaction_id = tt_id
                self._tt_id_to_trx[tt_id] = trx
                sess.boxes.append({
                    'tt_id': tt_id, 'box': box, 'nominal': amount,
                    'count': count, 'is_rejected': is_rejected,
                })
            sess.add_event({
                'dt': dt, 'date': date_str, 'time': time_str,
                'kind': 'insertBox', 'raw': line.strip()[:200],
            })
            self.lines_matched += 1
            return

        if ('UPDATE terminal_transactions' in line and "status" in line
                and ('ok' in line or 'refund' in line)):
            sum_m = re.search(r"`sum`\s*=\s*(\d+)", line)
            id_m = re.search(r'where id\s*=\s*(\d+)', line)
            status_m = re.search(r"`status`\s*=\s*'(\w+)'", line)
            if id_m:
                tt_id = int(id_m.group(1))
                trx = self._tt_id_to_trx.get(tt_id)
                if not trx:
                    login = self._extract_login(line)
                    trx = (self._active_trx_by_user.get(login) if login
                           else self._active_trx)
                if trx:
                    sess = self._ensure_session(trx)
                    sess.transaction_id = tt_id
                    if sum_m:
                        sess.dispensed_amount = int(sum_m.group(1))
                    sess.add_event({
                        'dt': dt, 'date': date_str, 'time': time_str,
                        'kind': 'updateTransaction',
                        'db_status': status_m.group(1) if status_m else None,
                        'raw': line.strip()[:200],
                    })
                    self.lines_matched += 1
            return

        if 'cannotContinue' in line and '{' in line:
            login = self._extract_login(line)
            trx = (self._active_trx_by_user.get(login) if login
                   else self._active_trx)
            if trx:
                sess = self._ensure_session(trx)
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'cannotContinue', 'raw': line.strip()[:300],
                })
                self.lines_matched += 1
            return

        if 'Error occurs while withdrawing' in line:
            trx_m = re.search(r'trx[:\s]+([0-9a-f\-]{36})', line)
            if trx_m:
                sess = self._ensure_session(trx_m.group(1))
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'errorOccurs', 'raw': line.strip()[:250],
                })
                self.lines_matched += 1
            return

        if 'createCoupon' in line and 'status' in line:
            login = self._extract_login(line)
            trx = (self._active_trx_by_user.get(login) if login
                   else self._active_trx)
            val_m = re.search(
                r'"value"\s*:\s*\{\s*"low"\s*:\s*(-?\d+)', line
            )
            if trx and val_m:
                sess = self._ensure_session(trx)
                sess.recovery_coupon = str(abs(int(val_m.group(1))))
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'createCoupon', 'raw': line.strip()[:250],
                    'value': sess.recovery_coupon,
                })
                self.lines_matched += 1
            return

        if 'printSlip' in line and ('BARCODE' in line or 'balance' in line):
            login = self._extract_login(line)
            trx = (self._active_trx_by_user.get(login) if login
                   else self._active_trx)
            if trx:
                sess = self._ensure_session(trx)
                sess.receipt_printed = True
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'printSlip', 'raw': line.strip()[:150],
                })
                self.lines_matched += 1
            return

        if 'Complete transaction' in line:
            trx_m = re.search(r'transactionId:\s*([0-9a-f\-]{36})', line)
            if trx_m:
                sess = self._ensure_session(trx_m.group(1))
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'completeTransaction', 'raw': line.strip()[:200],
                })
                self.lines_matched += 1
            return

        if 'Redirect to HOME' in line and 'withdraw' in line:
            login = self._extract_login(line)
            trx = (self._active_trx_by_user.get(login) if login
                   else self._active_trx)
            if trx:
                sess = self._ensure_session(trx)
                is_failed = 'failed withdraw' in line
                sess.add_event({
                    'dt': dt, 'date': date_str, 'time': time_str,
                    'kind': 'redirectFailed' if is_failed else 'redirectOk',
                    'raw': line.strip()[:150],
                })
                self.lines_matched += 1
            return

        # ====== ЭТАП 2: Cash events (collection/withdraw/cash_in) ======
        # Три типа события отличаются содержимым rawTemplate.
        # Ловим по ключевым словам, которые захватываются grep-паттерном.
        if ('COLLECTION REPORT' in line
                or 'Withdraw report' in line
                or 'Cash in' in line
                or 'takenFromDispenserBoxes' in line
                or 'addedToDispenserBoxes' in line
                or 'outByNotes' in line):
            self._try_parse_cash_event(line, dt)
            return

    # ====================================================================
    # ЭТАП 2: ПАРСИНГ CASH EVENTS
    # ====================================================================

    def _detect_cash_event_type(self, line: str) -> str | None:
        """Определяет тип cash-события по ключевым словам в строке."""
        # Приоритет: COLLECTION REPORT проверяется первым, т.к. он содержит
        # и outByNotes, и inByDatesAndNotes.
        if 'COLLECTION REPORT' in line:
            return W2CashEvent.TYPE_COLLECTION
        if 'Withdraw report' in line or 'takenFromDispenserBoxes' in line:
            return W2CashEvent.TYPE_WITHDRAW
        if 'Cash in' in line or 'addedToDispenserBoxes' in line:
            return W2CashEvent.TYPE_CASH_IN
        # Fallback: только outByNotes без явного заголовка → COLLECTION
        if 'outByNotes' in line:
            return W2CashEvent.TYPE_COLLECTION
        return None

    def _unescape_placeholder(self, s: str) -> str:
        """Расшифровывает \\n → перенос строки и \\" → ковычку в payload.

        В frontend-логе placeholder приходит JSON-экранированным.
        В devices-логе — почти «сырой» (но тоже с \\n).
        """
        return s.replace('\\n', '\n').replace('\\"', '"')

    def _parse_notes_by_nominal(self, text: str) -> dict[int, int]:
        """Парсит записи '$X*N = $M' в {nominal: count}."""
        result: dict[int, int] = {}
        for m in self._RE_NOTE_ENTRY.finditer(text):
            nominal = int(m.group(1))
            count = int(m.group(2))
            result[nominal] = result.get(nominal, 0) + count
        return result

    def _parse_boxes_with_nominal(self, text: str) -> dict[int, int]:
        """Парсит записи 'Box N\\n$X*M' в {box_number: count}."""
        result: dict[int, int] = {}
        for m in self._RE_BOX_ENTRY.finditer(text):
            box_num = int(m.group(1))
            # m.group(2) — nominal (не используется как ключ)
            cnt = int(m.group(3))
            result[box_num] = result.get(box_num, 0) + cnt
        return result

    def _try_parse_cash_event(self, line: str, dt: datetime) -> None:
        """Пытается распарсить cash-событие (collection/withdraw/cash_in)
        из строки лога. При успехе добавляет в self.cash_events."""
        event_type = self._detect_cash_event_type(line)
        if event_type is None:
            return

        # Защита от дублей: одна и та же инкассация может попасть в парсер
        # и из frontend-, и из devices-лога, особенно в download-режиме.
        dedup_key = (event_type, dt.isoformat())
        if dedup_key in self._seen_cash_events:
            return

        # Контент может быть JSON-экранированным (frontend) или «сырым»
        # (devices). В обоих случаях расшифровка безопасна.
        unescaped = self._unescape_placeholder(line)

        event = W2CashEvent(event_type, dt)

        # ===== CASH_IN =====
        if event_type == W2CashEvent.TYPE_CASH_IN:
            added_m = self._RE_ADDED_BOXES.search(unescaped)
            if added_m:
                payload = self._unescape_placeholder(added_m.group(1))
                event.boxes_delta = self._parse_boxes_with_nominal(payload)
                total_m = self._RE_TOTAL_SUM.search(unescaped)
                if total_m:
                    event.total_cents = int(total_m.group(1)) * 100
                event.raw_details = payload[:500]

        # ===== WITHDRAW =====
        elif event_type == W2CashEvent.TYPE_WITHDRAW:
            disp_m = self._RE_TAKEN_DISP.search(unescaped)
            if disp_m:
                payload = self._unescape_placeholder(disp_m.group(1))
                # В withdraw boxes_delta ОТРИЦАТЕЛЬНЫЕ (ушло из кассет)
                raw_boxes = self._parse_boxes_with_nominal(payload)
                event.boxes_delta = {b: -c for b, c in raw_boxes.items()}
                event.raw_details = f"Dispenser: {payload[:300]}"

            reject_m = self._RE_TAKEN_REJECT.search(unescaped)
            if reject_m:
                payload = self._unescape_placeholder(reject_m.group(1))
                event.reject_delta = self._parse_notes_by_nominal(payload)
                if event.raw_details:
                    event.raw_details += f" | Reject: {payload[:200]}"
                else:
                    event.raw_details = f"Reject: {payload[:300]}"

            total_m = self._RE_TOTAL_SUM.search(unescaped)
            if total_m:
                event.total_cents = int(total_m.group(1)) * 100

        # ===== COLLECTION =====
        elif event_type == W2CashEvent.TYPE_COLLECTION:
            out_m = self._RE_OUT_BY_NOTES.search(unescaped)
            if out_m:
                payload = self._unescape_placeholder(out_m.group(1))
                # В COLLECTION нет box_number — только по номиналам
                event.raw_by_nominal = self._parse_notes_by_nominal(payload)
                event.raw_details = f"OUT: {payload[:300]}"

            total_m = self._RE_TOTAL_OUT.search(unescaped)
            if total_m:
                event.total_cents = int(total_m.group(1)) * 100

        # Сохраняем событие, только если удалось распарсить хоть что-то полезное
        if (event.boxes_delta or event.reject_delta
                or event.raw_by_nominal or event.total_cents):
            self.cash_events.append(event)
            self._seen_cash_events.add(dedup_key)
            self.lines_matched += 1

    def get_last_cash_event(self, before_dt: datetime,
                            event_type: str | None = None):
        """Возвращает последнее cash-событие ДО before_dt.

        Args:
            before_dt: искать события раньше этого момента.
            event_type: если задан — только события этого типа.
                        Если None — любое из трёх.

        Returns:
            W2CashEvent или None.
        """
        candidates = [
            e for e in self.cash_events
            if e.dt and e.dt < before_dt
            and (event_type is None or e.event_type == event_type)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda e: e.dt)

    # ====================================================================
    # Финализация и статистика
    # ====================================================================

    def build_sessions(self):
        self.sessions = list(self.sessions_by_trx.values())
        for s in self.sessions:
            s.finalize_status()
        self.sessions.sort(key=lambda s: s.start_dt or datetime.min)
        # Сортируем cash_events для корректной работы детектора расхождений
        # и статистики «между инкассациями» (Этапы 3 и 4).
        self.cash_events.sort(key=lambda e: e.dt or datetime.min)

    def get_stats(self):
        total = len(self.sessions)
        status_counts = defaultdict(int)
        denom_dispensed = defaultdict(int)
        boxes_by_device = defaultdict(
            lambda: defaultdict(lambda: {'dispensed': 0, 'rejected': 0})
        )
        error_reasons = defaultdict(int)
        hardware_reasons = defaultdict(int)
        jam_reasons = defaultdict(int)
        empty_reasons = defaultdict(int)
        timeout_reasons = defaultdict(int)
        coupon_reasons = defaultdict(int)
        total_requested = 0
        total_dispensed = 0
        suspicious_total = 0
        suspicion_reasons = defaultdict(int)
        for s in self.sessions:
            status_counts[s.status] += 1
            total_requested += s.requested_amount
            total_dispensed += s.dispensed_amount
            if s.error_detail:
                error_reasons[s.error_detail] += 1
                if s.status == 'FAILED_HARDWARE':
                    hardware_reasons[s.error_detail] += 1
                elif s.status == 'FAILED_JAM':
                    jam_reasons[s.error_detail] += 1
                elif s.status == 'FAILED_EMPTY':
                    empty_reasons[s.error_detail] += 1
                elif s.status == 'FAILED_TIMEOUT':
                    timeout_reasons[s.error_detail] += 1
            if s.status == 'FAILED_COUPON' and s.payout_coupon_status:
                coupon_reasons[s.payout_coupon_status] += 1
            for b in s.boxes:
                dev = s.dispenser_device_id or '?'
                key = (b['box'], b['nominal'])
                if b['is_rejected'] == 2:
                    boxes_by_device[dev][key]['dispensed'] += b['count']
                    denom_dispensed[b['nominal']] += b['count']
                elif b['is_rejected'] == 1:
                    boxes_by_device[dev][key]['rejected'] += b['count']
            if s.has_suspicious_match():
                suspicious_total += 1
                for flags in s.suspicion_flags:
                    for flag in flags:
                        norm = re.sub(r'[+-]?\d+s', 'Ns', flag)
                        norm = re.sub(r'bank \d+(,\d+)*', 'bank N', norm)
                        norm = re.sub(r'vs \[[^\]]+\]', 'vs [...]', norm)
                        suspicion_reasons[norm] += 1
        return {
            'total': total, 'status_counts': dict(status_counts),
            'denom_dispensed': dict(denom_dispensed),
            'boxes_by_device': {k: dict(v) for k, v in boxes_by_device.items()},
            'error_reasons': dict(error_reasons),
            'hardware_reasons': dict(hardware_reasons),
            'jam_reasons': dict(jam_reasons),
            'empty_reasons': dict(empty_reasons),
            'timeout_reasons': dict(timeout_reasons),
            'coupon_reasons': dict(coupon_reasons),
            'total_requested_cents': total_requested,
            'total_dispensed_cents': total_dispensed,
            'dispensers': dict(self.dispensers),
            'lines_processed': self.lines_processed,
            'lines_matched': self.lines_matched,
            'suspicious_total': suspicious_total,
            'suspicion_reasons': dict(suspicion_reasons),
            # Новое в Этапе 2 — для будущей статистики
            'cash_events_count': len(self.cash_events),
            'cash_events_by_type': {
                W2CashEvent.TYPE_COLLECTION: sum(
                    1 for e in self.cash_events
                    if e.event_type == W2CashEvent.TYPE_COLLECTION
                ),
                W2CashEvent.TYPE_WITHDRAW: sum(
                    1 for e in self.cash_events
                    if e.event_type == W2CashEvent.TYPE_WITHDRAW
                ),
                W2CashEvent.TYPE_CASH_IN: sum(
                    1 for e in self.cash_events
                    if e.event_type == W2CashEvent.TYPE_CASH_IN
                ),
            },
        }