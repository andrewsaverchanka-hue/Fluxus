"""Модели данных: сессии банкнот и выдач W2."""

import re
from datetime import datetime

from core.constants import DENOM_MAP
from core.utils import classify_dispense_error, classify_lcdm_status


class BanknoteSession:
    """
    Жизненный цикл одной банкноты на купюроприёмнике (Lima):
    INSERTED → STACKING → STACKED/REJECTED/ERROR.
    """

    def __init__(self, session_id, first_event):
        self.session_id = session_id
        self.events = [first_event]
        self.start_ts = first_event['timestamp']
        self.first_date = first_event.get('date', '?')
        self.first_time = first_event.get('time', '?')
        self.uuid = (first_event.get('banknote_uuid')
                     or first_event.get('banknote_id') or 'N/A')
        self.status = 'INCOMPLETE'
        self.denomination = first_event.get('banknote_raw')
        self.currency = first_event.get('currency')
        self.device_model = first_event.get('device_model') or 'Unknown'
        self.device_serial = first_event.get('device_serial') or 'N/A'
        self.closed = False
        self.error_reason = None
        self._update_status(first_event)

    def add_event(self, event):
        self.events.append(event)
        if event.get('banknote_raw') and not self.denomination:
            self.denomination = event['banknote_raw']
        if event.get('currency') and not self.currency:
            self.currency = event['currency']
        if event.get('device_model') and self.device_model == 'Unknown':
            self.device_model = event['device_model']
        if event.get('device_serial') and self.device_serial == 'N/A':
            self.device_serial = event['device_serial']
        self._update_status(event)

    def _update_status(self, event):
        et = event['event_type']
        if event.get('is_bnv', False):
            return
        if 'STACKED' in et:
            self.status = 'STACKED'
            self.closed = True
        elif 'CREDIT' in et:
            self.status = 'STACKED'
            self.closed = True
        elif 'REJECTED' in et:
            self.status = 'REJECTED'
            self.closed = True
        elif self.status not in ('ERROR',):
            if 'STACKING' in et:
                self.status = 'STACKING'
            elif 'INSERTED' in et and self.status == 'INCOMPLETE':
                self.status = 'INSERTED'

    def mark_error(self, bnv_event_name):
        if not self.closed:
            self.status = 'ERROR'
            self.error_reason = bnv_event_name

    def get_display_denom(self):
        if self.denomination:
            return DENOM_MAP.get(self.denomination, self.denomination / 100)
        return 0


class W2CashEvent:
    """Событие изменения баланса кассет W2.

    Три типа:
      - TYPE_COLLECTION    — полная инкассация (из кассет И reject-бокса).
                              Обычно идёт в паре с WITHDRAW непосредственно до неё.
      - TYPE_WITHDRAW      — промежуточное изъятие (из кассет и/или reject-бокса).
                              Используется и при инкассации, и как отдельное
                              изъятие в ходе обслуживания.
      - TYPE_CASH_IN       — пополнение (загрузили купюры в кассеты).
                              После него snapshot кассет растёт.

    Все три события парсятся из frontend-лога по полю rawTemplate внутри
    printSlip.placeholders или из devices_server-лога по строке Request: Print.
    """

    TYPE_COLLECTION = 'collection'
    TYPE_WITHDRAW = 'withdraw_report'
    TYPE_CASH_IN = 'cash_in'

    def __init__(self, event_type: str, dt: datetime):
        self.event_type = event_type
        self.dt = dt
        self.timestamp = dt.timestamp() if dt else 0.0

        # Изменение по кассетам: {box_number: delta_count}
        # Положительное для CASH_IN, отрицательное для COLLECTION/WITHDRAW.
        # Если box не определён (например, в COLLECTION outByNotes без box_num) —
        # используется ключ -1 с номиналом в raw_by_nominal.
        self.boxes_delta: dict[int, int] = {}

        # Для COLLECTION/WITHDRAW: сколько купюр УШЛО из reject-бокса,
        # сгруппированных по номиналу: {nominal: count}.
        # Например {5: 1, 20: 215} = забрали 1 шт $5 и 215 шт $20.
        self.reject_delta: dict[int, int] = {}

        # Сырые данные по номиналам (на случай если box не определён):
        # {nominal: count}. Используется для COLLECTION, где outByNotes
        # не содержит box_number — только общую разбивку по номиналам.
        self.raw_by_nominal: dict[int, int] = {}

        # Общая сумма в центах
        self.total_cents: int = 0

        # Сырые детали для вывода в UI
        self.raw_details: str = ""

    def date_str(self) -> str:
        return self.dt.strftime("%d/%m/%Y") if self.dt else "?"

    def time_str(self) -> str:
        return self.dt.strftime("%H:%M:%S") if self.dt else "?"

    def type_display(self) -> str:
        """Человекочитаемое название типа события."""
        return {
            self.TYPE_COLLECTION: 'Инкассация',
            self.TYPE_WITHDRAW: 'Изъятие (Withdraw)',
            self.TYPE_CASH_IN: 'Пополнение (Cash in)',
        }.get(self.event_type, self.event_type)

    def is_outflow(self) -> bool:
        """True если событие забирает деньги из кассет (COLLECTION/WITHDRAW)."""
        return self.event_type in (self.TYPE_COLLECTION, self.TYPE_WITHDRAW)

    def is_inflow(self) -> bool:
        """True если событие добавляет деньги в кассеты (CASH_IN)."""
        return self.event_type == self.TYPE_CASH_IN


class W2WithdrawSession:
    """
    Операция выдачи наличных на W2-терминале с точки зрения фронтенда.
    Собирается из skills-battle-frontend.log — по полю trx.
    После парсинга склеивается с W2LowLevelSession (реальная работа диспенсера).
    """

    def __init__(self, trx):
        self.trx = trx
        self.events = []
        self.start_dt = None
        self.end_dt = None
        self.start_date_str = '?'
        self.start_time_str = '?'
        self.coupon = None
        self.user_login = None
        self.group_uuid = None
        self.balance_id = None
        self.transaction_id = None
        self.requested_amount = 0
        self.dispensed_amount = 0
        self.commands = []
        self.dispensed_events = []
        self.boxes = []
        self.cassette_snapshot = []
        self.status = 'INCOMPLETE'
        self.error_detail = None
        self.retry_count = 0
        self.payout_coupon_status = None
        self.recovery_coupon = None
        self.receipt_printed = False
        self.dispenser_device_id = None
        self.dispenser_model = None
        self.dispenser_vendor = None
        self.lowlevel_sessions = []
        self.suspicion_flags = []

        # ===== Поля для будущего детектора расхождений кассет (Этап 3) =====
        # Расхождение между ожидаемым и реальным snapshot'ом кассет
        self.cassette_mismatch: bool = False
        # Детали расхождения: {box_number: (expected, actual, delta)}
        self.cassette_mismatch_details: dict = {}
        # Это «коллективное» расхождение (несколько выдач между snapshot'ами)?
        self.cassette_mismatch_collective: bool = False
        self.cassette_mismatch_group_size: int = 1

    def add_event(self, event):
        self.events.append(event)
        if self.start_dt is None or event['dt'] < self.start_dt:
            self.start_dt = event['dt']
            self.start_date_str = event['date']
            self.start_time_str = event['time']
        if self.end_dt is None or event['dt'] > self.end_dt:
            self.end_dt = event['dt']

    def finalize_status(self):
        if self.payout_coupon_status and self.payout_coupon_status != 'ok':
            self.status = 'FAILED_COUPON'
            return
        if (self.dispensed_amount > 0
                and self.dispensed_amount >= self.requested_amount):
            self.status = 'OK'
            return
        if 0 < self.dispensed_amount < self.requested_amount:
            self.status = 'PARTIAL'
            return
        for de in self.dispensed_events:
            if de.get('statusDetail'):
                cls = classify_dispense_error(de['statusDetail'])
                self.status = cls
                self.error_detail = de['statusDetail']
                return
        if any(e.get('kind') in ('cannotContinue', 'errorOccurs')
               for e in self.events):
            self.status = 'FAILED_HARDWARE'
            return
        self.status = 'INCOMPLETE'

    def duration_sec(self):
        if self.start_dt and self.end_dt:
            return (self.end_dt - self.start_dt).total_seconds()
        return 0

    def has_suspicious_match(self):
        """Есть ли хоть одна подозрительная LL-привязка в склейке."""
        return any(bool(flags) for flags in self.suspicion_flags)

    def suspicious_count(self):
        return sum(1 for flags in self.suspicion_flags if flags)

    def all_suspicion_flags(self):
        all_flags = []
        for flags in self.suspicion_flags:
            all_flags.extend(flags)
        return all_flags


class W2LowLevelSession:
    """
    Низкоуровневая сессия работы диспенсера LCDM2000/LCDM4000.
    """

    def __init__(self):
        self.model = None
        self.req_id = None
        self.job_id = None
        self.idx = None
        self.serial_path = None
        self.start_dt = None
        self.end_dt = None
        self.start_date_str = '?'
        self.start_time_str = '?'
        self.commands = []
        self.results = []
        self.status = 'INCOMPLETE'
        self.error_detail = None
        self.lcdm_status_code = None
        self.lcdm_device_code = None
        self.events = []
        self.closed = False

    def add_event(self, kind, dt, raw, **payload):
        ev = {'kind': kind, 'dt': dt, 'raw': raw}
        ev.update(payload)
        self.events.append(ev)
        if self.start_dt is None or dt < self.start_dt:
            self.start_dt = dt
            self.start_date_str = dt.strftime("%d/%m/%Y")
            self.start_time_str = dt.strftime("%H:%M:%S")
        if self.end_dt is None or dt > self.end_dt:
            self.end_dt = dt

    def total_requested(self):
        return sum(c.get('amount', 0) for c in self.commands)

    def total_dispensed(self):
        return sum(r.get('dispensed', 0) for r in self.results)

    def total_rejected(self):
        return sum(r.get('rejected', 0) for r in self.results)

    def finalize_status(self):
        if not self.results:
            if self.commands:
                self.status = 'INCOMPLETE'
            return
        error_results = [
            r for r in self.results
            if r.get('error') or (
                r.get('status_code') not in (None, 0x30, 0x31)
                if 'status_code' in r else False
            )
        ]
        if error_results:
            r = error_results[0]
            if r.get('status_code') is not None:
                desc, category = classify_lcdm_status(r['status_code'])
                self.status = category
                self.error_detail = desc
                self.lcdm_status_code = r['status_code']
            elif r.get('error'):
                self.error_detail = r['error']
                self.status = classify_dispense_error(r['error'])
                if r.get('device_code') is not None:
                    self.lcdm_device_code = r['device_code']
            return
        req = self.total_requested()
        disp = self.total_dispensed()
        if disp >= req and req > 0:
            self.status = 'OK'
        elif disp > 0:
            self.status = 'PARTIAL'
        else:
            self.status = 'INCOMPLETE'

    def duration_sec(self):
        if self.start_dt and self.end_dt:
            return (self.end_dt - self.start_dt).total_seconds()
        return 0